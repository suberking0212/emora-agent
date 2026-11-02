"""Local UI fixture: no real model/Guard/credentials; SQLite is temporary."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "candidate"))
from backend.app import create_app
from backend.config import Settings
from backend.provider import IncrementalJSONText
from backend.services import AnalysisService
from backend.storage import Database
from backend.tests.test_service_reliability import Guard, LEGACY_RESULT
import uvicorn

scratch = tempfile.TemporaryDirectory(prefix="emora-dialogue-ui-")
settings = Settings(_env_file=None, database_url=f"sqlite:///{scratch.name}/ui.db")
database = Database(settings.database_url)
database.init_db()
old = database.create("旧版合成记录", "completed", session_id="a"*32, result=LEGACY_RESULT)
database.set_session_title("a"*32, "验证：旧报告")
pending = database.create("旧版待澄清", "waiting_clarification", session_id="b"*32,
                          clarification_question="请补充时间？")
database.set_session_title("b"*32, "验证：旧待澄清")

class FixtureProvider:
    model = "OFFLINE-FIXTURE（非真实模型）"
    def __init__(self):
        self.calls = []
    async def complete(self, context, response_model, **kwargs):
        self.calls.append(context)
        text = context.get("clarification_answer") or context["original_input"]
        if "纠正" in text:
            reply = "是我理解偏了，你说的是同事之间的分工，不是伴侣关系。"
        elif "安全渲染" in text:
            reply = '**保留加粗** <img src=x onerror="window.__emoraXSS=1"><script>window.__emoraXSS=1</script> [危险链接](javascript:alert(1))'
        elif context.get("clarification_answer"):
            reply = "明白了，是周三的事情。你可以继续说。"
        elif context["history"]:
            reply = "当时的分工是怎么约定的？"
        else:
            reply = "你想先把这件事说出来，我们可以先不急着找办法。具体是哪一次？"
        parser = IncrementalJSONText()
        content = json.dumps({"reply": reply}, ensure_ascii=True)
        if callback := kwargs.get("on_text"):
            for i in range(0, len(content), 17):
                for event, path, value in parser.feed(content[i:i+17]):
                    await callback(path, event, value)
                await asyncio.sleep(0.002)
        return {"reply": reply}

provider = FixtureProvider()
guard = Guard()
service = AnalysisService(database=database, provider=provider, guard=guard, settings=settings)
app = create_app(settings)
app.state.service = service
app.state.provider = provider

@app.get("/__validation__/evidence")
async def evidence():
    return {"fixture_only": True, "calls": provider.calls,
            "guard_input_checks": len(guard.inputs), "guard_output_checks": len(guard.outputs)}

if __name__ == "__main__":
    try:
        uvicorn.run(app, host="127.0.0.1", port=18762, lifespan="off", log_level="warning")
    finally:
        database.close()
        scratch.cleanup()
