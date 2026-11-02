# Emora MVP 开发日志

本文件按时间追加文档核对、设计决策和实际变更。产品需求以 `PRD.md` 为准，技术实现以 `TECHNICAL_DESIGN.md` 为准；日志记录过程，不覆盖两份规范。

## 2026-10-31

- 后续依据 GPT-5.6 Terra 的模型接口资料确认原生推理强度支持 `none`、`low`、`medium`、`high`、`xhigh`、`max` 六档；此前关于 Ruoli 未声明该参数的记录反映当时未确认的状态。composer 通过 `/api/runtime` 读取 `provider.model` 和档位列表，页面初次加载及恢复可见时刷新；输入区左下方以小字号显示当前模型名，窄屏下模型名与计数换行。
- 完成端到端档位映射：请求契约限定六个值；前端在新分析和澄清请求发送 `reasoning_effort`；API/服务/Agent 将所选值传入深度分析提要及正式回复的每次 Provider completion；Provider 将该值写入 `chat.completions.create` 请求。成功响应和历史记录保存、恢复本次档位；旧 SQLite 表通过增量列迁移默认为 `medium`，澄清请求省略档位时沿用记录值。
- 以代码链确认前端档位会被映射到 Provider 出站请求。此前非业务探针中 `high` 请求被中转接口接受，但没有上游推理用量或状态元数据；因此这能证明参数可被接口接受，不能证明中转站已将它透传并实际启用对应推理资源。
- 同步 PRD、前端设计、技术设计、README 和运行手册；更新静态资源版本，避免缓存旧样式。验证范围按要求限于 Python/JavaScript/Shell 语法、应用和健康接口、SQLite 及已配置外部 endpoint 连通性，不做页面交互或业务行为测试。
- 验证结果：Python `compileall`、`node --check`、启动脚本 `bash -n` 均通过；`backend/tests/test_infrastructure.py` 为 5 passed，包含应用/静态资源可达、Agent 图可编译、SQLite `SELECT 1` 与已配置 Provider/Guard endpoint 连通性。重启项目栈后 Guard 和 Web 启动就绪，`/health` 返回 200。未执行页面交互或业务行为测试。
- 按 Ruoli OpenAI 兼容配置将本机 `.env` 分析模型切换为 `gpt-5.6-terra`，保留既有 API base 与凭据；`.env.example` 和运行手册示例同步更新。`.env` 仍为权限 600，密钥未打印或写入文档。模型目录查询确认 `gpt-5.6-terra` 存在。
- 按用户要求使用相同短提示、system 指令、temperature、max completion tokens 和 SSE 请求，对旧 `deepseek-v4.1-flash` 与新模型分别运行两次。新模型首个非空内容块 TTFT 为 4.130、3.028 秒，总用时为 4.217、3.223 秒，均 HTTP 200 并正常结束；本次直接对旧模型发起的两次流式请求均 HTTP 503。
- 进一步按用户要求核对配置切换前的 DeepSeek 历史业务日志和 SQLite 记录。旧模型“你好”首次分析的两次成功全链路为 5.784、5.808 秒，Provider 分别为 5.129、5.435 秒；另有两次 ReadTimeout，全链路 31.606、31.148 秒，且有一次约 30.963 秒的慢成功。旧请求是非流式，历史没有可恢复的 TTFT。新 Terra 的两次通用短提示流式基准为 3.028、4.130 秒 TTFT 和 3.223、4.217 秒总耗时；但测试 prompt 与生产 JSON Schema 提示不同，不能拿来当严格模型间 TTFT 对比。
- 新 Terra“你好”生产链路目前一次记录为 Provider 7.269 秒、端到端 7.994 秒。相较 DeepSeek 两次正常成功的均值 5.796 秒，这次 Terra 慢约 2.198 秒或 38%；相较 DeepSeek 超过 30 秒的历史尾部则更快。样本量很小、服务负载和旧模型波动明显；目前证据不支持“新模型典型总耗时更短”，旧 TTFT 也无法比较。模型目录确认新模型存在，实际新模型业务请求成功。
- 按服务商文档启用分析 Provider 原生 `stream=true`；Provider 聚合完整 SSE 后仍执行 JSON、领域和输出 Guard 校验，浏览器通过新增 `/api/analyses/stream` 与 `/api/analyses/{id}/clarify/stream` 接收 SSE。只有安全检查和 SQLite 保存完成后，后端才发送 `delta` 文本，前端聊天区逐块渲染；保留原 JSON API。新增 Provider 原生流测试和 SSE 路由测试，JavaScript 语法检查通过。
- 按用户提供的 ChatGPT App 与 Ruoli `gpt-5.6-terra` 回答差异，新增默认关闭的深度思考模式。前端 checkbox 经同一 JSON/SSE API 提交；请求契约、LangGraph state、Provider 和 SQLite 记录贯通该字段，旧 SQLite 表通过 `DEFAULT 0` 增量迁移。澄清可沿用或更改模式，历史响应恢复原值。
- 修正深度模式只有提示词变化、没有可见分析流的问题：增加一次单独的公开分析提要生成和输出 Guard 检查，安全提要通过 SSE `analysis` 分段送达，并作为正式答复上下文；前端在收到每段后等待绘制帧，避免同一读取块中的多段文本一次性挤在同一帧。隐藏思维链仍不传输，最终答复仍在完整输出检查和保存后发送。
- Ruoli 文档未提供原生 `reasoning_effort` 或 Skill 参数，深度模式只用明确的 system 指令和已支持的 `max_completion_tokens=4096`；扩写现有 JSON 字段的质量要求，要求区分事实/假设、给建议优先级，不补造个人背景、不诊断、不展示隐藏思维链。安全门、Schema、单轮澄清和事务顺序不变；此实现不保证与 ChatGPT App 的模型、系统指令或响应策略相同。
- 根据 standalone 输出安全顺序，SSE 仍在完整生成、校验、Guard 和 SQLite 提交后分段展示最终正文；使用自适应片段数，让短回复也有可见增量，同时将最长渐进时长限制在约 1.8 秒。更新技术设计、前端设计、PRD、README 与操作手册。
- 验证：`uv run pytest` 15 passed、2 skipped；Python 编译、JavaScript 语法、`start.sh` Shell 语法均通过；本地页面确认 checkbox 初始关闭且可开关，Web 健康检查返回 200。未将用户的健康信息重新发送给 Ruoli，因此本轮未测量深度模式真实模型输出质量或时延。

