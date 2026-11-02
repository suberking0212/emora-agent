# Emora Standalone 操作手册

本文适用于 `emora-mvp-standalone/`。Emora Web 后端默认监听 `127.0.0.1:8000`，并由 FastAPI 同源提供 `frontend/` 页面和静态资源；前端没有独立开发服务器。Qwen3Guard 是单独运行的模型服务，默认使用 `127.0.0.1:8001`，避免与 Web 服务冲突。

## 首次安装

在仓库根目录执行：

```bash
cd emora-mvp-standalone
uv sync
cp .env.example .env
```

编辑本地 `.env`，至少设置分析模型的 `EMORA_MODEL`、`EMORA_API_KEY` 和 `EMORA_API_BASE`。该文件含凭据，已加入 Git 忽略规则；不要把密钥粘贴到终端命令、工单或日志中。

常用安全编辑方式：

```bash
${EDITOR:-nano} .env
```

配置 Ruoli/其他 OpenAI 兼容 endpoint 的例子如下。将密钥直接填入本地 `.env`，不要替换或提交 `.env.example` 中的空值：

```dotenv
EMORA_MODEL=gpt-5.6-terra
EMORA_API_BASE=https://ruoli.dev/v1
EMORA_API_KEY=本机API密钥
```

`EMORA_API_BASE` 应是 API 根路径，通常以 `/v1` 结尾；不能填完整的 `/chat/completions` 路径。

分析模型请求使用服务商兼容接口的原生 `stream=true`。浏览器通过同源 SSE 接口接收阶段状态和文本分块。深度模式的分析提要在完整生成并通过输出 Guard 后先显示，同时后端继续生成正式答复；正式答复仍在结构校验、输出 Guard 和数据库提交完成后发送，避免未经安全检查的模型片段直接出现在聊天区。原有 JSON API 仍保留供脚本调用。

输入框上方的“深度思考”默认关闭；旁边的“思考强度”从 `/api/runtime` 读取当前 Provider 模型支持的 `none`、`low`、`medium`、`high`、`xhigh`、`max` 档位。选中的值随新分析或澄清请求发送，后端会将它传给每次 Provider completion，并在分析记录中保存；旧 SQLite 记录迁移时默认使用 `medium`。开启深度思考后，后端先让模型生成简短、面向用户的“分析提要”，通过 Qwen 输出安全检查后以 SSE 显示，并把它作为正式答复的上下文；随后生成正式答复，要求区分事实与假设、分析相关因素的相互影响，并给出更具体且有优先级的建议，同时将 `max_completion_tokens` 设为 4096。分析提要不是模型私有思维链。当前中转接口是否将档位继续透传至上游并改变推理资源，仍需中转服务端证据。模式会保存在该条分析记录中，澄清回答默认沿用，也可在提交回答时切换；重开历史记录时会恢复记录的模式。该模式会增加生成耗时和 API 用量，不改变最终答复的输出 Guard、结果校验及保存顺序。

## 启动、重启和停止

在 standalone 目录启动或重启 Web 应用：

```bash
./start.sh
```

脚本会先清理该 standalone 目录对应的旧启动管理进程、Web 和本地 Guard 进程及其子进程，先发送 TERM，等待最多 6 秒，仍未退出时发送 KILL 并再等待 3 秒。兼容旧脚本保存的 shell PID，也能通过命令与工作目录识别没有 PID 文件的手动启动进程。服务 PID 文件记录实际 Uvicorn PID，`.emora-stack.pid` 另记总启动管理进程。包含空格的项目路径也受支持。

脚本先启动本地 Guard，等待模型加载完成及健康检查成功，再启动 Web；Web 同源提供前端。必须同时确认新 PID 正在监听指定端口和 `/health` 返回 200，才打印 `ready`。两项服务的日志实时显示在当前终端，同时分别保存到 `backend/data/guard.log` 和 `backend/data/server.log`。Web 默认地址是 <http://127.0.0.1:8000/>，不会自动切换到 8001 或其他端口。Guard 未安装或启动失败时 Web 不会继续启动。并发启动通过文件锁串行处理。

启动成功后当前终端会保持被服务占用，这是预期行为；按 `Ctrl+C` 同时停止本次启动的 Guard 和 Web 并返回 shell 提示符。任一服务异常退出时，管理进程停止另一项服务并报告失败。需要继续使用该终端时，可另开一个终端窗口运行其他命令。

查看健康状态和页面响应：

