"""共享业务服务层，命令行与后续 API 统一通过此处进入工作流。"""

from __future__ import annotations

import hashlib
import platform
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import UUID, uuid4

from packages_py.compatibility import check_compatibility
from packages_py.config import Settings
from packages_py.providers import PROMPT_VERSION, ProviderError, ProviderRegistry
from packages_py.schemas import (
    AnalysisResult,
    AnalysisSession,
    InputVersion,
    RunRecord,
    RunResponse,
)
from packages_py.storage import PostgresStorage, StorageError
from packages_py.workflow_runtime import WorkflowRuntime


def runtime_manifest() -> dict:
    """生成恢复检查点所需的运行时版本与依赖信息。"""
    lock = Path(__file__).resolve().parent.parent / "uv.lock"
    return {
        "runtime_version": "stage1.runtime.v1",
        "graph_version": "stage1.graph.v1",
        "workflow_state_schema_version": "stage1.state.v1",
        "dependency_lock_hash": hashlib.sha256(lock.read_bytes()).hexdigest()
        if lock.is_file()
        else "unlocked",
        "checkpoint_compatibility_profile": "stage1.pg.v1",
    }


def serialized(method):
    """使用仓储层的会话锁，串行执行改变会话状态的操作。"""
    @wraps(method)
    def wrapped(self, subject_id, session_id, *args, **kwargs):
        with self.storage.execution_lock(session_id):
            return method(self, subject_id, session_id, *args, **kwargs)

    return wrapped


