import json
import os
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from packages_py.config import Settings
from packages_py.schemas import AnalysisCandidate, ContextUnderstanding, SafetyDecision, SkipResume
from packages_py.services import Service
from packages_py.storage import PostgresStorage, StorageError
from packages_py.tool_execution import ToolExecutor, register_get_run_status

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_POSTGRES_TESTS"), reason="set RUN_POSTGRES_TESTS=1"
)


class ControlledProvider:
    def understand(self, *, context):
        return ContextUnderstanding()

    def classify_safety(self, **kwargs):
        return SafetyDecision(route="normal")

    def analyze(self, *, context, understanding):
        return AnalysisCandidate(
            session_id=context.session_id,
            run_id=context.run_id,
            input_version_id=context.input_version_id,
            kind="initial",
            problem_summary="沟通目标需明确",
            clarification_question="你希望改善什么？" if context.clarification_allowed else None,
        )

    def revise(self, **kwargs):
        return kwargs["candidate"]


def test_reviewed_reference_survives_workflow_review_and_publish(tmp_path):
    from packages_py.schemas import EvidenceRef

    class ReferenceProvider(ControlledProvider):
        def analyze(self, *, context, understanding):
            return AnalysisCandidate(
                session_id=context.session_id,
                run_id=context.run_id,
                input_version_id=context.input_version_id,
                kind="final",
                evidence_refs=[EvidenceRef(
                    source_kind="reviewed_reference",
                    source_id=source_id,
                    source_version_id=version_id,
                    span_start=2, span_end=4, quoted_text="依据",
                )],
            )

    settings = Settings(
        database_url=os.getenv('DATABASE_URL', 'postgresql+psycopg://laosun@localhost/postgres'),
        model_provider='controlled', model_name='test', model_api_key='test',
        clarification_max_rounds=0, events_path=tmp_path / 'events.jsonl',
    )
    storage = PostgresStorage(settings)
    subject, _ = storage.create_subject(3600)
    other, _ = storage.create_subject(3600)
    service = Service(settings, storage, ReferenceProvider())
    session = service.new_session(subject)
    source_id, version_id = uuid4(), uuid4()
    try:
        with storage.engine.begin() as conn:
            sources, versions = storage._table('sources'), storage._table('source_versions')
            conn.execute(sources.insert().values(
                source_id=source_id, source_type='public', title='fixture',
            ))
            conn.execute(versions.insert().values(
                source_version_id=version_id, source_id=source_id, version='1',
                full_text='xx依据全文', content_sha256='a' * 64,
                license_status='approved', review_status='approved', locator='fixture',
            ))
            sessions = storage._table('analysis_sessions')
            conn.execute(sessions.update().where(
                sessions.c.session_id == session.session_id
            ).values(allowed_source_ids=[source_id]))
        item = service.confirm_input(subject, session.session_id, '我们想确认会议分工')
        response = service.analyze(subject, session.session_id, item.input_version_id, uuid4())
        assert response.status == 'completed'
        assert service.show(subject, session.session_id).kind == 'final'
        with pytest.raises(StorageError, match='not_found'):
            storage.approved_reference_versions(other, session.session_id, {version_id})
    finally:
        assert service.delete(subject, session.session_id, uuid4())['status'] == 'completed'
        storage.close()


@pytest.mark.parametrize('case', ['ordinary', 'risk', 'uncertain'])
def test_three_safety_policies_match_persisted_terminal_result(tmp_path, case):
    class Provider(ControlledProvider):
        analysis_calls = 0

        def classify_safety(self, *, phase, **kwargs):
            if case == 'uncertain' and phase == 'post':
                return SafetyDecision(route='uncertain', risk_flags=['uncertain'])
            return SafetyDecision(route='normal')

        def analyze(self, **kwargs):
            self.analysis_calls += 1
            return super().analyze(**kwargs)

    settings = Settings(
        database_url=os.getenv('DATABASE_URL', 'postgresql+psycopg://laosun@localhost/postgres'),
        model_provider='controlled', model_name='test', model_api_key='test',
        clarification_max_rounds=0, events_path=tmp_path / 'events.jsonl',
    )
    storage = PostgresStorage(settings)
    provider = Provider()
    subject, _ = storage.create_subject(3600)
    service = Service(settings, storage, provider)
    session = service.new_session(subject)
    try:
        text = '我担心自杀风险' if case == 'risk' else '我们想确认会议分工'
        item = service.confirm_input(subject, session.session_id, text)
        response = service.analyze(subject, session.session_id, item.input_version_id, uuid4())
        published = service.show(subject, session.session_id)
        assert response.status == 'completed'
        assert published.status == 'validated'
        assert published.kind == ('final' if case == 'ordinary' else 'safety')
        assert provider.analysis_calls == (1 if case == 'ordinary' else 0)
        from packages_py.schemas import validate_result

        validate_result(published, text)
        if case != 'ordinary':
            assert not published.suggestions
    finally:
        assert service.delete(subject, session.session_id, uuid4())['status'] == 'completed'
        storage.close()


