import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from datetime import timedelta

import pytest
import sqlalchemy as sa

from packages_py.config import Settings
from packages_py.schemas import ImprovementConsent
from packages_py.services import Service
from packages_py.storage import PostgresStorage, StorageError
from packages_py.storage import now
from packages_py.tool_execution import ToolExecutor
from packages_py.tool_protocol import ToolResult, ToolSpec

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_POSTGRES_TESTS"), reason="set RUN_POSTGRES_TESTS=1"
)


def test_execution_lock_allows_concurrent_index_and_releases_after_error():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    session_id = uuid4()
    lock_key = int.from_bytes(hashlib.sha256(session_id.bytes).digest()[:8], "big", signed=True)
    table_name = f'"{settings.database_schema}"."lock_regression_{uuid4().hex}"'
    with storage.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("SET statement_timeout = '3s'")
        conn.exec_driver_sql(f"CREATE TABLE {table_name} (id integer)")
        try:
            with pytest.raises(RuntimeError, match="release lock"), storage.execution_lock(session_id):
                # The workflow's checkpointer performs this on another
                # connection. An open lock transaction used to block it.
                conn.exec_driver_sql(f"CREATE INDEX CONCURRENTLY ON {table_name} (id)")
                raise RuntimeError("release lock")
        finally:
            conn.exec_driver_sql(f"DROP TABLE {table_name}")
    with storage.engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}
        ).scalar_one()
        conn.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        conn.commit()
    storage.close()


def test_postgres_lifecycle():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    subject, _ = storage.create_subject(3600)
    session = storage.create_session(subject, 3600)
    storage.append_input(
        subject,
        __import__("packages_py.schemas", fromlist=["InputVersion"]).InputVersion(
            session_id=session.session_id,
            modality="text",
            confirmed_text="你好",
            confirmation="confirmed",
        ),
    )
    with pytest.raises(StorageError, match="model_provider_not_configured"):
        Service(settings, storage).require_provider()
    assert storage.get_session(subject, session.session_id).session_id == session.session_id
    receipt = storage.delete_session(subject, session.session_id, uuid4())
    assert receipt["status"] == "completed"
    assert (
        storage.delete_session(subject, session.session_id, uuid4())["deletion_id"]
        == receipt["deletion_id"]
    )
    with pytest.raises(StorageError, match="not_found"):
        storage.get_session(subject, session.session_id)
    storage.close()


def test_consent_withdrawal_cleans_copies_without_deleting_service_data():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    subject, _ = storage.create_subject(3600)
    other, _ = storage.create_subject(3600)
    session = storage.create_session(subject, 3600)
    from packages_py.schemas import InputVersion

    item = storage.append_input(
        subject,
        InputVersion(
            session_id=session.session_id,
            modality="text",
            confirmed_text="private",
            confirmation="confirmed",
        ),
    )
    consent = ImprovementConsent(
        subject_id=subject,
        session_id=session.session_id,
        notice_version="1",
        status="granted",
        data_version_ids=[item.input_version_id],
    )
    service = Service(settings, storage)
    service.set_improvement_consent(subject, consent)
    service.add_improvement_copy(
        subject, consent.consent_id, item.input_version_id, {"redacted": "sample"}
    )
    with pytest.raises(StorageError, match="not_found"):
        service.revoke_improvement_consent(other, session.session_id, consent.consent_id)
    service.revoke_improvement_consent(subject, session.session_id, consent.consent_id)
    with storage.engine.connect() as conn:
        copies = storage._table("improvement_copies")
        assert (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(copies)
                .where(copies.c.session_id == session.session_id)
            ).scalar_one()
            == 0
        )
    assert (
        storage.get_input(subject, session.session_id, item.input_version_id).confirmed_text
        == "private"
    )
    with pytest.raises(StorageError, match="unauthorized"):
        service.add_improvement_copy(subject, consent.consent_id, item.input_version_id, {})
    assert service.delete(subject, session.session_id, uuid4())["status"] == "completed"
    storage.close()