class Service:
    """为各类前端统一协调模型调用、持久化与工作流执行。"""
    def __init__(
        self,
        settings: Settings | None = None,
        storage: PostgresStorage | None = None,
        provider=None,
    ):
        self.settings = settings or Settings()
        self.storage = storage or PostgresStorage(self.settings)
        self.provider = provider
        self._injected_provider = provider is not None
        if (
            provider is None
            and self.settings.model_provider
            and self.settings.model_name
            and self.settings.model_api_key
        ):
            self.provider = ProviderRegistry().create(
                {
                    "provider": self.settings.model_provider,
                    "model_name": self.settings.model_name,
                    "endpoint": self.settings.model_endpoint,
                    "timeout_seconds": self.settings.model_timeout_seconds,
                    "max_attempts": self.settings.model_max_attempts,
                },
                self.settings.model_api_key,
            )

    def require_provider(self):
        if self.provider is None:
            raise StorageError("model_provider_not_configured")
        return self.provider

    def new_session(self, subject_id: UUID) -> AnalysisSession:
        return self.storage.create_session(subject_id, 86400)

    def confirm_input(
        self,
        subject_id: UUID,
        session_id: UUID,
        text: str,
        modality: str = "text",
        correction: bool = False,
    ) -> InputVersion:
        if not text.strip():
            raise StorageError("invalid_request")
        return self.storage.append_input(
            subject_id,
            InputVersion(
                session_id=session_id,
                modality=modality,
                confirmed_text=text,
                confirmation="corrected" if correction else "confirmed",
            ),
        )

    def _run_record(
        self, session: AnalysisSession, input_version: InputVersion, request_id: UUID
    ) -> RunRecord:
        def package_version(name: str) -> str:
            try:
                return version(name)
            except PackageNotFoundError:
                return "unknown"

        lock_path = Path(__file__).resolve().parent.parent / "uv.lock"
        lock_hash = (
            hashlib.sha256(lock_path.read_bytes()).hexdigest()
            if lock_path.is_file()
            else "unlocked"
        )
        return RunRecord(
            run_id=uuid4(),
            session_id=session.session_id,
            input_version_id=input_version.input_version_id,
            request_id=request_id,
            thread_id=str(uuid4()),
            status="running",
            clarification_rounds=0,
            model_version=self.settings.model_name or "unknown",
            prompt_version=PROMPT_VERSION,
            graph_version="stage1.graph.v1",
            rules_version="safety.v1",
            schema_version="stage1.schema.v1",
            runtime_version="stage1.runtime.v1",
            python_version=platform.python_version(),
            langgraph_version=package_version("langgraph"),
            langchain_core_version=package_version("langchain-core"),
            checkpointer_version=package_version("langgraph-checkpoint-postgres"),
            checkpoint_compatibility_profile="stage1.pg.v1",
            dependency_lock_hash=lock_hash,
            workflow_state_schema_version="stage1.state.v1",
            provider=self.settings.model_provider or "unconfigured",
            config_version="config.v1",
            credential_ref="env" if self.settings.model_api_key else "none",
        )

    def _runtime(self, subject_id: UUID, snapshot=None):
        repository = self.storage.workflow_repository(subject_id)
        provider = self.require_provider()
        if snapshot is not None:
            repository.output_max_revisions = snapshot["output_max_revisions"]
            if not self._injected_provider:
                if snapshot["credential_ref"] != ("env" if self.settings.model_api_key else "none"):
                    raise StorageError("provider_unavailable")
                provider = ProviderRegistry().create(
                    {
                        "provider": snapshot["provider"],
                        "model_name": snapshot["model_version"],
                        "endpoint": snapshot.get("endpoint"),
                        "timeout_seconds": snapshot["timeout_seconds"],
                        "max_attempts": snapshot["max_attempts"],
                    },
                    self.settings.model_api_key,
                )
        url = self.settings.checkpoint_url or self.settings.database_url
        return WorkflowRuntime(repository, provider, url, self.settings.checkpoint_schema)

    @serialized
    def analyze(
        self, subject_id: UUID, session_id: UUID, input_version_id: UUID, request_id: UUID
    ) -> RunResponse:
        # 随运行保存配置快照，恢复时沿用原模型和次数限制，
        # 避免工作区配置变更悄然影响已开始的分析。
        self.require_provider()
        session = self.storage.get_session(subject_id, session_id)
        input_version = self.storage.get_input(subject_id, session_id, input_version_id)
        run = self._run_record(session, input_version, request_id)
        snapshot = {
            "config_snapshot_schema_version": "stage1.config.v1",
            "provider": run.provider,
            "model_version": run.model_version,
            "timeout_seconds": self.settings.model_timeout_seconds,
            "max_attempts": self.settings.model_max_attempts,
            "credential_ref": run.credential_ref,
            "clarification_max_rounds": self.settings.clarification_max_rounds,
            "output_max_revisions": self.settings.output_max_revisions,
            "runtime_version": run.runtime_version,
            "graph_version": run.graph_version,
            "workflow_state_schema_version": run.workflow_state_schema_version,
            "dependency_lock_hash": run.dependency_lock_hash,
            "checkpoint_compatibility_profile": run.checkpoint_compatibility_profile,
            "python_version": run.python_version,
            "langgraph_version": run.langgraph_version,
            "langchain_core_version": run.langchain_core_version,
            "checkpointer_version": run.checkpointer_version,
        }
        snapshot["endpoint"] = self.settings.model_endpoint
        run = self.storage.create_run(
            subject_id, run, snapshot, {"input_version_id": str(input_version_id)}
        )
        if run.status != "running":
            return RunResponse.model_validate(run.model_dump(include=set(RunResponse.model_fields)))
        _, snapshot = self.storage.get_run(subject_id, session_id, run.run_id)
        runtime = self._runtime(subject_id, snapshot)
        try:
            state = runtime.start(
                {
                    "session_id": str(session_id),
                    "run_id": str(run.run_id),
                    "input_version_id": str(input_version_id),
                    "thread_id": run.thread_id,
                    "clarification_rounds": 0,
                    "clarification_max_rounds": snapshot["clarification_max_rounds"],
                    "clarification_skipped": False,
                }
            )
            status = (
                "waiting_clarification"
                if state.get("phase") == "waiting_clarification"
                else "completed"
            )
            if state.get("phase") == "failed":
                raise StorageError("output_validation_failed")
            return RunResponse.model_validate(
                {
                    key: value
                    for key, value in {**run.model_dump(), "status": status}.items()
                    if key in RunResponse.model_fields
                }
            )
        except ProviderError as exc:
            self.storage.update_run_status(subject_id, session_id, run.run_id, "failed", exc.code)
            raise StorageError(exc.code) from exc
        except StorageError as exc:
            if exc.code != "stale_version":
                self.storage.update_run_status(
                    subject_id, session_id, run.run_id, "failed", exc.code
                )
            raise
        except Exception as exc:
            self.storage.update_run_status(
                subject_id, session_id, run.run_id, "failed", "output_validation_failed"
            )
            raise StorageError("output_validation_failed") from exc
        finally:
            runtime.close()

    @serialized
    def resume(
        self, subject_id: UUID, session_id: UUID, run_id: UUID, request, text: str | None = None
    ) -> RunResponse:
        """检查运行时兼容性及输入合法性后，继续暂停的运行。"""
        if not request.skip and not text:
            raise StorageError("invalid_request")
        if request.skip and text is not None:
            raise StorageError("invalid_request")
        new_input = (
            None
            if request.skip
            else InputVersion(
                input_version_id=request.input_version_id,
                session_id=session_id,
                modality="text",
                confirmed_text=text,
                confirmation="corrected",
            )
        )
        previous, snapshot = self.storage.get_run(subject_id, session_id, run_id)
        if check_compatibility(previous.model_dump(), runtime_manifest())["action"] != "allowed":
            raise StorageError("checkpoint_incompatible")
        run = self.storage.resume_run(
            subject_id,
            session_id,
            run_id,
            request,
            new_input,
            max_rounds=snapshot["clarification_max_rounds"],
        )
        if run.status != "running":
            return RunResponse.model_validate(run.model_dump(include=set(RunResponse.model_fields)))
        runtime = self._runtime(subject_id, snapshot)
        try:
            state = runtime.resume(
                {
                    "thread_id": run.thread_id,
                    "request": {
                        **request.model_dump(mode="json"),
                        "clarification_rounds": run.clarification_rounds,
                        "clarification_skipped": request.skip,
                    },
                    "verified_state": {
                        "runtime_version": run.runtime_version,
                        "graph_version": run.graph_version,
                        "workflow_state_schema_version": run.workflow_state_schema_version,
                        "dependency_lock_hash": run.dependency_lock_hash,
                        "checkpoint_compatibility_profile": run.checkpoint_compatibility_profile,
                    },
                    "runtime_manifest": runtime_manifest(),
                }
            )
            status = (
                "completed"
                if state.get("phase") != "waiting_clarification"
                else "waiting_clarification"
            )
            return RunResponse.model_validate(
                {
                    key: value
                    for key, value in {**run.model_dump(), "status": status}.items()
                    if key in RunResponse.model_fields
                }
            )
        except Exception as exc:
            code = getattr(exc, "code", None) or (
                str(exc)
                if str(exc) in {"checkpoint_incompatible", "run_not_waiting"}
                else "output_validation_failed"
            )
            if code not in {"checkpoint_incompatible", "run_not_waiting"}:
                self.storage.update_run_status(subject_id, session_id, run_id, "failed", code)
            raise StorageError(code) from exc
        finally:
            runtime.close()

    def show(self, subject_id: UUID, session_id: UUID) -> AnalysisResult | None:
        return self.storage.latest_result(subject_id, session_id)

    def delete(self, subject_id: UUID, session_id: UUID, request_id: UUID,
               request_payload: dict | None = None) -> dict:
        return self.storage.delete_session(subject_id, session_id, request_id,
                                           request_payload=request_payload)

    def retrieve_references(self, subject_id, session_id, query, source_ids, limit=5):
        return self.storage.retrieve_references(subject_id, session_id, query, source_ids, limit)

    def set_improvement_consent(self, subject_id, consent):
        return self.storage.set_consent(subject_id, consent)

    def revoke_improvement_consent(self, subject_id, session_id, consent_id):
        return self.storage.revoke_consent(subject_id, session_id, consent_id)

    def add_improvement_copy(self, subject_id, consent_id, data_version_id, redacted_payload):
        return self.storage.add_improvement_copy(
            subject_id, consent_id, data_version_id, redacted_payload
        )
