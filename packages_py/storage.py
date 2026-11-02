"""PostgreSQL 仓储层，负责业务数据持久化及所属用户检查。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from packages_py.config import Settings
from packages_py.schemas import (
    AnalysisCandidate,
    AnalysisResult,
    AnalysisSession,
    ImprovementConsent,
    InputVersion,
    RunRecord,
)


class StorageError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def now() -> datetime:
    return datetime.now(timezone.utc)


def digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PostgresStorage:
    """管理会话、运行、模型产物和删除任务，并检查数据所属用户。"""
    def __init__(self, settings: Settings):
        if not settings.database_url.startswith("postgresql"):
            raise StorageError("persistence_failed")
        self.engine = sa.create_engine(settings.database_url, pool_pre_ping=True)
        self.settings = settings
        self.asset_root = settings.asset_storage_root
        if self.asset_root is not None:
            self.asset_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.schema = settings.database_schema
        metadata = sa.MetaData(schema=self.schema)
        metadata.reflect(self.engine)
        self.tables = {name.split(".")[-1]: table for name, table in metadata.tables.items()}

    def close(self) -> None:
        self.engine.dispose()

    @contextmanager
    def execution_lock(self, session_id: UUID):
        """工作流执行期间持有会话级咨询锁，即使发生异常也释放。"""
        key = int.from_bytes(hashlib.sha256(session_id.bytes).digest()[:8], "big", signed=True)
        with self.engine.connect() as conn:
            conn.execute(sa.text("SELECT pg_advisory_lock(:key)"), {"key": key})
            # 咨询锁绑定数据库连接，提交事务后仍然有效；立即提交可避免工作流
            # 执行期间保留 SQLAlchemy 隐式事务，阻塞其他连接上的并发 DDL。
            conn.commit()
            try:
                yield
            finally:
                conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                conn.commit()

    def _table(self, name: str):
        return self.tables[name]

    def create_subject(self, ttl_seconds: int) -> tuple[UUID, str]:
        if ttl_seconds <= 0:
            raise StorageError("invalid_request")
        self._validate_retention()
        subject, token = uuid4(), secrets.token_urlsafe(32)
        expiry = now() + timedelta(seconds=ttl_seconds)
        with self.engine.begin() as conn:
            conn.execute(
                self._table("subjects").insert().values(subject_id=subject, expires_at=expiry)
            )
            conn.execute(
                self._table("access_tokens")
                .insert()
                .values(
                    token_digest=hashlib.sha256(token.encode()).hexdigest(),
                    subject_id=subject,
                    expires_at=expiry,
                )
            )
        return subject, token

    def authenticate(self, token: str) -> UUID:
        table = self._table("access_tokens")
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(table).where(
                        table.c.token_digest == hashlib.sha256(token.encode()).hexdigest(),
                        table.c.expires_at > now(),
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise StorageError("unauthorized")
        return row["subject_id"]

    def _session(self, conn, subject_id: UUID, session_id: UUID, *, deleted=False, lock=False):
        table = self._table("analysis_sessions")
        statement = sa.select(table).where(
            table.c.session_id == session_id,
            table.c.subject_id == subject_id,
            table.c.expires_at > now(),
        )
        if not deleted:
            statement = statement.where(table.c.deleted_at.is_(None))
        if lock:
            statement = statement.with_for_update()
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise StorageError("not_found")
        return row

    def create_session(self, subject_id: UUID, ttl_seconds: int) -> AnalysisSession:
        self._validate_retention()
        session = AnalysisSession(session_id=uuid4(), subject_id=subject_id)
        with self.engine.begin() as conn:
            expiry = conn.execute(
                sa.select(self._table("subjects").c.expires_at).where(
                    self._table("subjects").c.subject_id == subject_id
                )
            ).scalar_one_or_none()
            if expiry is None or expiry <= now() or ttl_seconds <= 0:
                raise StorageError("unauthorized")
            conn.execute(
                self._table("analysis_sessions")
                .insert()
                .values(
                    **session.model_dump(exclude={"allowed_source_ids"}),
                    allowed_source_ids=[],
                    expires_at=min(expiry, now() + timedelta(seconds=ttl_seconds)),
                )
            )
        return session

    def get_session(self, subject_id: UUID, session_id: UUID) -> AnalysisSession:
        with self.engine.connect() as conn:
            row = self._session(conn, subject_id, session_id)
            return AnalysisSession.model_validate(
                {key: row[key] for key in AnalysisSession.model_fields}
            )

    def append_input(self, subject_id: UUID, input_version: InputVersion) -> InputVersion:
        table = self._table("input_versions")
        with self.engine.begin() as conn:
            self._session(conn, subject_id, input_version.session_id, lock=True)
            conn.execute(
                table.insert().values(
                    input_version_id=input_version.input_version_id,
                    session_id=input_version.session_id,
                    payload=input_version.model_dump(mode="json"),
                    created_at=now(),
                )
            )
            sessions = self._table("analysis_sessions")
            conn.execute(
                sessions.update()
                .where(sessions.c.session_id == input_version.session_id)
                .values(input_version_id=input_version.input_version_id)
            )
        return input_version

    def get_input(self, subject_id: UUID, session_id: UUID, input_id: UUID) -> InputVersion:
        with self.engine.connect() as conn:
            self._session(conn, subject_id, session_id)
            table = self._table("input_versions")
            row = conn.execute(
                sa.select(table.c.payload).where(
                    table.c.session_id == session_id, table.c.input_version_id == input_id
                )
            ).scalar_one_or_none()
            if row is None:
                raise StorageError("not_found")
            return InputVersion.model_validate(row)

    def _idempotency(
        self, conn, subject_id, session_id, request_id, operation, payload, operation_id
    ):
        """将客户端请求标识绑定到操作类型及规范化载荷，防止重复请求产生冲突。"""
        table = self._table("idempotency_records")
        row = (
            conn.execute(
                sa.select(table).where(
                    table.c.subject_id == subject_id,
                    table.c.session_id == session_id,
                    table.c.request_id == request_id,
                )
            )
            .mappings()
            .first()
        )
        hashed = digest(payload)
        if row:
            if row["operation"] != operation or row["payload_hash"] != hashed:
                raise StorageError("idempotency_conflict")
            return row["operation_id"]
        conn.execute(
            table.insert().values(
                subject_id=subject_id,
                session_id=session_id,
                request_id=request_id,
                operation=operation,
                payload_hash=hashed,
                operation_id=operation_id,
            )
        )
        return None

    def create_run(
        self, subject_id: UUID, run: RunRecord, snapshot: dict, payload: dict
    ) -> RunRecord:
        """在同一事务内创建活动运行，并保存本次运行的配置快照。"""
        if "api_key" in json.dumps(snapshot).lower():
            raise StorageError("invalid_request")
        with self.engine.begin() as conn:
            session = self._session(conn, subject_id, run.session_id, lock=True)
            existing = self._idempotency(
                conn, subject_id, run.session_id, run.request_id, "analyze", payload, run.run_id
            )
            table = self._table("runs")
            if existing:
                record = conn.execute(
                    sa.select(table.c.record).where(table.c.run_id == existing)
                ).scalar_one()
                return RunRecord.model_validate(record)
            if session["input_version_id"] != run.input_version_id:
                raise StorageError("stale_version")
            input_row = conn.execute(
                sa.select(self._table("input_versions").c.payload).where(
                    self._table("input_versions").c.input_version_id == run.input_version_id
                )
            ).scalar_one()
            input_version = InputVersion.model_validate(input_row)
            if (
                input_version.confirmation == "pending"
                or not (input_version.confirmed_text or "").strip()
            ):
                raise StorageError("input_unconfirmed")
            if session["active_run_id"]:
                old = conn.execute(
                    sa.select(table.c.record).where(table.c.run_id == session["active_run_id"])
                ).scalar_one()
                if old["status"] in {"running", "waiting_clarification"}:
                    raise StorageError("stale_version")
                old["status"] = "superseded"
                conn.execute(
                    table.update()
                    .where(table.c.run_id == session["active_run_id"])
                    .values(status="superseded", record=old)
                )
            conn.execute(
                table.insert().values(
                    run_id=run.run_id,
                    session_id=run.session_id,
                    request_id=run.request_id,
                    payload_hash=digest(payload),
                    status=run.status,
                    record=run.model_dump(mode="json"),
                    config_snapshot=snapshot,
                    config_snapshot_schema_version=snapshot["config_snapshot_schema_version"],
                    created_at=now(),
                )
            )
            sessions = self._table("analysis_sessions")
            conn.execute(
                sessions.update()
                .where(sessions.c.session_id == run.session_id)
                .values(active_run_id=run.run_id)
            )
            conn.execute(
                self._table("thread_mappings")
                .insert()
                .values(thread_id=run.thread_id, session_id=run.session_id, run_id=run.run_id)
            )
        return run

    def get_run(self, subject_id: UUID, session_id: UUID, run_id: UUID) -> tuple[RunRecord, dict]:
        with self.engine.connect() as conn:
            self._session(conn, subject_id, session_id)
            table = self._table("runs")
            row = (
                conn.execute(
                    sa.select(table).where(
                        table.c.run_id == run_id, table.c.session_id == session_id
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise StorageError("not_found")
            return RunRecord.model_validate(row["record"]), row["config_snapshot"]

    def _active(self, conn, subject_id, result):
        session = self._session(conn, subject_id, result.session_id, lock=True)
        if (
            session["active_run_id"] != result.run_id
            or session["input_version_id"] != result.input_version_id
        ):
            raise StorageError("stale_version")
        return session

    def save_version(self, subject_id: UUID, result: AnalysisCandidate) -> UUID:
        with self.engine.begin() as conn:
            self._active(conn, subject_id, result)
            conn.execute(
                self._table("result_versions")
                .insert()
                .values(
                    result_version_id=result.result_version_id,
                    run_id=result.run_id,
                    session_id=result.session_id,
                    input_version_id=result.input_version_id,
                    payload=result.model_dump(mode="json"),
                    validated_at=None,
                )
            )
        return result.result_version_id

    def get_candidate(self, subject_id: UUID, result_id: UUID) -> AnalysisCandidate | None:
        table = self._table("result_versions")
        with self.engine.connect() as conn:
            row = (
                conn.execute(sa.select(table).where(table.c.result_version_id == result_id))
                .mappings()
                .first()
            )
            if not row:
                return None
            self._session(conn, subject_id, row["session_id"])
            payload = dict(row["payload"])
            payload.pop("status", None)
            return AnalysisCandidate.model_validate(payload)

    def publish_version(self, subject_id: UUID, result: AnalysisResult) -> None:
        """检查所属范围后，仅发布与已保存候选内容完全一致的结果。"""
        with self.engine.begin() as conn:
            self._active(conn, subject_id, result)
            table = self._table("result_versions")
            stored = (
                conn.execute(
                    sa.select(table)
                    .where(table.c.result_version_id == result.result_version_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            candidate = result.model_dump(mode="json", exclude={"status"})
            if not stored or stored["validated_at"] is not None or stored["payload"] != candidate:
                raise StorageError("persistence_failed")
            conn.execute(
                table.update()
                .where(table.c.result_version_id == result.result_version_id)
                .values(payload=result.model_dump(mode="json"), validated_at=now())
            )
            runs = self._table("runs")
            record = conn.execute(
                sa.select(runs.c.record).where(runs.c.run_id == result.run_id)
            ).scalar_one()
            if record["status"] != "running":
                raise StorageError("stale_version")
            status = (
                "waiting_clarification"
                if result.kind == "initial" and result.clarification_question
                else "completed"
            )
            record["status"] = status
            conn.execute(
                runs.update()
                .where(runs.c.run_id == result.run_id)
                .values(status=status, record=record)
            )

    def get_version(self, subject_id: UUID, result_id: UUID) -> AnalysisResult | None:
        table = self._table("result_versions")
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(table).where(
                        table.c.result_version_id == result_id, table.c.validated_at.is_not(None)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            self._session(conn, subject_id, row["session_id"])
            return AnalysisResult.model_validate(row["payload"])

    def latest_result(self, subject_id: UUID, session_id: UUID) -> AnalysisResult | None:
        with self.engine.connect() as conn:
            session = self._session(conn, subject_id, session_id)
            table = self._table("result_versions")
            row = conn.execute(
                sa.select(table.c.payload)
                .where(
                    table.c.session_id == session_id,
                    table.c.run_id == session["active_run_id"],
                    table.c.validated_at.is_not(None),
                )
                .order_by(table.c.validated_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            return AnalysisResult.model_validate(row) if row else None

    def set_consent(self, subject_id: UUID, consent: ImprovementConsent) -> ImprovementConsent:
        """替换既有授权并清理改进副本，避免继续沿用旧授权。"""
        with self.engine.begin() as conn:
            self._session(conn, subject_id, consent.session_id, lock=True)
            if consent.subject_id != subject_id:
                raise StorageError("not_found")
            for data_id in consent.data_version_ids:
                self._data_version(conn, consent.session_id, data_id)
            table = self._table("consents")
            existing = (
                conn.execute(
                    sa.select(table.c.payload).where(table.c.session_id == consent.session_id)
                )
                .scalars()
                .all()
            )
            for payload in existing:
                if payload["status"] == "granted":
                    payload["status"] = "revoked"
                    conn.execute(
                        table.update()
                        .where(table.c.consent_id == UUID(payload["consent_id"]))
                        .values(payload=payload)
                    )
            conn.execute(
                table.insert().values(
                    consent_id=consent.consent_id,
                    subject_id=subject_id,
                    session_id=consent.session_id,
                    payload=consent.model_dump(mode="json"),
                )
            )
            copies = self._table("improvement_copies")
            conn.execute(copies.delete().where(copies.c.session_id == consent.session_id))
        return consent

    def revoke_consent(
        self, subject_id: UUID, session_id: UUID, consent_id: UUID
    ) -> ImprovementConsent:
        with self.engine.begin() as conn:
            self._session(conn, subject_id, session_id, lock=True, deleted=True)
            table = self._table("consents")
            row = (
                conn.execute(
                    sa.select(table)
                    .where(
                        table.c.consent_id == consent_id,
                        table.c.subject_id == subject_id,
                        table.c.session_id == session_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise StorageError("not_found")
            consent = ImprovementConsent.model_validate(row["payload"])
            if consent.status == "granted":
                consent = consent.model_copy(update={"status": "revoked", "updated_at": now()})
                conn.execute(
                    table.update()
                    .where(table.c.consent_id == consent_id)
                    .values(payload=consent.model_dump(mode="json"))
                )
            conn.execute(
                self._table("improvement_copies")
                .delete()
                .where(self._table("improvement_copies").c.consent_id == consent_id)
            )
            return consent

    def add_improvement_copy(
        self, subject_id: UUID, consent_id: UUID, data_version_id: UUID, redacted_payload: dict
    ) -> UUID:
        with self.engine.begin() as conn:
            consents = self._table("consents")
            row = (
                conn.execute(
                    sa.select(consents).where(consents.c.consent_id == consent_id)
                )
                .mappings()
                .first()
            )
            if row is None or row["subject_id"] != subject_id:
                raise StorageError("not_found")
            self._session(conn, subject_id, row["session_id"], lock=True)
            row = conn.execute(sa.select(consents).where(
                consents.c.consent_id == consent_id).with_for_update()).mappings().first()
            if row is None:
                raise StorageError("not_found")
            consent = ImprovementConsent.model_validate(row["payload"])
            if consent.status != "granted" or data_version_id not in consent.data_version_ids:
                raise StorageError("unauthorized")
            self._data_version(conn, consent.session_id, data_version_id)
            copy_id = uuid4()
            conn.execute(
                self._table("improvement_copies")
                .insert()
                .values(
                    copy_id=copy_id,
                    consent_id=consent_id,
                    session_id=consent.session_id,
                    data_version_id=data_version_id,
                    payload=redacted_payload,
                )
            )
        return copy_id

    def delete_session(
        self, subject_id: UUID, session_id: UUID, request_id: UUID, receipt_ttl_seconds: int = 86400,
        request_payload: dict | None = None,
    ) -> dict:
        """先标记会话已删除并取消活动运行，再按范围执行可重试的清理。"""
        self._validate_retention()
        if receipt_ttl_seconds <= 0:
            raise StorageError("invalid_request")
        # Dependencies first: checkpoint needs thread mappings until it is cleaned.
        scopes = ["checkpoint", "events", "asset_storage", "improvement_copies", "service_data"]
        with self.engine.begin() as conn:
            self._session(conn, subject_id, session_id, lock=True, deleted=True)
            jobs = self._table("deletion_jobs")
            job = (
                conn.execute(
                    sa.select(jobs)
                    .where(jobs.c.subject_id == subject_id, jobs.c.session_id == session_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if job is None:
                deletion_id = uuid4()
                conn.execute(
                    jobs.insert().values(
                        deletion_id=deletion_id,
                        subject_id=subject_id,
                        session_id=session_id,
                        status="pending",
                        pending_scopes=scopes,
                        expires_at=now() + timedelta(seconds=receipt_ttl_seconds),
                    )
                )
            else:
                deletion_id = job["deletion_id"]
            operation_id = self._idempotency(
                conn,
                subject_id,
                session_id,
                request_id,
                "delete",
                request_payload if request_payload is not None else {"session_id": str(session_id)},
                deletion_id,
            )
            if operation_id:
                deletion_id = operation_id
            sessions = self._table("analysis_sessions")
            conn.execute(
                sessions.update()
                .where(sessions.c.session_id == session_id)
                .values(deleted_at=now())
            )
            runs = self._table("runs")
            active = conn.execute(
                sa.select(sessions.c.active_run_id).where(sessions.c.session_id == session_id)
            ).scalar_one()
            if active:
                record = conn.execute(
                    sa.select(runs.c.record).where(runs.c.run_id == active).with_for_update()
                ).scalar_one_or_none()
                if record and record["status"] in {"running", "waiting_clarification"}:
                    record["status"] = "cancelled"
                    conn.execute(
                        runs.update()
                        .where(runs.c.run_id == active)
                        .values(status="cancelled", record=record)
                    )
        with self.execution_lock(session_id):
            self._run_deletion(deletion_id)
        return self.deletion_status(subject_id, session_id)

    def _run_deletion(self, deletion_id: UUID) -> None:
        """记录每次清理尝试；失败时保留待清理范围，供后续重试。"""
        jobs = self._table("deletion_jobs")
        attempts = self._table("deletion_attempts")
        with self.engine.begin() as conn:
            job = (
                conn.execute(
                    sa.select(jobs).where(jobs.c.deletion_id == deletion_id).with_for_update()
                )
                .mappings()
                .first()
            )
            if not job or job["status"] == "completed":
                return
            attempted = list(job["pending_scopes"])
            attempt_id = uuid4()
            conn.execute(
                attempts.insert().values(
                    attempt_id=attempt_id,
                    deletion_id=deletion_id,
                    started_at=now(),
                    attempted_scopes=attempted,
                    remaining_scopes=attempted,
                )
            )
            remaining = list(attempted)
            error = None
            for scope in attempted:
                if scope == "service_data" and "checkpoint" in remaining:
                    continue
                try:
                    with conn.begin_nested():
                        self._clean_scopes(conn, job, [scope])
                    remaining.remove(scope)
                except Exception:
                    error = "persistence_failed"
            conn.execute(
                jobs.update()
                .where(jobs.c.deletion_id == deletion_id)
                .values(
                    status="failed" if remaining else "completed",
                    pending_scopes=remaining,
                    error_code=error,
                )
            )
            conn.execute(
                attempts.update()
                .where(attempts.c.attempt_id == attempt_id)
                .values(finished_at=now(), remaining_scopes=remaining, error_code=error)
            )

    def _clean_scopes(self, conn, job, remaining):
        """按范围清理检查点、事件文件及业务表；外部清理不能随数据库事务回滚。"""
        session_id = job["session_id"]
        thread_ids = (
            conn.execute(
                sa.select(self._table("thread_mappings").c.thread_id).where(
                    self._table("thread_mappings").c.session_id == session_id
                )
            )
            .scalars()
            .all()
        )
        if "checkpoint" in remaining:
            from langgraph.checkpoint.postgres import PostgresSaver

            from packages_py.workflow_runtime import checkpoint_connection

            if thread_ids:
                with checkpoint_connection(
                    self.settings.checkpoint_url or self.settings.database_url,
                    self.settings.checkpoint_schema,
                ) as checkpoint_conn:
                    saver = PostgresSaver(checkpoint_conn)
                    for thread in thread_ids:
                        saver.delete_thread(thread)
            remaining.remove("checkpoint")
        if "events" in remaining:
            from packages_py.observability import EventWriter

            EventWriter(self.settings.events_path).purge_session(session_id)
            remaining.remove("events")
        if "asset_storage" in remaining:
            assets = self._table("assets")
            count = conn.execute(sa.select(sa.func.count()).select_from(assets).where(
                assets.c.session_id == session_id
            )).scalar_one()
            if count:
                if self.asset_root is None:
                    raise StorageError("asset_storage_unavailable")
                for row in conn.execute(sa.select(assets).where(
                    assets.c.session_id == session_id)).mappings():
                    self._asset_path(row["object_key"]).unlink(missing_ok=True)
                conn.execute(assets.delete().where(assets.c.session_id == session_id))
            remaining.remove("asset_storage")
        for scope, names in {
            "improvement_copies": ("improvement_copies", "consents"),
            "service_data": (
                "workflow_artifacts",
                "result_versions",
                "input_versions",
                "thread_mappings",
            ),
        }.items():
            if scope in remaining:
                for name in names:
                    table = self._table(name)
                    conn.execute(table.delete().where(table.c.session_id == session_id))
                remaining.remove(scope)

    def retry_deletion(self, subject_id: UUID, session_id: UUID) -> dict:
        with self.engine.connect() as conn:
            jobs = self._table("deletion_jobs")
            row = (
                conn.execute(
                    sa.select(jobs).where(
                        jobs.c.subject_id == subject_id,
                        jobs.c.session_id == session_id,
                        jobs.c.expires_at > now(),
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise StorageError("not_found")
        if row["status"] == "failed":
            with self.execution_lock(session_id):
                self._run_deletion(row["deletion_id"])
        return self.deletion_status(subject_id, session_id)

    def deletion_status(self, subject_id: UUID, session_id: UUID) -> dict:
        table = self._table("deletion_jobs")
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(table).where(
                        table.c.subject_id == subject_id,
                        table.c.session_id == session_id,
                        table.c.expires_at > now(),
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise StorageError("not_found")
        return {
            "deletion_id": str(row["deletion_id"]),
            "session_id": str(session_id),
            "status": row["status"],
            "pending_scopes": row["pending_scopes"],
            "error_code": row["error_code"],
        }

    def update_run_status(
        self,
        subject_id: UUID,
        session_id: UUID,
        run_id: UUID,
        status: str,
        error_code: str | None = None,
    ) -> RunRecord:
        with self.engine.begin() as conn:
            session = self._session(conn, subject_id, session_id, lock=True)
            if session["active_run_id"] != run_id:
                raise StorageError("stale_version")
            table = self._table("runs")
            row = (
                conn.execute(sa.select(table).where(table.c.run_id == run_id).with_for_update())
                .mappings()
                .first()
            )
            if row is None:
                raise StorageError("not_found")
            record = dict(row["record"])
            record["status"], record["error_code"] = status, error_code
            conn.execute(
                table.update().where(table.c.run_id == run_id).values(status=status, record=record)
            )
            return RunRecord.model_validate(record)

    def execute_tool_once(
        self, key: str, operation, reconcile=None, payload_hash: str | None = None
    ):
        """对有副作用的调用持久化去重；超时后无法确认执行结果时返回未知状态。"""
        from packages_py.tool_protocol import ToolResult

        key_hash = hashlib.sha256(key.encode()).hexdigest()
        lock_key = int.from_bytes(bytes.fromhex(key_hash[:16]), "big", signed=True)
        with self.engine.connect() as conn:
            conn.execute(sa.text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
            conn.commit()
            try:
                table = self._table("tool_idempotency")
                with conn.begin():
                    row = (
                        conn.execute(
                            sa.select(table).where(table.c.key == key_hash).with_for_update()
                        )
                        .mappings()
                        .first()
                    )
                if row:
                    # 相同幂等键对应不同参数时必须拒绝，避免错误复用旧结果。
                    if row.get("payload_hash") != payload_hash:
                        return ToolResult(
                            tool_name=key.split(":", 1)[0],
                            ok=False,
                            error_code="tool_idempotency_conflict",
                        ).validate_shape()
                    if row["status"] == "completed" and row["output"] is not None:
                        return ToolResult.model_validate(row["output"]).validate_shape()
                    if reconcile is not None:
                        # 通过对账回调确认外部操作是否已完成，避免盲目重复执行副作用。
                        result = reconcile()
                        if result is not None:
                            result.validate_shape()
                            with conn.begin():
                                conn.execute(
                                    table.update()
                                    .where(table.c.key == key_hash)
                                    .values(
                                        status="completed", output=result.model_dump(mode="json")
                                    )
                                )
                            return result
                    return ToolResult(
                        tool_name=key.split(":", 1)[0], ok=False, error_code="tool_outcome_unknown"
                    ).validate_shape()
                with conn.begin():
                    conn.execute(
                        table.insert().values(
                            key=key_hash,
                            payload_hash=payload_hash,
                            status="running",
                            created_at=now(),
                        )
                    )
                try:
                    result = operation().validate_shape()
                except Exception:
                    with conn.begin():
                        conn.execute(
                            table.update().where(table.c.key == key_hash).values(status="unknown")
                        )
                    return ToolResult(
                        tool_name=key.split(":", 1)[0], ok=False, error_code="tool_outcome_unknown"
                    ).validate_shape()
                with conn.begin():
                    conn.execute(
                        table.update()
                        .where(table.c.key == key_hash)
                        .values(
                            status="unknown"
                            if result.error_code == "tool_timeout"
                            else "completed",
                            output=result.model_dump(mode="json"),
                        )
                    )
                return result
            finally:
                conn.commit()
                conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
                conn.commit()

    def load_workflow_input(
        self, session_id: str, input_id: str, *, subject_id: UUID | None = None
    ) -> InputVersion:
        """Load workflow input only through the subject-bound repository facade."""
        if subject_id is None:
            raise StorageError("unauthorized")
        table = self._table("input_versions")
        with self.engine.connect() as conn:
            self._session(conn, subject_id, UUID(session_id))
            row = conn.execute(
                sa.select(table.c.payload).where(
                    table.c.session_id == UUID(session_id),
                    table.c.input_version_id == UUID(input_id),
                )
            ).scalar_one_or_none()
        if row is None:
            raise StorageError("not_found")
        item = InputVersion.model_validate(row)
        if (
            item.confirmation not in {"confirmed", "corrected"}
            or not (item.confirmed_text or "").strip()
        ):
            raise StorageError("input_unconfirmed")
        return item

    def store_workflow_artifact(
        self, run_id: str, session_id: str, kind: str, payload: dict, *, subject_id=None
    ) -> str:
        if subject_id is None:
            raise StorageError("not_found")
        artifact_id = uuid4()
        with self.engine.begin() as conn:
            self._session(conn, subject_id, UUID(session_id), lock=True)
            sessions = self._table("analysis_sessions")
            active = (
                conn.execute(
                    sa.select(sessions)
                    .where(
                        sessions.c.session_id == UUID(session_id),
                        sessions.c.deleted_at.is_(None),
                        sessions.c.active_run_id == UUID(run_id),
                        sessions.c.expires_at > now(),
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if active is None:
                raise StorageError("not_found")
            conn.execute(
                self._table("workflow_artifacts")
                .insert()
                .values(
                    artifact_id=artifact_id,
                    run_id=UUID(run_id),
                    session_id=UUID(session_id),
                    kind=kind,
                    payload=payload,
                    created_at=now(),
                )
            )
        return str(artifact_id)

    def get_workflow_artifact(self, subject_id: UUID, artifact_id: str, expected_kind: str) -> dict:
        table = self._table("workflow_artifacts")
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(table).where(
                        table.c.artifact_id == UUID(artifact_id), table.c.kind == expected_kind
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise StorageError("not_found")
            self._session(conn, subject_id, row["session_id"])
            return row["payload"]

    def register_asset(self, subject_id: UUID, session_id: UUID, asset_id: UUID,
                       object_key: str, modality: str, sha256: str,
                       expires_at: datetime) -> UUID:
        if expires_at <= now() or not object_key or len(sha256) != 64:
            raise StorageError("invalid_request")
        self._validate_retention()
        with self.engine.begin() as conn:
            session = self._session(conn, subject_id, session_id, lock=True)
            if expires_at > session["expires_at"]:
                raise StorageError("invalid_request")
            conn.execute(self._table("assets").insert().values(
                asset_id=asset_id, subject_id=subject_id, session_id=session_id,
                object_key=object_key, modality=modality, sha256=sha256,
                expires_at=expires_at))
        return asset_id

    def get_asset(self, subject_id: UUID, session_id: UUID, asset_id: UUID) -> dict:
        table = self._table("assets")
        with self.engine.connect() as conn:
            self._session(conn, subject_id, session_id)
            row = conn.execute(sa.select(table).where(
                table.c.asset_id == asset_id, table.c.subject_id == subject_id,
                table.c.session_id == session_id, table.c.expires_at > now()
            )).mappings().first()
        if row is None:
            raise StorageError("not_found")
        return dict(row)

    def _asset_path(self, object_key: str) -> Path:
        if self.asset_root is None:
            raise StorageError("asset_storage_unavailable")
        key = Path(object_key)
        if key.is_absolute() or ".." in key.parts:
            raise StorageError("persistence_failed")
        root = self.asset_root.resolve()
        path = (root / key).resolve()
        if root not in path.parents:
            raise StorageError("persistence_failed")
        return path

    def create_asset(self, subject_id: UUID, session_id: UUID, asset_id: UUID,
                     modality: str, content: bytes, expires_at: datetime) -> UUID:
        if self.asset_root is None:
            raise StorageError("asset_storage_unavailable")
        self._validate_retention()
        key = f"{subject_id}/{session_id}/{asset_id}"
        path = self._asset_path(key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            raise StorageError("idempotency_conflict")
        fd, temp_name = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
        published = False
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                with self.engine.begin() as conn:
                    session = self._session(conn, subject_id, session_id, lock=True)
                    if expires_at <= now() or expires_at > session["expires_at"]:
                        raise StorageError("invalid_request")
                    os.link(temp_name, path)
                    published = True
                    os.unlink(temp_name)
                    conn.execute(self._table("assets").insert().values(
                        asset_id=asset_id, subject_id=subject_id, session_id=session_id,
                        object_key=key, modality=modality,
                        sha256=hashlib.sha256(content).hexdigest(), expires_at=expires_at))
            except Exception:
                if published:
                    path.unlink(missing_ok=True)
                raise
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return asset_id

    def read_asset(self, subject_id: UUID, session_id: UUID, asset_id: UUID) -> bytes:
        metadata = self.get_asset(subject_id, session_id, asset_id)
        path = self._asset_path(metadata["object_key"])
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise StorageError("not_found") from exc
        if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
            raise StorageError("persistence_failed")
        return content

    def _data_version(self, conn, session_id: UUID, version_id: UUID) -> None:
        inputs = self._table("input_versions")
        results = self._table("result_versions")
        found = conn.execute(sa.select(inputs.c.input_version_id).where(
            inputs.c.session_id == session_id,
            inputs.c.input_version_id == version_id).union_all(
            sa.select(results.c.result_version_id).where(
                results.c.session_id == session_id,
                results.c.result_version_id == version_id)).limit(1)).scalar_one_or_none()
        if found is None:
            raise StorageError("not_found")

    def retrieve_references(self, subject_id: UUID, session_id: UUID, query: str,
                            source_ids: list[UUID], limit: int = 5):
        from packages_py.schemas import RetrievedSource
        self.get_session(subject_id, session_id)
        if not self.settings.reference_retrieval_enabled:
            return []
        if not query.strip() or not 1 <= limit <= 20:
            raise StorageError("invalid_request")
        with self.engine.connect() as conn:
            session = self._session(conn, subject_id, session_id)
            permitted = set(session["allowed_source_ids"] or []) & set(source_ids)
            if not permitted:
                return []
            sources, versions = self._table("sources"), self._table("source_versions")
            rows = conn.execute(sa.select(sources, versions).join(
                versions, versions.c.source_id == sources.c.source_id
            ).where(sources.c.source_id.in_(permitted),
                    versions.c.license_status == "approved",
                    versions.c.review_status == "approved")).mappings().all()
        terms = [part.casefold() for part in query.split() if part]
        ranked = []
        for row in rows:
            full = row["full_text"]
            lower = full.casefold()
            score = sum(lower.count(term) for term in terms)
            if not score:
                continue
            pos = next((i for i in range(len(full))
                        if any(full[i:].casefold().startswith(term) for term in terms)), 0)
            start, end = max(0, pos - 160), min(len(full), pos + 320)
            excerpt = full[start:end]
            ranked.append((score, RetrievedSource(
                source_id=row["source_id"], source_version_id=row["source_version_id"],
                source_type=row["source_type"], title=row["title"], excerpt=excerpt,
                locator=f"{row['locator'] or ''}#chars={start}:{end}",
                evidence_tier="reviewed", source_url=None)))
        return [item for _, item in sorted(ranked, key=lambda pair: -pair[0])[:limit]]

    def approved_reference_versions(
        self, subject_id: UUID, session_id: UUID, source_version_ids: set[UUID]
    ) -> dict[UUID, tuple[UUID, str]]:
        """Return only session-authorized, doubly approved full source versions."""
        if not source_version_ids:
            return {}
        with self.engine.connect() as conn:
            session = self._session(conn, subject_id, session_id)
            sources, versions = self._table("sources"), self._table("source_versions")
            rows = conn.execute(sa.select(versions.c.source_version_id, versions.c.source_id,
                                          versions.c.full_text).join(
                sources, sources.c.source_id == versions.c.source_id
            ).where(
                versions.c.source_version_id.in_(source_version_ids),
                versions.c.license_status == "approved",
                versions.c.review_status == "approved",
                versions.c.source_id.in_(session["allowed_source_ids"] or []),
            )).mappings().all()
        return {row["source_version_id"]: (row["source_id"], row["full_text"]) for row in rows}

    def restore_replaying_deletions(self, replay_deletions, open_content):
        """Restore gate: content remains unavailable until tombstones replay successfully."""
        replay_deletions()
        open_content()

    def replay_deletion_tombstones(self, content_opener) -> None:
        """Replay this database's deletion markers before enabling a restored content store."""
        sessions = self._table("analysis_sessions")
        with self.engine.connect() as conn:
            tombstones = conn.execute(sa.select(
                sessions.c.subject_id, sessions.c.session_id, sessions.c.deleted_at
            ).where(sessions.c.deleted_at.is_not(None))).mappings().all()
        for marker in tombstones:
            with self.engine.connect() as conn:
                job = conn.execute(sa.select(self._table("deletion_jobs")).where(
                    self._table("deletion_jobs").c.subject_id == marker["subject_id"],
                    self._table("deletion_jobs").c.session_id == marker["session_id"],
                )).mappings().first()
            if job is None:
                raise StorageError("persistence_failed")
            if job["status"] != "completed":
                with self.execution_lock(marker["session_id"]):
                    self._run_deletion(job["deletion_id"])
                with self.engine.connect() as conn:
                    status = conn.execute(sa.select(self._table("deletion_jobs").c.status).where(
                        self._table("deletion_jobs").c.deletion_id == job["deletion_id"]
                    )).scalar_one()
                if status != "completed":
                    raise StorageError("persistence_failed")
        content_opener()

    def _validate_retention(self) -> None:
        # Retention is an explicit deployment policy. Legacy development settings
        # may omit it; once any TTL is configured, the complete bounded policy is required.
        values = (self.settings.service_ttl_seconds, self.settings.token_ttl_seconds,
                  self.settings.asset_ttl_seconds, self.settings.checkpoint_ttl_seconds,
                  self.settings.deletion_receipt_ttl_seconds, self.settings.backup_ttl_seconds)
        if any(value is not None for value in values):
            try:
                self.settings.validate_retention()
            except ValueError as exc:
                raise StorageError("invalid_retention_policy") from exc

    def validate_understanding(self, understanding, context) -> None:
        from packages_py.schemas import AnalysisCandidate

        candidate = AnalysisCandidate(
            session_id=context.session_id,
            run_id=context.run_id,
            input_version_id=context.input_version_id,
            kind="final",
            claims=understanding.claims,
            evidence_refs=understanding.evidence_refs,
        )
        from packages_py.schemas import validate_result

        validate_result(candidate, context.text)

    def validate_candidate(self, candidate: AnalysisCandidate, context, subject_id=None) -> None:
        from packages_py.schemas import validate_result

        if subject_id is None:
            raise StorageError("unauthorized")

        source_version_ids = {
            ref.source_version_id
            for ref in candidate.evidence_refs
            if ref.source_kind == "reviewed_reference" and ref.source_version_id is not None
        }
        references = self.approved_reference_versions(
            subject_id, context.session_id, source_version_ids
        )
        with self.engine.connect() as conn:
            session = self._session(conn, subject_id, context.session_id)
        validate_result(
            candidate,
            context.text,
            reference_versions=references,
            allowed_source_ids=set(session["allowed_source_ids"] or []),
        )

    @property
    def output_max_revisions(self) -> int:
        return 1

    def save_candidate(self, candidate: AnalysisCandidate) -> UUID:
        # 工作流仓储代理先绑定当前用户，再调用公开的存储接口。
        subject_id = getattr(self, "_workflow_subject_id", None)
        if subject_id is None:
            raise StorageError("unauthorized")
        return self.save_version(subject_id, candidate)

    def workflow_repository(self, subject_id: UUID):
        """为工作流构造绑定当前用户的仓储代理，统一产物与结果访问入口。"""
        storage = self

        class Repository:
            output_max_revisions = storage.output_max_revisions

            def emit_event(self, state, step, status, duration_ms=0, error_code=None):
                from packages_py.observability import EventWriter
                from packages_py.schemas import RunEvent

                run, _ = storage.get_run(
                    subject_id, UUID(state["session_id"]), UUID(state["run_id"])
                )
                values = run.model_dump(include=set(RunEvent.model_fields))
                values.update(
                    step=step, status=status, duration_ms=duration_ms, error_code=error_code
                )
                EventWriter(storage.settings.events_path).emit(RunEvent.model_validate(values))

            def __getattr__(self, name):
                return getattr(storage, name)

            def load_input(self, session_id, input_id):
                return storage.load_workflow_input(session_id, input_id, subject_id=subject_id)

            def store_artifact(self, state, kind, payload):
                return storage.store_workflow_artifact(
                    state["run_id"], state["session_id"], kind, payload, subject_id=subject_id
                )

            def load_artifact(self, state, artifact_id, kind):
                payload = storage.get_workflow_artifact(subject_id, artifact_id, kind)
                if kind == "understanding":
                    from packages_py.schemas import ContextUnderstanding

                    return ContextUnderstanding.model_validate(payload)
                return payload

            def save_candidate(self, candidate):
                return storage.save_version(subject_id, candidate)

            def validate_candidate(self, candidate, context):
                storage.validate_candidate(candidate, context, subject_id=subject_id)

            def get_candidate(self, result_id):
                return storage.get_candidate(subject_id, UUID(result_id))

            def finalize_candidate(self, candidate, state):
                input_version = storage.get_input(
                    subject_id, candidate.session_id, candidate.input_version_id
                )
                session = storage.get_session(subject_id, candidate.session_id)
                run, _ = storage.get_run(subject_id, candidate.session_id, candidate.run_id)
                from packages_py.schemas import validate_result_scope

                reference_ids = {
                    ref.source_version_id
                    for ref in candidate.evidence_refs
                    if ref.source_kind == "reviewed_reference" and ref.source_version_id is not None
                }
                references = storage.approved_reference_versions(
                    subject_id, candidate.session_id, reference_ids
                )
                validate_result_scope(
                    candidate, input_version, session, run, reference_versions=references
                )
                stored = storage.get_candidate(subject_id, candidate.result_version_id)
                if stored is None or stored.model_dump(mode="json") != candidate.model_dump(
                    mode="json"
                ):
                    raise StorageError("persistence_failed")
                from packages_py.schemas import AnalysisResult

                result = AnalysisResult.model_validate(
                    {**candidate.model_dump(), "status": "validated"}
                )
                storage.publish_version(subject_id, result)
                return result

        return Repository()

    def resume_run(
        self,
        subject_id: UUID,
        session_id: UUID,
        run_id: UUID,
        request,
        new_input: InputVersion | None = None,
        max_rounds: int = 2,
    ) -> RunRecord:
        """在同一事务内校验澄清回复、更新输入版本并恢复运行状态。"""
        with self.engine.begin() as conn:
            session = self._session(conn, subject_id, session_id, lock=True)
            payload = request.model_dump(mode="json")
            payload.update(
                {"run_id": str(run_id), "text": new_input.confirmed_text if new_input else None}
            )
            idem = (
                conn.execute(
                    sa.select(self._table("idempotency_records")).where(
                        self._table("idempotency_records").c.subject_id == subject_id,
                        self._table("idempotency_records").c.session_id == session_id,
                        self._table("idempotency_records").c.request_id == request.request_id,
                    )
                )
                .mappings()
                .first()
            )
            if idem:
                if idem["operation"] != "resume" or idem["payload_hash"] != digest(payload):
                    raise StorageError("idempotency_conflict")
                prior = conn.execute(
                    sa.select(self._table("runs").c.record).where(
                        self._table("runs").c.run_id == idem["operation_id"]
                    )
                ).scalar_one_or_none()
                if prior is None:
                    raise StorageError("persistence_failed")
                return RunRecord.model_validate(prior)
            if session["active_run_id"] != run_id:
                raise StorageError("stale_version")
            runs = self._table("runs")
            row = (
                conn.execute(sa.select(runs).where(runs.c.run_id == run_id).with_for_update())
                .mappings()
                .first()
            )
            if row is None:
                raise StorageError("not_found")
            record = RunRecord.model_validate(row["record"])
            if record.status != "waiting_clarification":
                raise StorageError("run_not_waiting")
            if record.clarification_rounds >= max_rounds:
                raise StorageError("invalid_request")
            if request.skip:
                if new_input is not None or hasattr(request, "input_version_id"):
                    raise StorageError("invalid_request")
                input_id = record.input_version_id
            else:
                if new_input is None or new_input.input_version_id != request.input_version_id:
                    raise StorageError("invalid_request")
                if new_input.session_id != session_id or new_input.confirmation not in {
                    "confirmed",
                    "corrected",
                }:
                    raise StorageError("input_unconfirmed")
                conn.execute(
                    self._table("input_versions")
                    .insert()
                    .values(
                        input_version_id=new_input.input_version_id,
                        session_id=session_id,
                        payload=new_input.model_dump(mode="json"),
                        created_at=now(),
                    )
                )
                input_id = new_input.input_version_id
                conn.execute(
                    self._table("analysis_sessions")
                    .update()
                    .where(self._table("analysis_sessions").c.session_id == session_id)
                    .values(input_version_id=input_id)
                )
            self._idempotency(
                conn, subject_id, session_id, request.request_id, "resume", payload, run_id
            )
            updated = record.model_copy(
                update={
                    "input_version_id": input_id,
                    "status": "running",
                    "clarification_rounds": record.clarification_rounds + 1,
                }
            )
            conn.execute(
                runs.update()
                .where(runs.c.run_id == run_id)
                .values(status="running", record=updated.model_dump(mode="json"))
            )
            return updated