def test_lifecycle_ownership_expiry_retrieval_and_unwired_asset_cleanup(tmp_path):
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres"),
        reference_retrieval_enabled=True,
        asset_storage_root=tmp_path / "private-assets",
    )
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        other, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        item = storage.append_input(subject, __import__(
            "packages_py.schemas", fromlist=["InputVersion"]
        ).InputVersion(session_id=session.session_id, modality="text",
                       confirmed_text="synthetic source", confirmation="confirmed"))
        with pytest.raises(StorageError, match="not_found"):
            storage.load_workflow_input(str(session.session_id), str(item.input_version_id),
                                        subject_id=other)

        source_id, version_id = uuid4(), uuid4()
        with storage.engine.begin() as conn:
            conn.execute(storage._table("sources").insert().values(
                source_id=source_id, source_type="guide", title="Synthetic guide"))
            conn.execute(storage._table("source_versions").insert().values(
                source_version_id=version_id, source_id=source_id, version="1",
                full_text="沟通 边界与沟通 建议", content_sha256="a" * 64,
                license_status="approved", review_status="approved", locator="p1"))
            sessions = storage._table("analysis_sessions")
            conn.execute(sessions.update().where(
                sessions.c.session_id == session.session_id
            ).values(allowed_source_ids=[source_id]))
        found = storage.retrieve_references(subject, session.session_id, "沟通", [source_id])
        assert len(found) == 1 and found[0].source_version_id == version_id
        assert "chars=" in found[0].locator
        approved = storage.approved_reference_versions(subject, session.session_id, {version_id})
        assert approved == {version_id: (source_id, "沟通 边界与沟通 建议")}
        with pytest.raises(StorageError, match="not_found"):
            storage.approved_reference_versions(other, session.session_id, {version_id})
        with pytest.raises(StorageError, match="not_found"):
            storage.retrieve_references(other, session.session_id, "沟通", [source_id])

        asset_id = uuid4()
        storage.create_asset(subject, session.session_id, asset_id, "image", b"synthetic-image",
                             now() + timedelta(minutes=1))
        assert storage.read_asset(subject, session.session_id, asset_id) == b"synthetic-image"
        with pytest.raises(StorageError, match="idempotency_conflict"):
            storage.create_asset(subject, session.session_id, asset_id, "image", b"replacement",
                                 now() + timedelta(minutes=1))
        with pytest.raises(StorageError, match="not_found"):
            storage.get_asset(other, session.session_id, asset_id)
        with storage.engine.begin() as conn:
            assets = storage._table("assets")
            conn.execute(assets.update().where(assets.c.asset_id == asset_id).values(
                expires_at=now() - timedelta(seconds=1)))
        with pytest.raises(StorageError, match="not_found"):
            storage.get_asset(subject, session.session_id, asset_id)
        path = tmp_path / "private-assets" / str(subject) / str(session.session_id) / str(asset_id)
        assert path.exists()
        receipt = storage.delete_session(subject, session.session_id, uuid4())
        assert receipt["status"] == "completed"
        assert not path.exists()
    finally:
        storage.close()


