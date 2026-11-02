"""Compare real validation contracts, not model quality; no credentials or network."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
code = '''
import json,sys
sys.path.insert(0,sys.argv[1])
from backend.contracts import AgentDecision
payload = {"reply":"我先听你说。"}
try:
    AgentDecision.model_validate(payload)
    accepted=True
except ValueError:
    accepted=False
print(json.dumps({"reply_only_accepted":accepted,"schema":AgentDecision.model_json_schema()},ensure_ascii=False))
'''
comparison = {}
for arm in ["baseline", "candidate"]:
    comparison[arm] = json.loads(subprocess.check_output([sys.executable,"-c",code,str(ROOT/arm)],text=True))
assert not comparison["baseline"]["reply_only_accepted"]
assert comparison["candidate"]["reply_only_accepted"]
comparison["quality_verdict"] = "not_measured_no_real_model_calls"
(ROOT / "validation/offline-contract-comparison.json").write_text(json.dumps(comparison,ensure_ascii=False,indent=2))
print(json.dumps({arm:comparison[arm]["reply_only_accepted"] for arm in ["baseline","candidate"]}))
