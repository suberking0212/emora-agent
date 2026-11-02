# 阶段一 CLI 与验收测试手册

## 1. 目的与状态约定

本手册指导阶段 1 的本地测试：在 PostgreSQL 上运行 CLI，确认输入，调用一个活动 provider 执行分析，校验/保存结果，处理澄清恢复，查看结果并删除会话。另覆盖安全分支、授权撤回、运行事件、工具设施和兼容性测试。

记录每个项目时只能使用以下状态：

- **通过**：命令或测试实际运行成功，记录运行环境、命令、退出码和证据。
- **失败**：实际执行且结果不满足预期；保留错误信息和复现条件。
- **跳过**：明确没有执行，并写明原因。
- **未验证**：缺少运行证据。不能把设计、代码存在、mock 测试或命令帮助输出写成真实 provider 能力/质量通过。
- **需要添加 provider 后测试**：暂缓真实 provider 测试的专用标记；provider 配置后再测，不与通过混用。

当前已知：无真实 provider 测试仍须覆盖纯契约、PostgreSQL、受控流程、CLI 参数、迁移和内部工具。真实 provider 调用、OCR 的实际识别和产品质量评测在配置真实 provider 后进行。本手册不会因写有命令就代表该命令已在本机通过。

## 2. 测试前检查

在仓库根目录执行以下命令。项目锁文件是依赖安装依据；涉及安装/运行测试时使用 frozen 模式，避免隐式改动依赖锁。

```bash
python3 --version
uv --version
uv sync --frozen
uv run --frozen python --version
uv run --frozen emotional-agent --help
uv run --frozen emotional-agent analyze --help
uv run --frozen emotional-agent resume --help
uv run --frozen emotional-agent show --help
uv run --frozen emotional-agent delete --help
uv run --frozen emotional-agent onboard --help
```

阶段 1 的候选兼容基线要求 Python 3.10+，本轮实测曾在 Python 3.12.12 完成冻结安装；请把实际版本记入测试记录。预期 CLI 命令只有 `onboard`、`analyze`、`resume`、`show`、`delete`。不得出现工具选择、反馈或改进授权命令。

检查仓库内 `.env.example`，按本机配置准备环境变量。`DATABASE_URL` 指向业务 PostgreSQL；`CHECKPOINT_URL` 可指向同一 PostgreSQL 实例，但业务 schema 与 checkpoint schema 必须独立。示例：

```bash
export DATABASE_URL='postgresql+psycopg://laosun@localhost:5432/postgres'
export CHECKPOINT_URL="$DATABASE_URL"
export DATABASE_SCHEMA='app_stage1_cli_20261022'
export CHECKPOINT_SCHEMA='checkpoint_stage1_cli_20261022'
```

示例中用户名、密码和数据库名须换成实际值。密码含 `@`、`/`、`:` 等 URL 保留字符时必须 URL 编码。不要把真实密钥写进文档、shell 历史、日志或仓库。运行日志需脱敏。（目前是本地无密码配置）

迁移配置从 `.env`/环境读取 schema，Alembic 的连接 URL 从 `DATABASE_URL` 读取。先确认目标 PostgreSQL 可连接，并且当前账号可创建 schema/table：

```bash
uv run --frozen python -c 'from sqlalchemy import create_engine, text; import os; e=create_engine(os.environ["DATABASE_URL"]); print(e.connect().execute(text("select version()")).scalar())'
```

在空测试数据库执行迁移：

```bash
uv run --frozen alembic upgrade head
uv run --frozen alembic current
```

预期 head 为 `0003_tool_payload_hash`。禁止在含重要数据的数据库上做清理性测试。需要重新验证 fresh migration 时使用新的空数据库或新的 `DATABASE_SCHEMA` 与 `CHECKPOINT_SCHEMA` 名称，不要删除共享 schema。

## 3. 无 provider 的自动测试

这些测试不得访问真实 provider；受控 provider 只验证状态机/存储/恢复契约，不证明真实模型行为。

```bash
uv run --frozen ruff check packages_py tests migrations
uv run --frozen python -m compileall -q packages_py migrations tests
uv run --frozen pytest -q
RUN_POSTGRES_TESTS=1 DATABASE_URL="$DATABASE_URL" uv run --frozen pytest -q
```