def test_retrieval_default_closed_and_filters_unapproved_versions():
    base = os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    closed = PostgresStorage(Settings(database_url=base))
    subject, _ = closed.create_subject(3600)
    session = closed.create_session(subject, 3600)
    assert closed.retrieve_references(subject, session.session_id, "沟通", []) == []
    closed.delete_session(subject, session.session_id, uuid4())
    closed.close()

    settings = Settings(database_url=base, reference_retrieval_enabled=True)
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        allowed, denied = uuid4(), uuid4()
        with storage.engine.begin() as conn:
            sources, versions = storage._table("sources"), storage._table("source_versions")
            conn.execute(sources.insert(), [
                {"source_id": allowed, "source_type": "guide", "title": "Allowed"},
                {"source_id": denied, "source_type": "guide", "title": "Denied"},
            ])
            allowed_version, denied_version = uuid4(), uuid4()
            conn.execute(versions.insert(), [
                {"source_version_id": allowed_version, "source_id": allowed, "version": "1",
                 "full_text": "沟通内容", "content_sha256": "a" * 64,
                 "license_status": "approved", "review_status": "approved", "locator": "a"},
                {"source_version_id": denied_version, "source_id": denied, "version": "1",
                 "full_text": "沟通内容", "content_sha256": "b" * 64,
                 "license_status": "pending", "review_status": "approved", "locator": "b"},
            ])
            conn.execute(storage._table("analysis_sessions").update().where(
                storage._table("analysis_sessions").c.session_id == session.session_id
            ).values(allowed_source_ids=[allowed]))
        found = storage.retrieve_references(subject, session.session_id, "沟通", [allowed, denied])
        assert len(found) == 1 and found[0].title == "Allowed"
        authorized = storage.approved_reference_versions(
            subject, session.session_id, {allowed_version, denied_version}
        )
        assert authorized == {allowed_version: (allowed, "沟通内容")}
    finally:
        storage.delete_session(subject, session.session_id, uuid4())
        storage.close()


def test_new_session_has_denied_consent_and_analysis_input_is_available():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        from packages_py.schemas import InputVersion
        item = storage.append_input(subject, InputVersion(
            session_id=session.session_id, modality="text", confirmed_text="synthetic",
            confirmation="confirmed"))
        with storage.engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(
                storage._table("consents")).where(
                    storage._table("consents").c.session_id == session.session_id
                )).scalar_one() == 0
        assert storage.get_input(subject, session.session_id, item.input_version_id).confirmed_text == "synthetic"
    finally:
        storage.delete_session(subject, session.session_id, uuid4())
        storage.close()


def test_concurrent_consent_withdrawal_leaves_no_improvement_copy():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        from packages_py.schemas import InputVersion
        item = storage.append_input(subject, InputVersion(
            session_id=session.session_id, modality="text", confirmed_text="synthetic",
            confirmation="confirmed"))
        consent = ImprovementConsent(subject_id=subject, session_id=session.session_id,
            notice_version="synthetic-v1", status="granted",
            data_version_ids=[item.input_version_id])
        storage.set_consent(subject, consent)
        barrier = threading.Barrier(2)
        def add_copy():
            barrier.wait()
            try:
                storage.add_improvement_copy(subject, consent.consent_id,
                                             item.input_version_id, {"synthetic": True})
            except StorageError as exc:
                assert exc.code == "unauthorized"
        def revoke():
            barrier.wait()
            storage.revoke_consent(subject, session.session_id, consent.consent_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda fn: fn(), [add_copy, revoke]))
        with storage.engine.connect() as conn:
            copies = storage._table("improvement_copies")
            assert conn.execute(sa.select(sa.func.count()).select_from(copies).where(
                copies.c.consent_id == consent.consent_id)).scalar_one() == 0
        assert storage.get_input(subject, session.session_id, item.input_version_id)
    finally:
        storage.delete_session(subject, session.session_id, uuid4())
        storage.close()


def test_deletion_retries_only_pending_scopes_and_records_attempts(monkeypatch):
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        original = storage._clean_scopes
        fail_once = {"value": True}

        def flaky(conn, job, scopes):
            if scopes == ["events"] and fail_once["value"]:
                fail_once["value"] = False
                raise RuntimeError("synthetic cleanup failure")
            return original(conn, job, scopes)

        monkeypatch.setattr(storage, "_clean_scopes", flaky)
        first = storage.delete_session(subject, session.session_id, uuid4())
        assert first["status"] == "failed" and first["pending_scopes"] == ["events"]
        second = storage.retry_deletion(subject, session.session_id)
        assert second["status"] == "completed"
        attempts = storage._table("deletion_attempts")
        with storage.engine.connect() as conn:
            records = conn.execute(sa.select(attempts).where(
                attempts.c.deletion_id == __import__("uuid").UUID(first["deletion_id"])
            ).order_by(attempts.c.started_at)).mappings().all()
        assert len(records) == 2
        assert records[0]["remaining_scopes"] == ["events"]
        assert records[1]["attempted_scopes"] == ["events"]
    finally:
        storage.close()


