# Emora MVP Standalone

## Requirements

- Python 3.11+
- An OpenAI-compatible analysis model endpoint
- A local Qwen3Guard-Gen endpoint (bundled Transformers launcher for Mac, or external vLLM)

Install and configure:

```bash
cd emora-mvp-standalone
uv sync
cp .env.example .env
```

Set `EMORA_MODEL`, `EMORA_API_KEY`, and `EMORA_API_BASE` in `.env`. On Apple Silicon, install the official 0.6B weights once:

```bash
./install-guard.sh
```

Start or restart the app from this directory (the FastAPI backend also serves the separated frontend files):

```bash
./start.sh
```

The script stops the previous project stack, starts local Guard and waits for model readiness, then starts Web and its same-origin frontend. Guard is an internal safety service, not the browser entry point. After both services pass health checks, the terminal prints the Emora Web URL (default `http://127.0.0.1:8000/`). Both services print logs to the current terminal; log files are `backend/data/guard.log` and `backend/data/server.log`. It verifies the real server PIDs own their fixed ports, without automatically switching ports or killing unrelated applications. Press `Ctrl+C` to stop both, or use `./start.sh --stop` from another terminal. Set `EMORA_HOST` or `EMORA_PORT` to override Web defaults. An external Guard URL is not launched or stopped locally. `./start-guard.sh` remains available for independent Guard troubleshooting.

The SQLite database defaults to `backend/data/emora.db`. Run offline reliability checks with `uv run pytest`. Simulated tests cover engineering failure paths and safety boundaries; they do not establish clinical quality or the real Guard's general accuracy. Select a Qwen3Guard checkpoint that fits the deployment machine.

The analysis provider uses native `stream=true` Chat Completions. The server incrementally extracts user-facing text from streamed JSON fields. A separate ordered worker checks the accumulated prefix and releases a fragment after about 24 characters, a sentence boundary, or 300 ms, without pausing reads from the model stream. The complete response still must pass JSON/domain validation, a full output safety check, and persistence before it is confirmed. In deep-thinking mode, the public analysis summary streams while it is generated and then becomes context for the final answer. It is not private chain-of-thought. Provider and Guard logs record first-content/first-visible latency, chunk counts, and check duration without logging generated text, which helps distinguish upstream buffering from application-side delays. This app currently invokes no agent skills or tools, and the existing JSON endpoints remain available for scripts.

The composer includes a default-off deep-thinking option and a reasoning-effort selector (`none`, `low`, `medium`, `high`, `xhigh`, `max`). The selected value is sent on both analysis and clarification requests, passed to every provider completion, and persisted with the analysis record; older SQLite rows default to `medium`. The current gateway accepts these values, but their upstream effect is unverified (it also accepts an invalid value). `/api/runtime` reports this limitation. Deep mode adds a separate analysis-summary generation pass and retains the existing final-answer instruction and token budget. The app never returns private chain-of-thought.

Completed turns now belong to a persisted session. Follow-up requests send `session_id`; omitting it creates a new session. `GET /api/sessions/{session_id}` restores records in order, with `next_before` pagination. Model context retains the newest complete turns within `EMORA_CONTEXT_MAX_TURNS` (12) and `EMORA_CONTEXT_MAX_CHARS` (32000); failed and blocked turns remain in the transcript but are excluded from model history. This is an application character budget, excluding the system prompt and message framing, not a tokenizer or long-term memory.

`EMORA_RESPONSE_FORMAT=json_object` is the verified default for this gateway. Optional `json_schema` sends a strict wire Schema, but the current gateway did not enforce it in adversarial probes; application Pydantic validation remains mandatory. There is no automatic format fallback or JSON repair. Before first startup with the added session columns, back up the SQLite database; legacy records migrate as independent sessions. See [round-one remediation report](research/engineering-remediation-round1/整改报告.md).

Run `.venv/bin/python scripts/verify_engineering.py tests` for repeatable offline checks. Real network probes are explicit: `provider` tests gateway capabilities; `real` uses synthetic inputs, the configured Provider and Guard, and a disposable SQLite database. The evidence contains status, timing and reported usage rather than full conversations or credentials.

See [`doc/OPERATIONS.md`](doc/OPERATIONS.md) for the Chinese startup/configuration guide and troubleshooting table.