PostgreSQL tests 会创建主体、会话、输入、运行、用途授权副本及工具幂等记录，并实际删除测试会话。应使用隔离的本地测试数据库/schema，不要指向生产或含重要个人数据的库。

当前 suite 的证据范围：

- Pydantic 严格字段、引用、证据切片和事件脱敏契约。
- PostgreSQL 生命周期、归属授权、删除与改进授权撤回后的用途副本清理。
- 受控 provider 下 LangGraph 暂停、checkpoint 连接重建、恢复、重复恢复和不兼容拒绝。
- PostgreSQL 测试数据下真实只读 `get_run_status` 领域操作及 `ToolNode` 接线。
- 副作用工具持久幂等回放、payload 冲突、并发单次执行、超时结果核对回调。
- CLI 选项互斥及 JSONL 事件序列/脱敏。

本轮运行记录：默认 suite 4 passed、4 skipped；启用 PostgreSQL 后 8 passed。后续修改后需按上面命令重新执行；不要照抄历史结果作为新代码的验证。

## 4. 配置 provider

当前 CLI 的 `onboard` 实现只接受 provider 名称 `openai`，使用 OpenAI-compatible Chat Completions JSON Schema structured output。传入可访问的模型 ID。可选 endpoint 用于兼容服务。

```bash
uv run --frozen emotional-agent onboard --help
uv run --frozen emotional-agent onboard --provider openai --model '<模型ID>'
```

自定义 endpoint：

```bash
uv run --frozen emotional-agent onboard \
  --provider openai \
  --model '<模型ID>' \
  --endpoint 'https://兼容服务/v1'
```

CLI 隐藏提示 API key。onboard 会先发结构化健康探测，探测成功后才保存配置。凭据优先写系统 Keychain；Keychain 不可用时会询问是否存进用户配置目录下权限为 `0600` 的文件。只在确认本机安全后接受文件回退。测试 key 不要通过 `--api-key` 参数传入；目前没有这个参数。

配置路径：`~/.config/emotional-agent/workspaces/<workspace-id>/config.json`。CLI 还会在同一 workspace 路径存身份信息。运行前可检查文件权限和是否意外含明文密钥；`config.json` 应只保存 `credential_ref` 而不含 key。

**onboard 未通过时不要声称 provider 已配置。** 本次真实探测是否通过、探测模型是否支持严格 JSON Schema、认证及联网均需实际验证。

## 5. CLI 闭环手动测试

所有以下步骤均需在 provider 健康探测成功且 PostgreSQL 迁移到 head 后进行。CLI 输出主体为 JSON；手动记录 session/run UUID、执行时间和错误码，但不要记录会话原文、API key 或完整供应商响应。

### 5.1 分析一段文本

```bash
uv run --frozen emotional-agent analyze --text '我们讨论工作安排时产生了分歧，我希望更清楚地表达自己的需求。'
```

CLI 应先用默认拒绝确认提示询问是否使用输入。回答 `y` 后开始运行。记录返回的 `session_id`、`run_id`、`status`。状态可以是 `completed` 或 `waiting_clarification`；若失败记录 `error_code`。

重复使用已有会话：

```bash
uv run --frozen emotional-agent analyze \
  --session-id '<SESSION_UUID>' \
  --text-file './test-input.txt'
```

三种输入来源 `--text`、`--text-file`、`--image` 必须且只能选择一种。文本文件须 UTF-8。测试输入避免使用真实个人敏感信息，删除后确认运行事件也清理。

图片路径会调用 macOS Vision OCR，打印识别文本并单独要求确认。图片分支实际受平台和系统 Vision 依赖限制；非 macOS 或缺少 `swift` 时预期返回 `provider_unavailable`。OCR 识别质量、图片内容处理均需单独记录，当前未验证。

### 5.2 恢复澄清

若 analyze 返回 `waiting_clarification`，用结果里的 session/run ID 继续。

提供修正或补充文本：

```bash
uv run --frozen emotional-agent resume '<SESSION_UUID>' '<RUN_UUID>' --text '我希望先说明工作安排，再表达自己的感受。'
```

跳过澄清：

```bash
uv run --frozen emotional-agent resume '<SESSION_UUID>' '<RUN_UUID>' --skip
```

检查只提交一个分支。无 `--text` 且无 `--skip`，或两者同时出现，都应返回 `invalid_request`。`skip=true` 不能携带输入。恢复只允许 waiting clarification 状态，不能重复推进轮数或对已完成运行恢复。