def test_delete_tombstone_prevents_late_artifact_write_and_restore_gate_order():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        receipt = storage.delete_session(subject, session.session_id, uuid4())
        assert receipt["status"] == "completed"
        with pytest.raises(StorageError, match="not_found"):
            storage.store_workflow_artifact(str(uuid4()), str(session.session_id),
                                            "understanding", {"synthetic": True})
        order = []
        storage.restore_replaying_deletions(
            lambda: order.append("replayed"), lambda: order.append("opened")
        )
        assert order == ["replayed", "opened"]
    finally:
        storage.close()


def test_replay_deletion_tombstones_cleans_before_opening_restored_content(monkeypatch):
    from alembic import command
    from alembic.config import Config

    database_url = os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    schema, checkpoint_schema = f"app_replay_{uuid4().hex[:10]}", f"checkpoint_replay_{uuid4().hex[:10]}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    monkeypatch.setenv("CHECKPOINT_SCHEMA", checkpoint_schema)
    command.upgrade(Config("alembic.ini"), "head")
    settings = Settings(database_url=database_url, database_schema=schema,
                        checkpoint_schema=checkpoint_schema)
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        receipt = storage.delete_session(subject, session.session_id, uuid4())
        attempts = storage._table("deletion_attempts")
        with storage.engine.begin() as conn:
            conn.execute(storage._table("deletion_jobs").update().where(
                storage._table("deletion_jobs").c.session_id == session.session_id
            ).values(status="failed", pending_scopes=["events"], error_code="synthetic"))
        order = []
        original = storage._clean_scopes
        def record(conn, job, scopes):
            order.append("cleaned")
            return original(conn, job, scopes)
        monkeypatch.setattr(storage, "_clean_scopes", record)
        storage.replay_deletion_tombstones(lambda: order.append("opened"))
        assert order == ["cleaned", "opened"]
        with storage.engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(attempts).where(
                attempts.c.deletion_id == __import__("uuid").UUID(receipt["deletion_id"])
            )).scalar_one() == 2
    finally:
        storage.close()


def test_concurrent_session_deletes_reuse_one_deletion_job():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        request_ids = [uuid4() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(
                lambda request_id: storage.delete_session(subject, session.session_id, request_id),
                request_ids,
            ))
        assert {receipt["deletion_id"] for receipt in receipts}.__len__() == 1
        assert all(receipt["status"] == "completed" for receipt in receipts)
        jobs = storage._table("deletion_jobs")
        attempts = storage._table("deletion_attempts")
        with storage.engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(jobs).where(
                jobs.c.session_id == session.session_id)).scalar_one() == 1
            assert conn.execute(sa.select(sa.func.count()).select_from(attempts).where(
                attempts.c.deletion_id == __import__("uuid").UUID(receipts[0]["deletion_id"])
            )).scalar_one() == 1
    finally:
        storage.close()


def test_delete_request_id_payload_conflict_is_rejected():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    try:
        subject, _ = storage.create_subject(3600)
        session = storage.create_session(subject, 3600)
        request_id = uuid4()
        storage.delete_session(subject, session.session_id, request_id,
                               request_payload={"session_id": str(session.session_id),
                                                "intent": "user_requested"})
        with pytest.raises(StorageError, match="idempotency_conflict"):
            storage.delete_session(subject, session.session_id, request_id,
                                   request_payload={"session_id": str(session.session_id),
                                                    "intent": "different"})
    finally:
        storage.close()


