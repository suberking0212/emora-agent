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

分析模型请求使用服务商兼容接口的原生 `stream=true`。Provider 逐个读取上游事件并增量提取用户可见的 JSON 字符串字段；独立的有序消费任务每约 24 个字符、遇到句末或等待 300ms 后，对累积输出前缀执行 Qwen 输出 Guard，通过后立即经同源 SSE 发送，不会让 Guard 请求暂停 Provider 读取模型流。正式回复会在模型生成期间逐段出现。Provider 日志记录首个内容事件耗时、事件数和字符数；Guard 日志记录首段可见文本耗时、检查次数与耗时，两者均不记录生成正文，可据此区分模型/中转站首字延迟与本机输出检查延迟。完整 JSON 仍须通过解析、领域校验、整段输出 Guard 和数据库提交；只有这些步骤全部成功，前端才收到完成状态。已显示的片段均已通过对应的前缀安全检查。原有 JSON API 仍保留供脚本调用。

输入区的“推理强度”从 `/api/runtime` 读取应用允许的 `none`、`low`、`medium`、`high`、`xhigh`、`max` 档位。这不是模型能力认证：当前网关接受这些值，也接受非法探针值；档位是否在上游生效尚未确认，接口以 `reasoning_effort_verification=accepted_effect_unverified` 标注。选中的值随新分析或澄清请求发送，后端会将它传给 Provider completion，并在分析记录中保存；旧 SQLite 记录迁移时默认使用 `medium`。

当前网关实测使用 `EMORA_RESPONSE_FORMAT=json_object`：请求端原生 JSON 模式能在对照探针中约束 JSON 格式，但不保证业务 Schema。可选的 `json_schema` 会发送严格 wire Schema；本轮 Chat 和 Responses 真实探针均未证明网关执行严格约束，因此不能宣称 Structured Outputs 已贯通。`text` 仅供显式诊断。应用始终解析完整文档并执行 Pydantic/领域校验；不提取前后杂文中的 JSON、不修复坏 JSON、不自动降级或重试。更换 endpoint 后需重新验证能力。

## 会话、上下文和数据库迁移

首次请求不传 `session_id`，系统创建会话；追问必须传回响应中的 `session_id`。`GET /api/sessions` 提供会话列表和置顶/归档状态；`GET /api/sessions/{session_id}` 按轮次恢复记录，`next_before` 用于读取更早页面。`PATCH /api/sessions/{session_id}` 更新置顶或归档状态，`DELETE /api/sessions/{session_id}` 删除会话及全部轮次。等待澄清时应使用该分析记录的 `/clarify` 接口，仍只允许原有一轮澄清。同会话分析操作在单进程串行执行，过期的跨实例写入返回 `session_conflict`，不覆盖已保存内容。

模型历史仅包含最近已完成轮次的用户内容、澄清问答及模型结果；失败/拦截轮次保留在界面记录中，不作为后续模型历史。默认保留最多 12 轮、32,000 应用字符，并优先删除完整旧轮次。字符预算包含当前轮完整上下文，不包含 System Prompt 及消息框架，不是模型 Token 上限。超限当前内容直接失败，避免无界拼接；不引入长期 Memory。

为恢复完成后的追问关联，`init_db` 会幂等增加 `session_id`、`turn_index` 两列和会话轮次唯一索引。旧记录以自身 `id` 作为独立会话，不推测旧记录之间的关系。修改前后的旧字段保持不变。本轮迁移测试和真实测试只用临时合成数据库，未迁移用户现有数据库。

首次用新版本启动前，先停止应用并备份实际 SQLite 数据库。默认路径可使用 SQLite backup API：

```bash
.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
source = Path('backend/data/emora.db')
target = source.with_name('emora.before-round1.db')
if not source.exists() or target.exists():
    raise SystemExit('源库不存在或备份已存在；请检查路径，不覆盖现有备份')
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
print(target)
PY
```

自定义数据库 URL 时相应调整路径。恢复旧版本时先停服务，再恢复备份；新增列本身兼容旧代码，无需破坏性删除列。

## 启动、重启和停止

在 standalone 目录启动或重启 Web 应用：

```bash
./start.sh
```

