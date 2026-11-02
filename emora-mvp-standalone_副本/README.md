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

The script stops the previous project stack, starts local Guard and waits for model readiness, then starts Web and its same-origin frontend. Both services print logs to the current terminal; log files are `backend/data/guard.log` and `backend/data/server.log`. It verifies the real server PIDs own their fixed ports and pass health checks, without automatically switching ports or killing unrelated applications. Press `Ctrl+C` to stop both, or use `./start.sh --stop` from another terminal. Set `EMORA_HOST` or `EMORA_PORT` to override Web defaults. An external Guard URL is not launched or stopped locally. `./start-guard.sh` remains available for independent Guard troubleshooting.

The SQLite database defaults to `backend/data/emora.db`. Run infrastructure checks with `uv run pytest`. Tests only check startup and connectivity; they do not establish product quality or safety coverage. Select a Qwen3Guard checkpoint that fits the deployment machine.

The analysis provider uses native `stream=true` Chat Completions and aggregates each structured response on the server. The browser receives SSE progress events during the full request. In deep-thinking mode, the model first prepares a concise, user-facing analysis summary; the summary passes the output safety check before it is streamed to the chat and used as context for the final answer. It is a public summary, not private chain-of-thought. The final answer is sent only after validation, output safety checking, and persistence, so answer text waits for that gate. This app currently invokes no agent skills or tools, and the existing JSON endpoints remain available for scripts.

The composer includes a default-off deep-thinking option and a native reasoning-effort selector. The runtime endpoint reads the current model name from the provider and advertises all supported effort values (`none`, `low`, `medium`, `high`, `xhigh`, `max`). The selected value is sent on both analysis and clarification requests, passed to every provider completion, and persisted with the analysis record; older SQLite rows default to `medium`. Deep mode adds a separate analysis-summary generation pass and a more careful final-answer instruction with the documented `max_completion_tokens` budget. The app never returns private chain-of-thought.

See [`doc/OPERATIONS.md`](doc/OPERATIONS.md) for the Chinese startup/configuration guide and troubleshooting table.