**验证重点：** 当前服务构造 corrected 输入版本，由仓储在恢复事务中插入新输入并更新会话版本。输入式恢复尚缺完整运行证据；需核对恢复结果绑定新 input_version_id、轮数只增加一次，重复相同请求不重复插入输入。出现异常应记录失败，不能只凭代码路径认定通过。

### 5.3 查看结果

```bash
uv run --frozen emotional-agent show '<SESSION_UUID>'
```

检查只返回已验证并发布的结果；运行等待澄清时可返回已发布的初步结果；运行状态由 analyze/resume 的响应体现，show 返回的是结果而非 RunResponse。核对内容结构及来源证据，不以单次主观满意判断质量通过。保存实际输出结构时必须先去除原文、身份和密钥。

### 5.4 删除会话

```bash
uv run --frozen emotional-agent delete '<SESSION_UUID>'
```

预期返回完成回执。随后再次 show 应返回 `not_found`。如测试 JSONL 事件，核对对应 session 的事件已清除；如运行过 workflow，核对该 session 对应 checkpoint 与 workflow artifacts 已清除。不要只凭 CLI 回执就认定所有副本都已物理清理；数据库断言和 checkpoint 查询才是副本清理证据。

## 6. 输入校验与负向命令

```bash
# analyze 缺少输入：invalid_request，退出码 2
uv run --frozen emotional-agent analyze

# analyze 输入来源互斥：invalid_request，退出码 2
uv run --frozen emotional-agent analyze --text 'hello' --image './sample.png'

# 空文本：应拒绝，不创建有效分析运行
uv run --frozen emotional-agent analyze --text ''

# resume 同时提供文本和 skip：invalid_request，退出码 2
uv run --frozen emotional-agent resume '<SESSION_UUID>' '<RUN_UUID>' --text '补充' --skip

# 未知会话/主体无权访问：通常返回 not_found，不泄漏其他主体数据
uv run --frozen emotional-agent show '00000000-0000-0000-0000-000000000000'
```

`analyze --text ''` 仍可能先显示确认提示；拒绝或错误退出后检查数据库没有发布结果。CLI 对无效 UUID 的解析也会在业务层之前失败。

## 7. 错误排查

先保留完整错误码、命令、退出码和最后的 traceback（若有）；不要粘贴包含数据库 URL 密码、原文或 provider 原始响应的日志。