脚本会先清理该 standalone 目录对应的旧启动管理进程、Web 和本地 Guard 进程及其子进程，先发送 TERM，等待最多 6 秒，仍未退出时发送 KILL 并再等待 3 秒。兼容旧脚本保存的 shell PID，也能通过命令与工作目录识别没有 PID 文件的手动启动进程。服务 PID 文件记录实际 Uvicorn PID，`.emora-stack.pid` 另记总启动管理进程。包含空格的项目路径也受支持。

脚本先启动本地 Guard，等待模型加载完成及健康检查成功，再启动 Web；Web 同源提供前端。必须同时确认新 PID 正在监听指定端口和 `/health` 返回 200，才显示成功。Guard 只作为内部安全服务，启动提示不会将其端口作为网页入口；整套服务成功后会打印 `Emora Web is ready: <URL>`。Web 默认地址是 <http://127.0.0.1:8000/>，设置 `EMORA_PORT` 后成功提示会使用该端口，不会自动切换到 8001 或其他端口。Guard 未安装或启动失败时 Web 不会继续启动。并发启动通过文件锁串行处理。两项服务的日志实时显示在当前终端，同时分别保存到 `backend/data/guard.log` 和 `backend/data/server.log`。

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
| `EMORA_RESPONSE_FORMAT` | 请求端输出格式 | `json_object`；可选 `json_schema`、`text`，不自动降级 |
| `EMORA_CONTEXT_MAX_CHARS` | 历史与当前内容的应用字符预算 | `32000`；允许 1000–200000，不包含 System Prompt/消息框架 |
| `EMORA_CONTEXT_MAX_TURNS` | 最多保留的完整历史轮次 | `12`；允许 1–100 |
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

执行离线工程可靠性测试（不请求真实 Provider/Guard）：

```bash
.venv/bin/python scripts/verify_engineering.py tests
```

也可运行 `uv run pytest`。测试覆盖 Provider 请求与解析、Service/图/SQLite 集成、进程内 HTTP JSON/SSE、会话与安全回归及启动脚本；模型/Guard 替身不代表真实模型测试。两项可选 endpoint 连通检查默认跳过；分别通过 `EMORA_TEST_API_BASE`/`EMORA_TEST_API_KEY` 和 `EMORA_TEST_QWEN_GUARD_BASE_URL`/`EMORA_TEST_QWEN_GUARD_API_KEY` 显式配置，它们只检查模型目录，不读取 `.env` 中的运行凭据。

复核本轮受保护的 Prompt、业务 Schema、Renderer、安全分类及图结构，并生成相对修改前快照的源码差异：

```bash
.venv/bin/python scripts/verify_engineering.py audit
```

此检查依赖本轮保存的基线，只验证列出的代码边界及父仓库 Git 状态，不代替功能测试，也不单凭 Git 状态证明未跟踪文件内容完全一致。

显式执行真实网络验证（使用本机配置、合成输入，产生 API 用量）：

```bash
.venv/bin/python scripts/verify_engineering.py provider
.venv/bin/python scripts/probe_provider.py --alternatives
.venv/bin/python scripts/probe_provider.py --json-mode-checks
.venv/bin/python scripts/probe_provider.py --reasoning-comparison
.venv/bin/python scripts/verify_engineering.py real
```