```bash
curl -fsS http://127.0.0.1:8000/health
curl -I http://127.0.0.1:8000/
```

查看后端日志：

```bash
tail -n 100 backend/data/server.log
tail -f backend/data/server.log
```

停止当前应用：

```bash
./start.sh --stop
```

下次运行 `./start.sh` 会先报告并清理残留的 standalone 服务进程。应用数据库在 `backend/data/emora.db`；停止服务或查看日志不会删除历史记录。

更改 Web 监听地址或端口后，使用相同变量运行启动脚本：

```bash
EMORA_HOST=0.0.0.0 EMORA_PORT=8080 ./start.sh
```

`EMORA_HOST=0.0.0.0` 会让局域网接口也能访问服务；仅在确有需要时使用，并自行配置网络访问控制。端口被其他应用占用时，脚本报告占用 PID 并失败，不杀其他应用、不自动换端口。可先检查 `lsof -nP -iTCP:8000 -sTCP:LISTEN`；确认需要更改端口时才显式设置 `EMORA_PORT=8080`。Uvicorn 访问日志里的 `127.0.0.1:62xxx` 是浏览器连接的临时源端口，不是 Web 服务监听端口。

## Qwen3Guard 服务

### Apple Silicon Mac 本地安装

Qwen3Guard 官方提供 Apache-2.0 许可的开放权重。本地推理不调用付费模型 API，使用自己的内存、磁盘与电力。远程推理平台和云主机的费用由平台决定，分析模型的 API 费用仍单独计算。

本机使用独立 `.guard-venv` 安装 PyTorch/Transformers，运行官方 tokenizer chat template 和模型 `generate`，优先使用 Apple MPS，无法使用 MPS 时使用 CPU。Web 的 Python 环境不安装这些大型依赖；不自建安全分类规则。默认先部署较小的官方 0.6B checkpoint，4B/8B 也可通过安装命令显式选择；尺寸不同不代表已经验证产品场景中的安全效果。

```bash
./install-guard.sh
# 如 Hugging Face 下载不通，使用 ModelScope 的 Qwen 仓库：
EMORA_GUARD_DOWNLOAD_SOURCE=modelscope ./install-guard.sh
# 显式选用 4B（下载更大，加载和推理需要更多资源）：
EMORA_GUARD_DOWNLOAD_SOURCE=modelscope ./install-guard.sh Qwen/Qwen3Guard-Gen-4B
```

安装成功后脚本只更新 `.env` 的三个 Guard 字段，保留分析模型凭据，并将 `.env` 权限设为 600。权重保存在 `.guard-models/`，不提交 Git。运行时只从本地权重目录加载，不再下载：

```bash
# 一个终端：自动先启动安全模型，再启动 Web 和同源前端
./start.sh
```

当前终端实时输出两项服务日志。`./start.sh --stop` 停止整个本地应用。需要独立排查 Guard 时可以使用 `./start-guard.sh` 和 `./start-guard.sh --stop`；已由总启动脚本管理时，不要独立重启它的子服务，否则总管理进程会按服务异常退出处理并停止另一项服务。

本机于 2026-10-30 完成官方 0.6B 权重下载及 MPS 加载验证，Guard 输入和输出协议均已通过现有客户端实际调用；Web 与 Guard 独立重启后旧 PID 消失，各固定端口仅有一个监听进程。基础设施检查共 11 项通过。上述结果不等同于情感场景安全效果或真实用户体验验证。

### 已有 vLLM 环境

也可继续使用外部 vLLM 服务，配置中的模型 ID 必须与加载的 checkpoint 一致。此方式不需要安装本项目的 Transformers 服务。示例使用 `8001`，与 Emora Web 默认端口 `8000` 分开：

```bash
vllm serve Qwen/Qwen3Guard-Gen-4B --host 127.0.0.1 --port 8001 --max-model-len 32768
```

在 `.env` 中对应设置：

```dotenv
EMORA_QWEN_GUARD_BASE_URL=http://127.0.0.1:8001/v1
EMORA_QWEN_GUARD_API_KEY=EMPTY
EMORA_QWEN_GUARD_MODEL=Qwen/Qwen3Guard-Gen-4B
```

如果改用官方 `0.6B` 或 `8B` 检查点，启动命令和 `EMORA_QWEN_GUARD_MODEL` 要一起改。重新启动 Qwen 服务后确认其模型目录可达：

```bash
curl -fsS http://127.0.0.1:8001/v1/models
```

