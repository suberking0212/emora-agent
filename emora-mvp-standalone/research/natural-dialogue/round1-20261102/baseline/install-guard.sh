#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"
MODEL="${1:-Qwen/Qwen3Guard-Gen-0.6B}"
case "$MODEL" in
  Qwen/Qwen3Guard-Gen-0.6B|Qwen/Qwen3Guard-Gen-4B|Qwen/Qwen3Guard-Gen-8B) ;;
  *) printf 'Unsupported Qwen3Guard checkpoint\n' >&2; exit 1 ;;
esac
uv venv --allow-existing .guard-venv --python .venv/bin/python
uv pip install --python .guard-venv/bin/python -r scripts/guard-requirements.txt
HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HUB_DISABLE_XET=1 .guard-venv/bin/python - "$MODEL" <<'PY'
from pathlib import Path
import os
import sys
model = sys.argv[1]
directory = Path('.guard-models') / model.rsplit('/', 1)[-1]
if os.environ.get('EMORA_GUARD_DOWNLOAD_SOURCE', 'huggingface') == 'modelscope':
    from modelscope import snapshot_download
    snapshot_download(model, local_dir=str(directory))
else:
    from huggingface_hub import snapshot_download
    snapshot_download(model, local_dir=directory,
                      allow_patterns=['*.json', '*.safetensors', '*.txt', '*.jinja', 'LICENSE', 'README.md'])
PY
# Update only Guard fields after a successful download; preserve credentials.
.venv/bin/python - "$MODEL" <<'PY'
from dotenv import set_key
from pathlib import Path
import os
import sys
path = Path('.env')
for key, value in {
    'EMORA_QWEN_GUARD_BASE_URL': 'http://127.0.0.1:8001/v1',
    'EMORA_QWEN_GUARD_API_KEY': 'EMPTY',
    'EMORA_QWEN_GUARD_MODEL': sys.argv[1],
}.items():
    set_key(path, key, value)
os.chmod(path, 0o600)
PY
printf 'Guard installed. Run ./start.sh to start Guard and Web in this terminal.\n'