- 将 `start.sh` 接入 `scripts/start_all.py`，同一前台生命周期先启动本地 Guard 并等待模型就绪，再启动同源 Web；两项服务日志均输出当前终端。重启、`--stop` 和 Ctrl+C 清理整组进程；本地 Guard 与 Web 固定使用各自端口，不自动顺延。兼容项目路径包含空格；外部 Guard endpoint 不由本地脚本管理。
- 本地 Guard HTTP 客户端对 loopback 使用 `trust_env=False`，避免本地请求被环境代理转发；这项改动只针对 loopback，不能解释或修复远端分析模型的全部失败。
- 按用户实际输入“你好”复现：Guard 请求已返回 200，分析接口仍会失败。旧 SDK `parse` 调用曾出现 `json_invalid`；改为网关文档支持的普通 `chat.completions.create`，只发送 model/messages，可信 system 提示携带 JSON Schema。严格 JSON 解析、服务层 Pydantic/领域校验及输出安全门均保留，不截取 JSON、不伪造回退答案。
- 修改后仍实际复现两次约 31 秒失败，脱敏异常显示 `APITimeoutError` / `ReadTimeout`。随后观察到同样输入的一次分析模型请求约 30.5 秒后返回 200，证明旧 30 秒窗口存在边界问题，不能仅凭这些日志归因于代理。将默认、配置样例和本机分析超时统一为 90 秒；禁用 SDK 自动重试，日志新增异常类型、HTTP 状态和耗时，未输出凭据或模型内容。
- 重启新版本后，用用户已提供的“你好”连续两次实际调用 `/api/analyses`，分别约 5.80、5.81 秒返回 `waiting_clarification`，输入/输出 Guard 通过，并按返回 ID 读取确认已持久化。此前的真实失败记录保留。上述结果确认本次链路可用，不保证上游延迟始终稳定，也不替代真实成年用户体验及安全有效性验证。
- 本轮完整基础设施套件 13 项通过、2 项因未注入测试 endpoint 而跳过；随后将本机实际 endpoint 安全注入测试进程，单独补跑两项连通性检查，均通过。合计覆盖 15 项检查；前一轮实际 endpoint 全套亦为 15 项通过。新增检查覆盖整组连续重启、Ctrl+C 清理、Guard 未就绪时禁止启动 Web、loopback 不受错误代理配置影响。Shell 语法检查通过。
- 同步 README、运行手册、技术设计和安装成功提示；测试服务在核验后停止，使用者通过 `./start.sh` 在自己的终端启动并查看日志。技术债务中的安全适用性和真实用户体验两项继续保持待验证。

## 2026-10-30