`provider` 直接探测网关，不经过 Service/Guard；其他探针分别核对 Responses、JSON 模式和相同输入下推理档位。`real` 启动独立临时 TCP HTTP 服务，使用真实 Provider、已运行的真实 Qwen Guard 和临时 SQLite，核验纠正、追问、隔离、拦截及重启恢复；退出时停止该临时 Web 并删除合成库，不停止已有 Guard。证据写入 `research/engineering-remediation-round1/`，同名验证结果会被后续执行更新；修改前基线禁止覆盖。浏览器手动 QA 与自动化测试的范围另见该目录整改报告。

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
| `provider_transport_error` / `provider_timeout` | 分析 Provider 连接失败或超时 | 检查网络、endpoint 和超时配置；不会自动重试。真实对照曾在约 90.6 秒超时，不能由此推断是代理或推理档位导致 |
| `provider_http_error` | 上游 HTTP 非成功状态，包括鉴权/模型/参数错误 | 查看 `provider_call` 的 `http_status`，核对配置；HTTP 拒绝不等同于模型内容拒绝 |
| `provider_model_refusal` | 上游显式 refusal 或 `content_filter` | 标记失败，不按用户真实风险分类，不记录为成功 |
| `provider_json_parse_error` / `provider_invalid_response_shape` | 前置杂文、代码围栏、重复键、非法数值、坏 JSON 或根节点不是对象 | 检查请求格式模式及网关能力；保持失败，不使用截取或正则修复 |
| `provider_incomplete_response` / `provider_empty_response` / `provider_protocol_error` | 截断、空响应或上游协议不符合预期 | 结合 `finish_reason` 和错误分类排查；模型目录可达不代表生成有效 |
| `schema_validation_failed` / `domain_validation_failed` / `invalid_analysis_result` | JSON 合法但字段类型/结构、条件约束或非空业务要求未通过 | 查看应用校验阶段状态；不跳过 Pydantic，也不保存为已完成 |
| `analysis_unavailable` / `internal_error` | 未分类的应用或持久化故障 | 按请求标识检查脱敏日志与异常类型；不能归类为用户风险或模型拒绝 |
| 错误 `output_rejected` | 输出安全检查返回 `Controversial` 或 `Unsafe` | 不发布或保存为完成；流式请求此前可能已显示通过前缀 Guard 的片段，不能据此认定完整结果通过安全检查 |
| 错误 `unsafe_input` 或状态 `blocked` | Qwen3Guard 将输入分类为 `Unsafe` | 系统会停止普通分析；这只是内容分类，不等同于对用户即时危险的判断 |
| 错误 `unsafe_input_seek_support` | `Unsafe` 同时带有 Suicide & Self-Harm 或 Violent 官方类别 | 页面显示谨慎的现实世界求助方向；分类不代表已确认个人意图或即时危险 |
| 错误 `clarification_limit_exceeded` | 唯一一轮补充后模型仍要求继续澄清 | 当前流程停止并标记失败；不能依靠重复提交形成多轮追问 |
| HTTP `404` / `analysis_not_found` | 历史记录 ID 不存在 | 刷新历史列表并打开现存记录 |
| HTTP `422` / `analysis_not_waiting_clarification` | 对不处于等待澄清状态的记录提交了回答 | 新开对话或选择正确的等待澄清记录 |
| HTTP `404` / `session_not_found` | 提交的会话标识不存在 | 从有效响应或历史记录取得会话标识；省略标识创建新会话 |
| HTTP `409` / `session_conflict` / `session_waiting_clarification` | 并发请求基于过期轮次，或当前会话等待澄清 | 恢复最新会话后重试；等待澄清时使用原记录的澄清接口 |
| `context_too_long` | 当前上下文已经超过字符预算 | 缩短输入或显式调整预算；系统不会丢弃当前输入来强行继续 |
| SQLite `unable to open database file` | 数据目录不存在或当前用户没有写权限 | 从 standalone 目录启动；确认 `backend/data/` 可写；检查 `EMORA_DATABASE_URL` |
| endpoint 报 `model not found` | 配置模型 ID 不在 endpoint 模型列表中 | 查询 `GET /v1/models` 或运行模型目录连通性命令，并将精确 ID 写入 `.env` |
| 聊天区没有增量显示 | 浏览器缓存了旧静态脚本、SSE 被反向代理缓冲或后端仍是旧进程 | 重启 `./start.sh` 并硬刷新页面；确认 `/api/analyses/stream` 返回 `text/event-stream`，代理需关闭响应缓冲 |

`provider_call` 记录 endpoint 主机、请求/返回模型、请求与会话标识、耗时、JSON 解析结果及上游实际提供的数值 Token usage；缺失用量保持未知，不估算。其 `status=parsed` 仅代表 JSON 已解析。`analysis_operation` 另记最终应用状态、提要/正式结果的 Schema 与领域校验状态；只有安全检查和持久化均通过才能标记完成。SSE 的 HTTP 200 只表示流已开始，最终成功以 `complete` 事件中的业务状态为准，失败也可能出现在流内。

默认日志不记录用户输入、模型提示词/回复或 API Key。SQLite 继续按原有产品要求保存会话内容，新增日志不会复制这些正文。排查时不要打开调试级别去记录正文；只分享脱敏后的错误码、请求标识、HTTP 状态、耗时和异常类型。
