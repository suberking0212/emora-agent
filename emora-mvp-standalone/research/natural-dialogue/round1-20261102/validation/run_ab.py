"""Controlled baseline/candidate comparison. Default is an OFFLINE plan only.

Live mode is for use ONLY after human paid-call authorization; the flag itself
is not evidence of authorization. Never run live in unattended tests.
"""
import argparse
import asyncio
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "validation/ab_cases.json").read_text())


def worker(arm, case, env_file, effort):
    sys.path.insert(0, str(ROOT / arm))
    from backend.config import Settings
    from backend.provider import ProviderAdapter, provider_metrics_var
    from backend.safety import Qwen3GuardClient
    from backend.services import AnalysisService
    from backend.storage import Database
    with tempfile.TemporaryDirectory(prefix="emora-ab-") as tmp:
        settings = Settings(_env_file=env_file, database_url=f"sqlite:///{tmp}/ab.db")
        if not settings.model or not settings.api_base or settings.api_key.get_secret_value() == "EMPTY":
            raise ValueError("live_configuration_missing")
        provider = ProviderAdapter(model=settings.model, api_key=settings.api_key.get_secret_value(),
            api_base=settings.api_base, timeout_seconds=settings.request_timeout_seconds,
            response_format=settings.response_format)
        assert provider.client.max_retries == 0
        guard = Qwen3GuardClient(base_url=settings.qwen_guard_base_url,
            api_key=settings.qwen_guard_api_key.get_secret_value(), model_id=settings.qwen_guard_model,
            timeout_seconds=settings.qwen_guard_timeout_seconds)
        db = Database(settings.database_url)
        db.init_db()
        service = AnalysisService(database=db, provider=provider, guard=guard, settings=settings)
        async def run():
            metrics = []
            token = provider_metrics_var.set(metrics)
            records, previous = [], None
            try:
                for text in case["turns"]:
                    deltas = []
                    if previous and previous.status.value == "waiting_clarification":
                        response = await service.continue_with_answer(previous.id, text,
                            reasoning_effort=effort, answer_delta=deltas.append)
                    else:
                        response = await service.create(text,
                            session_id=previous.session_id if previous else None,
                            reasoning_effort=effort, answer_delta=deltas.append)
                    records.append({"user":text, "response":response.model_dump(mode="json"),
                                    "safe_stream_text":"".join(deltas)})
                    previous = response
                return {"arm":arm, "case_id":case["id"], "requested_model":settings.model,
                    "effort":effort, "response_format":settings.response_format,
                    "records":records, "provider_metrics":metrics,
                    "title_calls":0, "retries":0}
            finally:
                provider_metrics_var.reset(token)
                await provider.close()
                await guard.close()
        try:
            return asyncio.run(run())
        finally:
            db.close()


def visible(record):
    response = record["response"]
    if response["status"] == "waiting_clarification":
        return response["clarification_question"]
    result = response.get("result")
    if result and "reply" in result:
        return result["reply"]
    if result:
        labels = [("情境概括", result["summary"]), ("主要情绪", "、".join(result["emotions"])),
                  ("核心矛盾", result["conflict"]), ("你可能在意的", result["user_needs"]),
                  ("可以尝试", "\n".join(f"{i+1}. {v}" for i,v in enumerate(result["suggestions"]))),
                  ("下一步", result["next_step"]), ("安全说明", result.get("safety_note"))]
        return "\n\n".join(f"### {label}\n\n{text}" for label,text in labels if text)
    if response["status"] == "blocked":
        return ("我很关心你现在的处境。如果你或他人可能正面临现实危险，请联系当地急救服务、危机热线或可信任的人。"
                if response["error_code"] == "unsafe_input_seek_support" else "这段内容目前无法继续进行普通对话。")
    return "这次暂时无法完成。你的内容仍保留在当前对话中，可以稍后重新开始。"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorized-live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--effort", choices=["none","low","medium","high","xhigh","max"], default="medium")
    parser.add_argument("--worker", choices=["baseline","candidate"], help=argparse.SUPPRESS)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    args = parser.parse_args()
    assert len(CASES) == 10 and sum(len(c["turns"]) for c in CASES) == 14
    if not args.authorized_live:
        print(json.dumps({"mode":"offline_plan_only", "credentials_read":False, "network_calls":0,
            "cases":len(CASES), "turns_per_arm":14, "max_paid_generations":28,
            "quality_claim":"unverified"}, ensure_ascii=False, indent=2))
        return
    if args.env_file is None or not args.env_file.is_absolute() or not args.env_file.is_file():
        parser.error("live mode requires an explicit absolute --env-file")
    if args.worker:
        case = next(c for c in CASES if c["id"] == args.case)
        try:
            print(json.dumps(worker(args.worker,case,str(args.env_file),args.effort),ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"error_type":type(exc).__name__}))
            sys.exit(1)
        return
    output = ROOT / "validation/live-results"
    output.mkdir(exist_ok=False)  # prevents accidental duplicate paid runs
    rng = random.Random(20261102)
    blind, mapping, configurations = [], [], []
    for case in CASES:
        arms = ["baseline", "candidate"]
        rng.shuffle(arms)
        for label, arm in zip(["X", "Y"], arms):
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                "--authorized-live", "--env-file", str(args.env_file), "--effort", args.effort,
                "--worker", arm, "--case", case["id"]], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"{case['id']}/{arm}: stopped; no automatic retries")
            payload = json.loads(result.stdout)
            configurations.append((payload["requested_model"], payload["effort"], payload["response_format"]))
            if len(set(configurations)) != 1:
                raise RuntimeError("configuration_changed_during_comparison")
            (output / f"{case['id']}-{arm}.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2))
            blind.append({"case_id":case["id"], "label":label, "rubric":case["rubric"],
                "turns":[{"user":r["user"],"status":r["response"]["status"],"reply":visible(r)} for r in payload["records"]]})
            mapping.append({"case_id":case["id"],"label":label,"arm":arm})
            (output / "blind-review.json").write_text(json.dumps(blind,ensure_ascii=False,indent=2))
            (output / "review-key.json").write_text(json.dumps(mapping,ensure_ascii=False,indent=2))
            print(json.dumps({"case":case["id"],"arm":arm,"statuses":[r["response"]["status"] for r in payload["records"]],"generation_attempts":len(payload["provider_metrics"])},ensure_ascii=False),flush=True)
    print("A/B generated; quality verdict requires independent human review. No titles or retries.")


if __name__ == "__main__":
    main()