- 核对 `TECHNICAL_DESIGN.md` 与 `PRD.md`、`FRONTEND_DESIGN.md` 的用户流程、状态、API 契约、数据结构、安全门、澄清轮次、事务和测试边界。
- 明确分析/澄清接口为同步请求—响应：`pending` 是浏览器在请求未完成期间的本地呈现，不属于 SQLite 或 `AnalysisStatus` 持久状态；持久状态为 `waiting_clarification`、`completed`、`blocked`、`failed`。
- 校正 FastAPI lifespan 代码骨架，使其按数据库、Provider、安全客户端、Agent 图、Service 顺序初始化，并按逆序关闭；资源关闭支持同步或异步 `close()`，异常时继续清理其余资源。
- 清理辅助函数兼容 SQLAlchemy `Engine.dispose()`，与 SQLite 存储选型的实际资源释放接口一致。
- 澄清续答的输入安全检查明确针对原始描述、已保存澄清问题及补充回答组成的完整上下文；最多一轮限制仍由服务层程序约束。
- 统一成功响应与 HTTP 错误响应的边界，并说明最终结果和 `completed` 仅在 SQLite 事务提交后返回。数据库不可用时不能承诺失败状态也能成功持久化。
- 在技术设计文件树登记本日志和 `TECHNICAL_DEBT.md`；自动化测试边界保持为基础设施启动、可达性和连通性检查，不替代用户体验验证，不虚构业务样例。
- 本次只修改设计文档并新增工作记录；未实现应用代码、未连接模型或安全 endpoint、未执行真实用户交互。检查方式为文档交叉阅读和术语/状态检索。
- 按反馈收缩技术债务文档：移除开发阶段的常规待办和待选配置，只保留安全能力适用性与真实用户体验验证两项尚无证据关闭的问题。
- 明确前后端分离边界：浏览器端只通过 JSON API 通信，Jinja2 只提供不含业务数据的应用外壳；MVP 保持同源、同仓库部署，不新增前端框架或构建工具。
- 按要求将文件系统结构明确拆为根目录下的 `backend/` 和 `frontend/`，同步调整模块路径、静态资源挂载、模板目录、SQLite 数据目录和启动入口说明。
- 核对后端原设计只有异常处理器中的零散 `logger.exception()`，没有统一日志初始化、请求关联或敏感字段约束；新增标准库日志配置与 HTTP 请求关联 ID 设计，不引入日志平台或新依赖。
- 本次只调整文档结构和示例路径，未在独立 MVP 目录中创建应用源码目录或迁移文件；技术设计反映目标结构，供实现阶段遵循。
- 按独立技术设计在 `emora-mvp-standalone/` 实现 FastAPI/SQLite MVP：新增 Pydantic Web 契约、异步 OpenAI Provider、编译一次的单节点 LangGraph、Qwen3Guard-Gen 官方文本协议适配、服务层强制输入/输出门禁、事务存储、脱敏请求日志、同源 JSON API 与原生 HTML/CSS/JS 会话界面。
- 新增基础设施测试，范围仅覆盖应用/静态资源启动可达、LangGraph 可编译、SQLite `SELECT 1`，以及真实配置 endpoint 的模型列表连通性；未发送情感案例或伪造业务数据。
- 核验：`uv run pytest` 为 3 passed、2 skipped；跳过项是未配置的分析与 Qwen endpoint 连通性。应用页面与普通/澄清/完成/阻断/失败状态均通过单页会话呈现。真实模型、安全 endpoint 与真实成年用户体验尚未验证，相关风险继续保留在技术债务文档。


- 修复安全 endpoint 与 Web 共用 8000 的配置遗漏：代码默认值、配置样例、本机配置、README 与技术设计统一为 Guard 8001 / Web 8000。增加受控 analysis_failed 错误码日志，不输出用户文本或凭据。本机未发现 Guard 8001 监听服务或 vllm 命令；安全服务连通性仍未满足。

### 启动清理修复与本地 Guard 部署

- 将 `start.sh` 改为调用 `scripts/launch.py`，记录实际 Uvicorn PID；按项目路径、模块和工作目录识别旧服务，兼容旧 shell PID 文件及 PID 文件缺失。先保存子进程集合，再 TERM，超时 KILL；检查旧进程退出、固定端口释放、新 PID 监听和健康检查后才报告 ready。增加启动锁及替换进程的 PID 文件保护，日志同步输出到当前终端和文件。
- 新增真实进程基础设施测试，覆盖连续三次重启、PID 文件缺失、手动相对路径启动、Ctrl+C 清理、无关占用端口保护，以及父子进程均忽略 TERM 时的强制清理。端口被无关进程占用时直接失败，不自动改用 8001。
- 增加 `install-guard.sh`、`start-guard.sh`、独立 `.guard-venv` 和 Transformers 本地服务。通过 ModelScope 下载官方 `Qwen/Qwen3Guard-Gen-0.6B` 权重（模型文件 1,503,300,328 字节），已在本机 Apple MPS 成功加载；使用官方 tokenizer 模板和生成协议，不新增分类规则。安装后只更新本机 Guard 配置并设置 `.env` 权限 600，未输出分析密钥。
- 实际重启验证：Guard 旧 PID 76083 被替换为 76261；Web 旧 PID 74404 被替换为 76344。两个旧 PID 均已不存在，8000、8001 各只有一个监听进程；重启一项服务不终止另一项。Web `/health`、首页及两个静态资源均返回 200。
- 使用本次用户原始请求及本次已发送的真实助手回复验证 Guard 输入/输出接口和现有客户端解析，未编造情感或风险案例；两次调用分别约 1.28 秒和 0.21 秒，包含输出 `Refusal` 字段。此证据仅确认模型推理和传输协议可用，不证明产品场景安全效果。
- 使用本机实际配置运行基础设施测试，分析 Provider 和 Guard 模型列表连通性检查均成功，合计 11 项通过、无跳过。Shell 语法与 Python 编译检查通过。补全 `doc/OPERATIONS.md` 的本地安装、启动停止、端口说明与错误对照；真实用户体验及安全能力适用性仍保留在技术债务中。