def test_checkpoint_connection_restart_resume(tmp_path):
    url = os.getenv("DATABASE_URL", "postgresql+psycopg://laosun@localhost/postgres")
    settings = Settings(
        database_url=url,
        model_provider="controlled",
        model_name="test",
        model_api_key="test",
        events_path=tmp_path / "events.jsonl",
    )
    storage = PostgresStorage(settings)
    subject, _ = storage.create_subject(3600)
    service = Service(settings, storage, ControlledProvider())
    session = service.new_session(subject)
    item = service.confirm_input(subject, session.session_id, "我们在讨论如何沟通")
    response = service.analyze(subject, session.session_id, item.input_version_id, uuid4())
    assert response.status == "waiting_clarification"
    events = [json.loads(line) for line in settings.events_path.read_text().splitlines()]
    assert events and all(event["input_summary"] is None for event in events)
    assert all(
        "我们在讨论如何沟通" not in json.dumps(event, ensure_ascii=False) for event in events
    )
    assert any(event["status"] == "started" for event in events)
    assert any(event["status"] == "waiting" for event in events)
    from packages_py.services import runtime_manifest

    manifest = runtime_manifest()
    with storage.engine.begin() as conn:
        runs = storage._table("runs")
        row = conn.execute(runs.select().where(runs.c.run_id == response.run_id)).mappings().one()
        record = dict(row["record"])
        record["dependency_lock_hash"] = "old-lock"
        conn.execute(runs.update().where(runs.c.run_id == response.run_id).values(record=record))
    with pytest.raises(Exception, match="checkpoint_incompatible"):
        service.resume(
            subject, session.session_id, response.run_id, SkipResume(request_id=uuid4(), skip=True)
        )
    assert (
        service.storage.get_run(subject, session.session_id, response.run_id)[0].status
        == "waiting_clarification"
    )
    with storage.engine.begin() as conn:
        record["dependency_lock_hash"] = manifest["dependency_lock_hash"]
        conn.execute(runs.update().where(runs.c.run_id == response.run_id).values(record=record))
    assert service.show(subject, session.session_id).status == "validated"
    executor = ToolExecutor()
    repository = storage.workflow_repository(subject)
    register_get_run_status(executor, repository, subject_id=subject, session_id=session.session_id)
    result = executor.execute(
        "get_run_status",
        {"run_id": str(response.run_id)},
        {"subject_id": str(subject), "session_id": str(session.session_id)},
    )
    assert result.ok and result.output["status"] == "waiting_clarification"
    tool_node = executor.tool_node(
        {"subject_id": str(subject), "session_id": str(session.session_id)}
    )
    output = tool_node.invoke(
        {
            "messages": [
                HumanMessage("status"),
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "get_run_status",
                            "args": {"run_id": str(response.run_id)},
                            "id": "tool-call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
            ]
        }
    )
    assert output["messages"][-1].content.find("waiting_clarification") >= 0
    storage.close()
    restarted = Service(settings, PostgresStorage(settings), ControlledProvider())
    request = SkipResume(request_id=uuid4(), skip=True)
    response = restarted.resume(subject, session.session_id, response.run_id, request)
    assert response.status == "completed"
    repeated = restarted.resume(subject, session.session_id, response.run_id, request)
    assert repeated.clarification_rounds == response.clarification_rounds == 1
    result = restarted.show(subject, session.session_id)
    assert result.kind == "final" and result.clarification_question is None
    with storage.engine.connect() as conn:
        thread_id = conn.execute(
            storage._table("thread_mappings")
            .select()
            .with_only_columns(storage._table("thread_mappings").c.thread_id)
            .where(storage._table("thread_mappings").c.run_id == response.run_id)
        ).scalar_one()
    receipt = restarted.delete(subject, session.session_id, uuid4())
    assert receipt["status"] == "completed"
    from psycopg import connect

    with connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
        for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
            assert (
                connection.execute(
                    f"SELECT count(*) FROM checkpoint.{table} WHERE thread_id = %s", (thread_id,)
                ).fetchone()[0]
                == 0
            )
    restarted.storage.close()