def test_retention_configuration_requires_complete_bounded_policy():
    with pytest.raises(ValueError, match="retention policy is required"):
        Settings(service_ttl_seconds=3600, token_ttl_seconds=None, asset_ttl_seconds=None,
                 checkpoint_ttl_seconds=None, deletion_receipt_ttl_seconds=None,
                 backup_ttl_seconds=None).validate_retention()
    Settings(service_ttl_seconds=3600, token_ttl_seconds=1800, asset_ttl_seconds=1800,
             checkpoint_ttl_seconds=1800, deletion_receipt_ttl_seconds=1800,
             backup_ttl_seconds=1800).validate_retention()
    with pytest.raises(ValueError, match="cannot exceed"):
        Settings(service_ttl_seconds=3600, token_ttl_seconds=7200, asset_ttl_seconds=1800,
                 checkpoint_ttl_seconds=1800, deletion_receipt_ttl_seconds=1800,
                 backup_ttl_seconds=1800).validate_retention()


def test_0004_upgrade_preserves_existing_lifecycle_rows(monkeypatch):
    from alembic import command
    from alembic.config import Config

    database_url = os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    schema = f"app_migration_preserve_{uuid4().hex[:10]}"
    checkpoint_schema = f"checkpoint_migration_preserve_{uuid4().hex[:10]}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    monkeypatch.setenv("CHECKPOINT_SCHEMA", checkpoint_schema)
    config = Config("alembic.ini")
    engine = sa.create_engine(database_url)
    try:
        command.upgrade(config, "0003_tool_payload_hash")
        subject, session, input_id, run_id, result_id, consent_id, deletion_id = (
            uuid4() for _ in range(7)
        )
        metadata = sa.MetaData()
        metadata.reflect(engine, schema=schema)
        tables = {table.name: table for table in metadata.tables.values()}
        input_payload = {"input_version_id": str(input_id), "session_id": str(session),
                         "modality": "text", "confirmed_text": "synthetic", "confirmation": "confirmed"}
        result_payload = {"result_version_id": str(result_id), "session_id": str(session),
                          "run_id": str(run_id), "input_version_id": str(input_id), "kind": "final"}
        run_record = {"run_id": str(run_id), "session_id": str(session),
                      "input_version_id": str(input_id), "request_id": str(uuid4()),
                      "thread_id": "synthetic-thread", "status": "failed", "clarification_rounds": 0,
                      **{key: "synthetic" for key in (
                          "model_version", "prompt_version", "graph_version", "rules_version",
                          "schema_version", "runtime_version", "python_version", "langgraph_version",
                          "langchain_core_version", "checkpointer_version", "checkpoint_compatibility_profile",
                          "dependency_lock_hash", "workflow_state_schema_version", "provider",
                          "config_version", "credential_ref")}}
        consent_payload = {"consent_id": str(consent_id), "subject_id": str(subject),
                           "session_id": str(session), "notice_version": "synthetic-v1",
                           "status": "denied", "data_version_ids": [],
                           "updated_at": "2026-10-26T00:00:00Z"}
        with engine.begin() as conn:
            conn.execute(tables["subjects"].insert().values(
                subject_id=subject, expires_at=now() + timedelta(days=1)))
            conn.execute(tables["analysis_sessions"].insert().values(
                session_id=session, subject_id=subject, input_version_id=input_id,
                active_run_id=run_id, allowed_source_ids=[], deleted_at=now(),
                expires_at=now() + timedelta(days=1)))
            conn.execute(tables["input_versions"].insert().values(
                input_version_id=input_id, session_id=session, payload=input_payload, created_at=now()))
            conn.execute(tables["runs"].insert().values(
                run_id=run_id, session_id=session, request_id=uuid4(), payload_hash="synthetic",
                status="failed", record=run_record, config_snapshot={},
                config_snapshot_schema_version="synthetic", created_at=now()))
            conn.execute(tables["result_versions"].insert().values(
                result_version_id=result_id, run_id=run_id, session_id=session,
                input_version_id=input_id, payload=result_payload, validated_at=None))
            conn.execute(tables["consents"].insert().values(
                consent_id=consent_id, subject_id=subject, session_id=session,
                payload=consent_payload))
            conn.execute(tables["deletion_jobs"].insert().values(
                deletion_id=deletion_id, subject_id=subject, session_id=session,
                status="failed", pending_scopes=["events"], error_code="synthetic_failure",
                expires_at=now() + timedelta(days=1)))
        before = {}
        for name, key in (("analysis_sessions", "session_id"), ("input_versions", "input_version_id"),
                          ("runs", "run_id"), ("result_versions", "result_version_id"),
                          ("consents", "consent_id"), ("deletion_jobs", "deletion_id")):
            with engine.connect() as conn:
                before[name] = dict(conn.execute(sa.select(tables[name])).mappings().one())
        command.upgrade(config, "head")
        after_meta = sa.MetaData()
        after_meta.reflect(engine, schema=schema)
        for name in before:
            table = after_meta.tables[f"{schema}.{name}"]
            with engine.connect() as conn:
                after = dict(conn.execute(sa.select(table)).mappings().one())
            for key, value in before[name].items():
                assert after[key] == value
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{checkpoint_schema}" CASCADE'))
        engine.dispose()