Web 与 Qwen 是两个进程，两个固定端口。`./start.sh` 对本机 `http://127.0.0.1` 或 `http://localhost` Guard 地址自动管理本地模型；配置外部 Guard URL 时仅启动 Web，不启动或终止远程服务。Guard 客户端访问 loopback 地址时不使用环境或系统代理。Guard 的 OpenAI 兼容接口仅实现 `/v1/models` 和非流式 `/v1/chat/completions` 所需字段，审查文本直接返回模型输出。Qwen 服务不可用时，Web 服务会拒绝完成分析，不会跳过安全检查。

## 配置变量

| 变量 | 用途 | 默认值/说明 |
|---|---|---|
| `EMORA_MODEL` | 分析模型 ID | 必填；应在 Provider 模型目录中存在 |
| `EMORA_API_KEY` | 分析 endpoint 凭据 | 必填；只放在本机 `.env` |
| `EMORA_API_BASE` | OpenAI 兼容 API 根 URL | 例如 `https://ruoli.dev/v1` |
| `EMORA_DATABASE_URL` | SQLite URL | `sqlite:///backend/data/emora.db` |
| `EMORA_REQUEST_TIMEOUT_SECONDS` | 分析模型请求超时 | `90` |
| `EMORA_MAX_INPUT_LENGTH` | 输入长度上限 | `10000`，不能超过契约上限 |
| `EMORA_QWEN_GUARD_BASE_URL` | Qwen3Guard API 根 URL | 推荐本机 `http://127.0.0.1:8001/v1` |
| `EMORA_QWEN_GUARD_API_KEY` | Qwen endpoint 凭据 | 本机 vLLM 默认 `EMPTY` |
| `EMORA_QWEN_GUARD_MODEL` | Qwen3Guard checkpoint ID | 默认 `Qwen/Qwen3Guard-Gen-4B` |
| `EMORA_QWEN_GUARD_TIMEOUT_SECONDS` | Qwen 分类请求超时 | `15` |
| `EMORA_HOST` | Web 监听地址 | 启动脚本默认 `127.0.0.1` |
| `EMORA_PORT` | Web 监听端口 | 启动脚本默认 `8000` |

修改 `.env` 后需要重启 Web：

```bash
./start.sh
```

环境变量也可仅对一次启动生效，例如 `EMORA_PORT=8080 ./start.sh`。不要用 `export EMORA_API_KEY=...` 将真实密钥留在 shell 历史中。

## 连通性与基础检查

检查已配置分析模型 endpoint 的鉴权和网络连通性，只请求模型目录，不发送用户内容：

```bash
uv run python -c 'from backend.config import Settings; from openai import OpenAI; s=Settings(); c=OpenAI(base_url=s.api_base, api_key=s.api_key.get_secret_value(), timeout=15, max_retries=0); models=list(c.models.list()); print("endpoint reachable; models:", ", ".join(m.id for m in models)); c.close()'
```

执行项目基础设施测试：

```bash
uv run pytest
```

测试只核查应用/静态资源、SQLite、LangGraph 编译和可选 endpoint 连通性；未配置真实 endpoint 的连通测试会跳过。

## 常见错误对照