| 错误码/现象 | 常见原因 | 排查方法 |
|---|---|---|
| `model_provider_not_configured` | 当前 workspace 没有有效 provider 配置，或环境变量缺失 | 执行 onboard；检查 `config.json` 的 provider/model/credential_ref，不输出凭据内容。当前 CLI 配置优先读取 workspace 配置；纯环境变量是否可满足真实 CLI onboarding 尚未验证。 |
| `provider_unavailable` | provider 名称不支持、Keychain/credential_ref 不匹配、凭据引用失效、SDK 或 endpoint 不可用 | 当前 onboard 仅支持 `openai`。检查 Keychain 中 service `emotional-agent`、workspace key；文件回退时检查 `credential` 文件和 `0600` 权限。修复凭据引用，不要静默换另一凭据。重新执行 onboard。 |
| `provider_timeout` | 供应商请求超时或总预算耗尽 | 检查网络、endpoint 和模型可达性；按响应时间判断是否确实超过设置。避免盲目重试引发费用。当前 onboard 探测固定 15 秒/1 次，运行默认 60 秒/最多 2 次。 |
| `provider_rate_limited` | 服务端限流 | 检查账号配额/速率限制并等待后重试；确认同一调用总尝试数有界。不要把 429 当作 schema 错误。 |
| `provider_failed` | 供应商 5xx、请求失败或模型返回不合约结果 | 查状态码/请求 ID（若 adapter 安全记录），检查模型是否支持 JSON Schema strict；不能记录原始响应正文。 |
| `provider_schema_invalid` | 已收到模型文本，但不能通过目标 Pydantic schema（如返回 null、非法 UUID 或字段类型错误） | 与网络故障分别记录；不重试、不发布。仅记录字段路径和校验类型，不记录原始响应。即使供应商宣称 strict，也需逐阶段实测。 |
| `output_validation_failed` | 输出契约、引用/证据、结果校验或安全复核未通过 | 记录 run_id、step、错误码；检查引用 ID、输入版本和切片边界。不可绕过验证发布候选。 |
| `checkpoint_incompatible` | 运行时、依赖锁、图或状态版本与暂停 checkpoint 不兼容 | 不要创建新 run 冒充恢复成功；保留数据库/checkpoint 状态和 lock hash，核对当前 `uv.lock` 与 RunRecord。缺少已验证迁移器时只能明确拒绝。 |
| `run_not_waiting` | run 已结束、未暂停或状态不允许恢复 | 用 show 查看会话当前状态；只能恢复 waiting clarification 的运行。 |
| `stale_version` | 输入版本已变更或旧 run 不再是活动 run | 新建有效分析请求；不要在旧 run 上强制恢复或发布。 |
| `input_unconfirmed` | 输入为空或没有处于 confirmed/corrected 状态 | 重新走 CLI 输入确认；图片 OCR 必须人工确认识别文本。 |
| `idempotency_conflict` | 同一 request_id 用于不同操作/payload | 新请求生成新 UUID；重试原操作时必须沿用原 request_id 和完全相同 payload。 |
| `tool_idempotency_conflict` | 内部副作用工具的幂等键被不同 payload 重用 | 不得复用该键；检查调用方 key 设计。不要手工删除运行中的幂等记录。 |
| `tool_outcome_unknown` | 副作用调用异常/超时，不能证明外部操作是否完成 | 禁止直接再执行写操作。先运行该工具的 reconciliation 查询；只有核对到确切结果才保存 completed 并返回。当前测试用隔离 callback 验证机制，不代表生产外部工具已接入。 |
| `tool_idempotency_required` | 有副作用工具没有稳定幂等键、持久 store 或工具不支持幂等 | 检查工具声明与调用链。不可把工具改成只读来绕开约束。 |
| `tool_execution_failed` | 工具授权、schema、handler 或执行异常 | 检查白名单、授权 scope、输入/输出 JSON Schema；对敏感错误不要向模型/CLI回显内部异常。 |
| `unauthorized` / `not_found` | 主体 token 无效、会话不属于当前主体、会话已删/过期 | 确认当前 workspace identity 和 session ID。对其他主体会话应不暴露存在性。 |
| `persistence_failed` | PostgreSQL 不可达、迁移缺失或写约束失败 | 检查连接、schema、Alembic current、数据库日志及事务异常。不要改用内存数据库继续并宣称阶段闭环通过。 |
| OCR `provider_unavailable` / `ocr_failed` | 非 macOS、缺少 Swift/Vision、图片无法读取或识别失败 | 在支持环境确认 `swift` 可用，换用可读图片；如识别不可靠，停止并改用 `--text`/`--text-file` 后人工确认。 |

### 数据库常见故障

```bash
uv run --frozen alembic current
uv run --frozen alembic heads
uv run --frozen python -c 'import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ["DATABASE_URL"]); print(e.connect().execute(text("select current_database(), current_schema()")).one())'
```

确认业务表位于 `DATABASE_SCHEMA`、LangGraph checkpoint 表位于 `CHECKPOINT_SCHEMA`。若 `PostgresStorage` 报缺表，先确认迁移环境变量与 CLI 进程环境一致；Alembic 可能通过 shell 环境变量读取连接。若 checkpointer 缺表，确认 CLI/测试所用 `CHECKPOINT_URL`（服务当前以它或 `DATABASE_URL` 连接）与 schema 已设置并由运行时 setup 创建官方表。

## 8. 事件和数据治理检查

设置仅用于本地测试的路径：

```bash
export EVENTS_PATH="$PWD/.local-test/run-events.jsonl"
```

事件由图节点 writer 追加。检查 JSONL 字段：`input_summary` 必须为 null；不得含输入正文、prompt、API key 或供应商原始响应；运行中应有开始与完成/等待/失败状态事件。删除会话后，文件中该 session 的事件应被清理。测试完妥善移除本地敏感测试数据；不要把 `.local-test` 提交。

改进授权不通过 CLI 开放。只通过内部服务/隔离 PostgreSQL 测试 grant → add copy → revoke → 数据库核对：撤回后用途副本应为零，服务所需输入仍保留；未授权不能写入用途副本。会话删除需验证业务副本、workflow artifacts、checkpoint 和 JSONL 各存储面。

## 9. 内部工具设施

