"""Service, graph, SQLite and HTTP integration with simulated model/Guard."""
import asyncio
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from backend.app import create_app
from backend.services import AnalysisService, BusinessError
from backend.storage import Database
from backend.tests.test_service_reliability import DECISION, Guard, Provider, service

ASK = {"action": "ask_clarification", "clarification_question": "请补充时间？", "result": None}


def test_history_correction_is_ordered_isolated_and_restored(service):
    async def run():
        first = await service.create("合成事实：周二开会")
        other = await service.create("另一个会话的合成事实")
        await service.create("纠正：周三开会", session_id=first.session_id)
        restarted = AnalysisService(database=service.database, provider=Provider(), guard=Guard(),
                                    settings=service.settings)
        third = await restarted.create("请沿用纠正的信息", session_id=first.session_id)
        history = restarted.provider.calls[0][0]["history"]
        assert [m["role"] for m in history] == ["user", "assistant"] * 2
        assert history[0]["content"] == "合成事实：周二开会"
        assert history[2]["content"] == "纠正：周三开会"
        assert "另一个会话" not in json.dumps(history, ensure_ascii=False)
        assert third.turn_index == 2 and other.turn_index == 0
        assert len(restarted.session_history(first.session_id)["items"]) == 3
        assert len(service.guard.inputs) == 3
        assert "纠正" in restarted.guard.inputs[0]
    asyncio.run(run())


def test_clarification_restores_question_answer_once(service):
    service.provider.outputs = [ASK, DECISION, DECISION]
    async def run():
        first = await service.create("合成原始信息")
        with pytest.raises(BusinessError) as error:
            await service.create("不能跳过澄清", session_id=first.session_id)
        assert error.value.code == "session_waiting_clarification"
        answer = await service.continue_with_answer(first.id, "合成补充：周三")
        assert answer.clarification_answer == "合成补充：周三"
        current = service.provider.calls[1][0]
        assert current["clarification_answer"] == answer.clarification_answer
        assert current["clarification_round"] == 1 and not current["history"]
        await service.create("后续问题", session_id=first.session_id)
        history = service.provider.calls[-1][0]["history"]
        assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
        assert [m["content"] for m in history[:3]] == ["合成原始信息", ASK["clarification_question"], "合成补充：周三"]
        assert len(service.session_history(first.session_id)["items"]) == 2
    asyncio.run(run())


def test_clarification_limit_remains_enforced(service):
    service.provider.outputs = [ASK]
    async def run():
        first = await service.create("合成输入")
        result = await service.continue_with_answer(first.id, "合成答案")
        assert result.status == "failed" and result.error_code == "clarification_limit_exceeded"
    asyncio.run(run())


def test_context_budget_drops_whole_old_turns_and_retains_current(service):
    service.settings.context_max_chars = 1000
    groups = [[{"role":"user","content": str(i)*200},
               {"role":"assistant","content":"a"*200}] for i in range(4)]
    selected = service._bounded_history(groups, {"original_input":"current"})
    assert len(selected) == 4
    assert selected[0]["content"] == "2"*200 and selected[2]["content"] == "3"*200
    service.settings.context_max_turns = 1
    assert service._bounded_history(groups, {}) == groups[-1]
    with pytest.raises(BusinessError) as error:
        service._bounded_history(groups, {"original_input":"x"*1001})
    assert error.value.code == "context_too_long"


def test_oversize_context_fails_before_guard_and_model(service):
    service.settings.context_max_chars = 1000
    result = asyncio.run(service.create("x"*1001))
    assert result.status == "failed" and result.error_code == "context_too_long"
    assert not service.provider.calls and not service.guard.inputs


def test_reopen_database_recovers_session(service):
    first = asyncio.run(service.create("合成持久化信息"))
    url = str(service.database.engine.url)
    service.database.close()
    reopened = Database(url)
    try:
        reopened.init_db()
        service.database = reopened
        second = asyncio.run(service.create("合成后续", session_id=first.session_id))
        assert second.turn_index == 1
        assert service.provider.calls[-1][0]["history"][0]["content"] == "合成持久化信息"
    finally:
        reopened.close()


def test_same_session_requests_serialize_without_losing_turns(service):
    async def run():
        first = await service.create("first")
        results = await asyncio.gather(*(service.create(str(i),session_id=first.session_id) for i in range(3)))
        assert [r.turn_index for r in results] == [1,2,3]
        assert [len(c[0]["history"]) for c in service.provider.calls] == [0,2,4,6]
        assert not service._session_locks
    asyncio.run(run())