| 现象/错误码 | 常见原因 | 处理方法 |
|---|---|---|
| `./start.sh` 提示 `Backend failed to start` | `.env` 解析错误、依赖未安装、端口占用或应用导入失败 | 查看 `tail -n 100 backend/data/server.log`；运行 `uv sync`；检查 `EMORA_PORT` 是否可用 |
| `Backend did not become healthy` | Uvicorn 进程仍在启动、启动异常，或健康端口与脚本地址不一致 | 查看日志；确认启动脚本中的 `EMORA_HOST`/`EMORA_PORT`；本机检查 `curl http://127.0.0.1:8000/health` |
| `Port ... is occupied by unrelated PID(s)` | 指定端口被其他应用占用 | 用 `lsof -nP -iTCP:8000 -sTCP:LISTEN` 检查所属服务；确认后停止该服务，或显式设置 `EMORA_PORT`；脚本不会自动换端口 |
| `Could not stop old processes` | 同项目进程收到 TERM/KILL 后仍未退出或权限不足 | 检查输出 PID 的用户与进程状态；脚本会停止启动，避免把旧服务当作新服务成功 |
| `Guard not installed` | 本地模型未完成安装或 checkpoint 配置与目录不匹配 | 完整运行 `./install-guard.sh`；模型下载失败可改用 ModelScope 命令重试 |
| 下载 `Timeout` / `LocalEntryNotFoundError` | 模型仓库不可达或网络代理阻断元数据请求 | 运行 `EMORA_GUARD_DOWNLOAD_SOURCE=modelscope ./install-guard.sh`；成功前不启动 Guard |
| HTTP `422` / `input_too_long` | 输入为空、字段格式不正确或超过运行长度上限 | 提交非空文本；检查 `EMORA_MAX_INPUT_LENGTH`，最大不能超过 10,000 |
| 历史记录显示 `failed`，错误 `safety_check_unavailable` | Qwen3Guard 未启动、URL/端口错误、超时或响应不符合协议 | 检查 Qwen 进程和 `/v1/models`；核对 Guard URL、checkpoint 与 vLLM 启动参数；查看 Web 日志中的错误类型 |
| 错误 `analysis_unavailable` | 分析模型 endpoint 不可达、鉴权失败、模型 ID 错误或结构化响应不兼容 | 检查 `EMORA_API_BASE`、`EMORA_MODEL`、密钥是否有效；用上面的模型目录连通性命令确认模型 ID |
| Guard POST 已返回 `200`，之后 `analysis_unavailable` | 失败已经进入分析 Provider，不能继续归因于 Guard | 查看 `provider_request_failed` 的 `error_type`、`cause_type` 和 HTTP `status`；`APITimeoutError` 表示超时，4xx 表示上游拒绝，`ValidationError` 表示返回结构校验失败；模型目录成功不代表生成接口成功 |
| `JSONDecodeError` 或旧版 SDK `ValidationError/json_invalid` | 上游回复不是合法 JSON，或网关不支持原生 JSON Schema 输出 | 当前适配器只发普通对话请求，并在 system 提示中给出 JSON Schema；更新后重启。仍返回非法 JSON 时按失败处理，不跳过结构或安全校验 |
| `APITimeoutError` / `ReadTimeout` | 上游在请求超时窗口内没有返回完整响应；不能仅凭此认定代理问题 | 本机已观察到生成请求约 30.5 秒才返回，而旧配置为 30 秒；当前默认及本机配置改为 `EMORA_REQUEST_TIMEOUT_SECONDS=90`，修改后运行 `./start.sh` 重启。结合 `provider_request_completed/failed` 的 `duration_ms` 排查；仍超时则保留错误并检查上游，不无限重试 |
| 错误 `output_rejected` | 输出安全检查返回 `Controversial` 或 `Unsafe` | 候选结果不会展示或保存为完成；可稍后重新描述需求，不要试图绕开安全检查 |
| 错误 `unsafe_input` 或状态 `blocked` | Qwen3Guard 将输入分类为 `Unsafe` | 系统会停止普通分析；这只是内容分类，不等同于对用户即时危险的判断 |
| 错误 `unsafe_input_seek_support` | `Unsafe` 同时带有 Suicide & Self-Harm 或 Violent 官方类别 | 页面显示谨慎的现实世界求助方向；分类不代表已确认个人意图或即时危险 |
| 错误 `clarification_limit_exceeded` | 唯一一轮补充后模型仍要求继续澄清 | 当前流程停止并标记失败；不能依靠重复提交形成多轮追问 |
| HTTP `404` / `analysis_not_found` | 历史记录 ID 不存在 | 刷新历史列表并打开现存记录 |
| HTTP `422` / `analysis_not_waiting_clarification` | 对不处于等待澄清状态的记录提交了回答 | 新开对话或选择正确的等待澄清记录 |
| SQLite `unable to open database file` | 数据目录不存在或当前用户没有写权限 | 从 standalone 目录启动；确认 `backend/data/` 可写；检查 `EMORA_DATABASE_URL` |
| endpoint 报 `model not found` | 配置模型 ID 不在 endpoint 模型列表中 | 查询 `GET /v1/models` 或运行模型目录连通性命令，并将精确 ID 写入 `.env` |
| 聊天区没有增量显示 | 浏览器缓存了旧静态脚本、SSE 被反向代理缓冲或后端仍是旧进程 | 重启 `./start.sh` 并硬刷新页面；确认 `/api/analyses/stream` 返回 `text/event-stream`，代理需关闭响应缓冲 |

日志不会记录用户输入、模型提示词/回复或 API Key。排查时不要打开调试级别去记录这些内容；如需报告问题，只分享脱敏后的错误码、HTTP 状态和异常类型。