工具没有 CLI 命令或终端选择入口。通过内部测试调用 `ToolExecutor`/`ToolNode`：

```bash
RUN_POSTGRES_TESTS=1 DATABASE_URL="$DATABASE_URL" uv run --frozen pytest -q tests/test_workflow_postgres.py
RUN_POSTGRES_TESTS=1 DATABASE_URL="$DATABASE_URL" uv run --frozen pytest -q tests/test_postgres.py -k durable_side_effect
```

需检查真实 PostgreSQL 测试数据上的 `get_run_status` 授权与只读结果；隔离副作用测试覆盖幂等、冲突、并发和核对 callback。每个新增工具仍须提供其真实业务 handler、权限策略、schema、稳定幂等键和可靠 reconciliation；不可由受控 handler 的成功推断外部系统集成完成。当前 ToolExecutor 对 handler 超时只能在 handler 返回后判定超时，不能强制中止执行；超时不能解释为副作用已取消。

## 10. Provider 后手动测试与质量验收

状态标记为 **需要添加 provider 后测试**，直到真实请求执行完成并有可审查证据。配置后依次执行：

1. `onboard`：验证真实认证、structured output probe 和凭据持久化/重启读取。
2. 普通输入 `analyze`：验证理解、前置/理解后/输出安全检查、candidate schema、证据引用、发布与保存回读。
3. 澄清 `resume --text`：验证新输入版本确实保存/确认、轮数只推进一次、崩溃/重复请求幂等。
4. 澄清 `resume --skip`：验证不再提问，恢复到结果发布。
5. `show`：确认只呈现验证发布的结果。
6. `delete`：确认删除回执以及 PostgreSQL、checkpoint、workflow artifact、JSONL 中的副本清理。
7. provider 失败场景：测试认证错误、参数/schema 错误不重试、429/可恢复 5xx/超时重试上限与总预算、原文和 key 不进入日志。
8. 安全场景：用经审核的非危害样例覆盖 normal/safety/uncertain、输出安全复核和最多一次 revise。记录预期安全路由、实际响应及是否含普通建议。
9. 代表性评测：预先定义匿名化样例、评分规则和失败阈值，评价依据可追溯性、不确定性、建议适用性、安全路由、时延、成本。单个手工成功样例不等于质量评测通过。
10. 升级回归：修改锁文件或版本后重新跑迁移、普通/安全路径、修订、interrupt/resume、重启、并发、旧 checkpoint 拒绝/已验证迁移、删除禁止写回与错误码矩阵。未验证的旧版本 checkpoint 不得虚构为兼容。

Provider 结果具有模型和服务商变化性；记录 provider/model/endpoint 类型、时间、运行版本、lock hash 和用例 ID，不记录凭据、完整敏感对话或未经脱敏的响应。更新 provider 或依赖后重跑相关回归。

## 11. 测试记录模板

```text
日期（Asia/Shanghai）：
执行人/环境：
Python / uv / OS：
Git commit 或工作区状态：
PostgreSQL 版本、数据库（脱敏）和 schema：
provider/model（若适用；不记密钥）：
命令/测试用例：
状态：通过 / 失败 / 跳过 / 未验证 / 需要添加 provider 后测试
退出码与断言结果：
run_id / session_id（如适用）：
错误码、复现条件与排查结果：
脱敏证据位置：
范围限制与后续动作：
```

本次阶段结论必须回写[开发进度日志](../C端开发/开发进度日志.md)，验收项对照[阶段任务与验收标准](../C端开发/阶段任务与验收标准.md)。未开始的验收任务写进度/验收文档；仅已实现代码造成的缺口才加入[技术债务清单](../C端开发/技术债务清单.md)。

## 12. 当前已知未验证项

- 真实 provider 能力、onboard 到删除的完整真实 CLI 闭环。
- 带文本恢复的新输入版本、重复请求及崩溃重放的完整验证。
- provider 错误归一化、限流与 5xx/超时重试边界的真实网络行为。
- OCR 实际识别质量与系统依赖。
- 代表性产品质量、安全场景评测和成本/耗时基线。
- 真实外部副作用服务的执行与核对集成；目前只有隔离数据库及 callback 测试。
- 依赖升级回归与真实历史 checkpoint 兼容；无历史版本证据时不宣称通过。

本清单是操作指引，不会自行改变阶段验收状态。结果和证据应以进度日志、验收文档为准。