@pytest.mark.parametrize("clarify", [False, True])
def test_cross_instance_stale_write_reports_conflict(service, clarify):
    async def run():
        service.provider.outputs = [ASK if clarify else DECISION]
        first = await service.create("first")
        ready = 0
        barrier = asyncio.Event()
        class RacingProvider(Provider):
            async def complete(self, *args, **kwargs):
                nonlocal ready
                ready += 1
                if ready == 2:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), 2)
                return deepcopy(DECISION)
        instances = [AnalysisService(database=service.database, provider=RacingProvider(),
                     guard=Guard(),settings=service.settings) for _ in range(2)]
        tasks = [s.continue_with_answer(first.id, f"answer-{i}") if clarify else
                 s.create(f"next-{i}",session_id=first.session_id) for i,s in enumerate(instances)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(not isinstance(r,Exception) for r in results) == 1
        conflict = next(r for r in results if isinstance(r,Exception))
        assert isinstance(conflict,BusinessError) and conflict.code == "session_conflict"
        records = service.session_history(first.session_id)["items"]
        assert len(records) == (1 if clarify else 2)
        if clarify:
            assert records[0].clarification_answer in {"answer-0","answer-1"}
    asyncio.run(run())


def test_additive_migration_is_idempotent_and_preserves_legacy_fields(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'legacy.db'}")
    with database.engine.begin() as connection:
        connection.execute(text("""CREATE TABLE analyses (
            id TEXT PRIMARY KEY, original_input TEXT NOT NULL, clarification_question TEXT,
            clarification_answer TEXT, result_json TEXT, status TEXT NOT NULL,
            input_safety_level TEXT, input_safety_categories_json TEXT NOT NULL DEFAULT '[]',
            error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""))
        connection.execute(text("INSERT INTO analyses (id,original_input,status,created_at,updated_at)"
                                " VALUES (:id,'legacy','failed','old-created','old-updated')"), {"id":"a"*32})
    try:
        database.init_db()
        database.init_db()
        row = database.get("a"*32)
        assert row["session_id"] == row["id"] and row["turn_index"] == 0
        assert row["original_input"] == "legacy" and row["updated_at"] == "old-updated"
        assert row["reasoning_effort"] == "medium"
    finally:
        database.close()


def test_session_pages_have_no_duplicates(service):
    first = service.database.create("0","completed",result=DECISION["result"])
    for i in range(1,105):
        service.database.create(str(i),"completed",result=DECISION["result"],session_id=first["session_id"])
    page = service.session_history(first["session_id"])
    assert len(page["items"]) == 100 and page["next_before"] == 5
    older = service.session_history(first["session_id"],before=page["next_before"])
    assert [i.turn_index for i in older["items"] + page["items"]] == list(range(105))
    assert older["next_before"] is None


def test_http_json_sse_restore_and_error_semantics(service):
    app = create_app(service.settings)
    app.state.service, app.state.provider = service, service.provider
    # No lifespan: only the supplied temporary SQLite and simulated dependencies are used.
    client = TestClient(app)
    first = client.post("/api/analyses",json={"text":"first"})
    assert first.status_code == 200 and first.headers["X-Request-ID"]
    body = first.json()
    streamed = client.post("/api/analyses/stream",json={"text":"second","session_id":body["session_id"]})
    frames = {frame.splitlines()[0]: json.loads(frame.splitlines()[1].removeprefix("data: "))
              for frame in streamed.text.strip().split("\n\n")}
    assert frames["event: complete"]["turn_index"] == 1
    records = client.get(f"/api/sessions/{body['session_id']}").json()["items"]
    assert [r["original_input"] for r in records] == ["first","second"]
    sessions = client.get("/api/sessions").json()["items"]
    assert len(sessions) == 1 and sessions[0]["session_id"] == body["session_id"]
    assert client.patch(f"/api/sessions/{body['session_id']}",json={"pinned":True}).json()["pinned"] is True
    assert client.patch(f"/api/sessions/{body['session_id']}",json={"archived":True}).json()["archived"] is True
    assert client.get("/api/sessions").json()["items"][0]["archived"] is True
    assert client.patch(f"/api/sessions/{body['session_id']}",json={"archived":False}).json()["archived"] is False
    assert client.post("/api/analyses",json={"text":"x","session_id":"f"*32}).status_code == 404
    assert client.post("/api/analyses",json={"text":"x","session_id":"bad"}).status_code == 422
    assert client.get("/api/sessions/bad").status_code == 422
    assert client.get(f"/api/sessions/{'f'*32}").status_code == 404
    assert client.delete(f"/api/sessions/{body['session_id']}").status_code == 204
    assert client.get(f"/api/sessions/{body['session_id']}").status_code == 404
    service.guard = Guard(level="Unsafe",categories=["Suicide & Self-Harm"])
    blocked = client.post("/api/analyses/stream",json={"text":"synthetic unsafe"})
    assert json.loads(blocked.text.strip().split("data: ")[-1])["status"] == "blocked"
    assert "event: delta" not in blocked.text


def test_persistence_failure_cannot_return_completed(service,monkeypatch):
    def fail(*args,**kwargs):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(service.database,"create",fail)
    app = create_app(service.settings)
    app.state.service = service
    client = TestClient(app,raise_server_exceptions=False)
    result = client.post("/api/analyses",json={"text":"synthetic"})
    assert result.status_code == 500 and result.json() == {"error":"internal_error"}
    stream = client.post("/api/analyses/stream",json={"text":"synthetic"})
    assert "event: error" in stream.text and "event: complete" not in stream.text