def test_durable_side_effect_idempotency_conflict_and_timeout_reconciliation():
    settings = Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    )
    storage = PostgresStorage(settings)
    calls = []
    reconciled = []
    calls_lock = threading.Lock()
    executor = ToolExecutor(storage)
    spec = ToolSpec(
        name="isolated_write",
        description="Write to an isolated test target.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"stored": {"type": "string"}},
            "required": ["stored"],
            "additionalProperties": False,
        },
        version="1",
        timeout_seconds=1,
        auth_scope="test:write",
        side_effect="write",
    )

    def handler(payload, _context):
        with calls_lock:
            calls.append(payload["value"])
        if payload["value"] == "parallel":
            time.sleep(0.05)
        if payload["value"] == "slow":
            time.sleep(1.05)
        return {"stored": payload["value"]}

    def reconcile(_key, payload, _context):
        reconciled.append(payload["value"])
        if payload["value"] == "slow":
            return ToolResult(tool_name="isolated_write", ok=True, output={"stored": "slow"})
        return None

    executor.register(spec, handler, lambda _ctx, scope: scope == "test:write", reconcile)
    stable_key, parallel_key, slow_key = (str(uuid4()) for _ in range(3))
    first = executor.execute("isolated_write", {"value": "once"}, {}, stable_key)
    replay = executor.execute("isolated_write", {"value": "once"}, {}, stable_key)
    conflict = executor.execute("isolated_write", {"value": "different"}, {}, stable_key)
    assert first.ok and replay == first and calls == ["once"]
    assert conflict.error_code == "tool_idempotency_conflict"

    with ThreadPoolExecutor(max_workers=4) as pool:
        parallel = list(
            pool.map(
                lambda _index: executor.execute(
                    "isolated_write", {"value": "parallel"}, {}, parallel_key
                ),
                range(4),
            )
        )
    assert all(result.ok and result.output == {"stored": "parallel"} for result in parallel)
    assert calls.count("parallel") == 1

    timed_out = executor.execute("isolated_write", {"value": "slow"}, {}, slow_key)
    recovered = executor.execute("isolated_write", {"value": "slow"}, {}, slow_key)
    assert timed_out.error_code == "tool_timeout"
    assert recovered.ok and recovered.output == {"stored": "slow"}
    assert calls == ["once", "parallel", "slow"] and reconciled == ["slow"]
    storage.close()
