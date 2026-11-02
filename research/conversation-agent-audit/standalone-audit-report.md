# standalone 对话自然度与 Agent 架构专项审计

审计日期：2026-10-31  
审计范围：仅 emora-mvp-standalone/  
审计方式：执行链静态追踪、当前模型与 Guard 真实调用、生产前端渲染函数复核。  
变更边界：未改 standalone 代码、Prompt、Skill、数据库、依赖或配置。新增材料仅在本审计目录。

## 结论

Emora 当前属于 **LLM 与 Workflow 混合编排**，但用户体验由**固定报告契约和模板渲染主导**。LLM 理解输入、生成内容并在“澄清/完成”之间做有限选择；它无法选择“只倾听、不分析、不建议”这一输出形态，因为 complete 必须填入建议、情绪、矛盾、需求和下一步。

模板化的直接根因不是 LangGraph，也不是模型用了很多心理咨询师角色提示，而是以下约束的合力：

1. system prompt 要求每个完整结果都包含结构化分析和一项以上建议。
2. Pydantic Schema 将分析栏目设为必填，suggestions 至少一项，next_step 必填。
3. 服务层流式格式化器与前端 renderer 都把字段转换为固定栏目标题。
4. 请求是单条输入的一次性分析，模型没有一般对话历史，也没有“倾听/分析/建议”的行为选择。

LangGraph 图只有 START → agent → END。图本身技术上正确，但只包装一个模型调用，没有条件边、循环、图内恢复或工具调用。当前不需要为了“先进”而引入 Multi-Agent 或 MCP。

## 范围与证据

仅检查 emora-mvp-standalone，不包含父目录中的旧项目或 standalone 副本。

本次复测复用真实配置的分析 Provider（模型 gpt-5.6-terra，origin https://ruoli.dev）和本机 Qwen3Guard-Gen-0.6B。测试直接调用 AnalysisService._run()，传 database=None，不走会写 SQLite 的 create/continue API。没有读取或修改项目数据库。密钥未写入证据。

- [standalone-real-model-test.json](/Users/laosun/Documents/emotional%20solution%20agent/research/conversation-agent-audit/standalone-real-model-test.json)：此前八场景真实调用记录，含请求体、模型参数、解析结果和 Guard 标签。
- [standalone-raw-call-capture.json](/Users/laosun/Documents/emotional%20solution%20agent/research/conversation-agent-audit/standalone-raw-call-capture.json)：本次逐字捕获实际 Provider 请求、SSE 拼接后的原始模型内容、Guard 请求/原始响应、执行阶段、decision 和渲染文本。
- 下文的 api_status 是由 _run() 结果映射到服务代码分支的状态推导，不是本次直接请求 HTTP API 的响应。证据文件已注明这一点。

本次真实请求参数为 reasoning_effort=medium、stream=true；没有发送原生 response_format=json_schema，Schema 作为 system message 的文本内容。实际 messages 只有 system 和 user，没有 developer 或 assistant 历史消息。完整内容以脱敏方式保存在 raw-call-capture.json。

## 当前真实架构

    用户输入
      -> FastAPI /api/analyses/stream
      -> AnalysisService.create() / _run()
      -> Qwen3Guard 输入分类
           Unsafe -> blocked 状态 + 固定现实求助文本
           Safe/Controversial -> 可选深度提要 -> LangGraph
      -> StateGraph: START -> agent -> END
      -> ProviderAdapter 流式 JSON
      -> JSON 反序列化 + Pydantic/领域校验
      -> 输出 Guard（流式前缀及完整结果）
      -> SQLite 保存
      -> SSE complete/delta
      -> frontend resultText() 固定栏目渲染

调用入口见 emora-mvp-standalone/backend/app.py:108。应用在 lifespan 创建 Provider、Guard、图和 AnalysisService，见 backend/app.py:46。核心安全与模型流程见 backend/services.py:197。深度模式会先调用 PublicAnalysisBrief，再进入 Agent 图，见 backend/services.py:223；本次八场景没有勾选深度模式。

## LangGraph 节点与边

项目锁定 langgraph==1.2.18，见 pyproject.toml 和 uv.lock:655。LangGraph 官方滚动文档的 Graph API 定义 State、Nodes、Edges，并说明 conditional edges 可按状态路由；Persistence 文档说明 checkpointer 用于 thread-scoped state、短期记忆、human-in-the-loop 和恢复。链接：

- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://docs.langchain.com/oss/python/langgraph/persistence

实现与官方概念的对应：

| 元素 | 实际实现 | 审计判断 |
|---|---|---|
| State | EmoraGraphState TypedDict，含输入、一次澄清字段、推理档位、summary、回调和 decision，backend/agent.py:9 | 单次调用数据契约有效，不是对话历史 |
| Node：agent | 将 state 映射为 context，调用 EmoraAgent.decide/provider，写回 decision，backend/agent.py:41 | LLM 负责内容与 ask_clarification/complete 两种动作；没有工具或子任务 |
| Edge：START→agent | 固定普通边，backend/agent.py:37 | 不判断用户意图 |
| Edge：agent→END | 固定普通边，backend/agent.py:38 | 图内无 complete/clarify 条件路由 |
| Conditional Edge | 没有 | Agent 动作由服务层在图返回后处理 |
| Reducer | 没有显式 reducer | 单节点只写 decision，无 reducer 并不构成缺陷 |
| Checkpointer | compile() 未传 checkpointer，backend/agent.py:39 | 每次 ainvoke 从新 state 开始；SQLite 手工恢复一次澄清 |
| 循环/恢复 | 无 loop、interrupt、resume、thread_id | 不提供图内多轮或故障恢复 |

图在启动时编译一次并复用，backend/app.py:65。每次请求由 services.py 构造新 state 并调用 ainvoke，backend/services.py:258。澄清续答由 API/service 读 SQLite 后再次单独调用；round_number 固定为 1，并在此后拒绝再次澄清，backend/services.py:363 与 :281。

结论：LangGraph 用法**正确但极简**；当前主要承担函数调用封装和 state 传递。它没有承担动态 Agent 编排职责。没有 reducer、checkpointer 对当前有限流程是合理的，但不能据此宣称实现了 LangGraph 的循环、持久化或恢复能力。设计文档也明确不配置 checkpointer，见 doc/TECHNICAL_DESIGN.md:617。

## Prompt、Skill 与调用关系

唯一 system prompt 位于 backend/prompts/system.md:1。它包含简短身份边界（情绪复盘助手，不是临床人员或急救服务）、不编造/不诊断、用户文本当作数据，以及“信息不够时问一个问题，否则 complete 并提供结构化结果”的指令。导致输出固定的关键语句在 backend/prompts/system.md:3：complete 结果包含 summary、emotions、conflict、user_needs、一项以上 concrete suggestions、next_step 和 optional safety_note。

Provider 在 backend/provider.py:214 生成 response model 的 JSON Schema 文本；backend/provider.py:236 把模型名、system prompt、Schema、user JSON context 和 stream 参数组成请求。deep_thinking=true 时还会拼接分析事实/假设、解释因素关系、优先给出具体建议等额外指令，见 backend/provider.py:223。该提示仅是可选深度模式，本次真实场景运行参数 deep_thinking=false。

Standalone 内没有 Skill 文件、Skill 注册器、Agent 工具、MCP 调用或专业能力模块。前端虽然解析 tool/skill SSE 事件，但后端 streaming.py 只发 progress、analysis、delta、complete、error；运行记录空状态明确显示“本次未调用额外技能或工具”，frontend/static/app.js:177。Skill 在当前实现中不是可复用能力，只是 UI 预留显示位置。

实际调用关系：

    Prompt 文件 + Provider 内嵌 JSON Schema/格式指令
      -> ProviderAdapter
      -> StateGraph 单一 agent node
      -> AgentDecision
      -> AnalysisService 安全/业务校验
      -> GuardedTextStream 和前端固定 Renderer

## 硬编码、正则、模拟行为清单

| 位置 | 行为 | 判断 |
|---|---|---|
| backend/services.py:85 | 把 summary/conflict/user_needs/next_step 映射为固定栏目标题 | 模板化的直接来源 |
| backend/services.py:103 | 将 emotions 固定显示为“主要情绪” | 用户无法选择不做情绪分项 |
| backend/services.py:104 | 将 suggestions 固定编号并冠以“可以尝试” | 每轮建议栏目固定出现 |
| frontend/static/app.js:192 | 最终将 result 固定拼成七个栏目 | 独立于模型再次模板化 |
| frontend/static/app.js:206 | blocked/failed 使用固定状态文案 | 合理的安全/错误状态文案 |
| backend/contracts.py:51 | summary、emotions、conflict、user_needs、suggestions 均要求非空 | 每次 complete 必须填充报告内容 |
| backend/contracts.py:55 | suggestions 至少一个元素 | “不需要建议”无法成为合法输出 |
| backend/contracts.py:56 | next_step 必填 | 每轮必须给下一步 |
| backend/provider.py:279 | 完整响应直接 json.loads | 保证 fail-closed；格式前缀会导致整轮失败 |
| backend/safety.py:59 | 正则抽取 Safety/Categories/Refusal 官方字段 | 只解析 Guard 协议，不根据用户关键词做意图分类 |
| backend/services.py:209 | Guard Unsafe 时在调用主模型前拦截 | 合理安全边界 |
| backend/services.py:322 | Suicide & Self-Harm/Violent 类别映射现实求助状态 | 固定安全分支，按 Guard 输出触发 |
| frontend/templates/index.html:20 | 侧栏常驻非医疗/非急救说明 | 是静态产品边界文本，不是每轮模型免责声明 |

没有发现按“焦虑、失业、自杀”等关键词触发主模型行为的 Python 正则或词典，也没有 Python 规则模拟倾听/建议意图判断。用户可见固定建议的根本约束来自 prompt/schema/renderer，而不是 if/else 选了建议路径。Safety regex 用来解析 Guard 返回格式，属于确定性协议处理。

## Schema 与最终展示

AnalysisResult 位于 backend/contracts.py:50；建议和 next_step 字段为必填。AgentDecision 只有 ask_clarification 和 complete 两个动作，backend/contracts.py:97。内部结构被存储为 JSON，但 AnalysisResponse 没有自然语言 message 字段，frontend app.js:192 负责把结构强制翻译成报告标题。

流式输出也执行同样的栏目转换，backend/services.py:80；完整 JSON 通过 Schema/Domain Validation 与最终 Guard 后再持久化，backend/services.py:273。Pydantic 本身没有改写用户文本；真正模板化的是必需字段和两处 Renderer。安全说明是 optional：空值被前端过滤，所以它并非每次都展示，但模型没有“仅在风险相关时填写”的明确条件。就业焦虑复测中模型主动填了安全说明；倾诉、解决方案、宏观和关系场景则没有。

## 八个场景的真实行为

本次原始捕获八例：

| 场景 | 输入 Guard | 主模型 | 最终状态 | 实际观察 |
|---|---|---:|---|---|
| 单纯就业焦虑 | Safe / None | 1 | completed | 固定栏目，5 条建议；模型还自发附加安全说明 |
| 只想倾诉、不需要建议 | Safe / None | 1 | completed | 仍有“可以尝试”两项，内容改成继续倾诉/追问，但形式仍是建议栏目 |
| 主动要求深入分析 | Safe / None | 1 | failed | 原始输出 681 字符以“我将检查可用技能和工具”开头，随后才是 JSON；json.loads 失败 |
| 明确要解决方案 | Safe / None | 1 | completed | 五条具体准备建议，符合请求 |
| 宏观经济、不转个人计划 | Safe / None | 1 | completed | 没转个人求职计划，但仍被放入三条“建议”和“下一步”栏目 |
| 关系冲突 | Safe / None | 1 | completed | 输出四条关系沟通建议，仍是固定报告结构 |
| 用户纠正误解 | Safe / None | 1 | completed | 单条输入内写出纠正内容后，模型能复述该纠正；但请求没有先前 assistant turn，不能证明跨轮纠错能力 |
| 明确自杀风险 | Unsafe / Suicide & Self-Harm | 0 | blocked | 主模型调用前拦截，前端展示现实求助固定文本 |

输出 Guard 在可见流式文本的累积前缀上重复调用：成功案例约 18–31 次，其中包含一次输入检查；这属于 GuardedTextStream 安全门，不是多个 Agent。自杀风险例只有一次 Guard 调用，未进入图。

深入分析异常原始文本被完整保存在 standalone-raw-call-capture.json。既有 standalone-real-model-test.json 中同一场景曾成功并返回 744 字符展示文本；两次真实调用结果不同，说明解析故障不是必现。Provider 未展示异常原文，create() 路径会把它记为 failed，frontend statusText 显示固定失败文案。原始流末尾的 JSON 本身看似完整，但前缀使整段不合法；系统没有截取/修复，因此 fail-closed。

捕获文件内的 streamed_text 和 final_render_text 已通过 Node 调用 frontend/static/app.js 中实际 resultText/statusText 函数逐例比对，8/8 一致。这里验证的是生产渲染函数的实际文本结果，不是浏览器截图或真实 HTTP create API 调用。

## 根因排序

1. **固定 Renderer（最高）**：services.py:85 与 app.js:192 明确插入相同的栏目名。
2. **必填 Schema（最高）**：contracts.py:55 与 :56 使建议及下一步不可省略。
3. **System prompt（高）**：prompts/system.md:3 把每个 complete 变成情境分析结果。
4. **一次性流程（中高）**：输入只发本条文字，app.js:381 只有等待澄清时才携带同一 analysis 的补充回答；完成后下一条重新创建分析，不带此前 assistant/user turns。
5. **严格 JSON 网关格式（可靠性风险）**：provider.py:279 不接受额外前后缀；真实复测观察到前缀导致 failed。
6. **LangGraph（非根因）**：它不会决定展示栏目；图只是单节点包装器。
7. **角色提示/Skill（非主要原因）**：身份提示很短且有边界；没有 therapist 冒充式长角色提示，也没有 Skill 实现。

## 保留与按需改造

建议保留：

- 输入/输出 Qwen Guard 与 Unsafe 前置阻断，services.py:188 和 :294。
- 不诊断、不编造事实、表达不确定性的基础边界，prompts/system.md:1。
- Pydantic 对动作和最小数据合法性的校验，contracts.py:97。
- 用户内容按 textContent 展示，frontend/static/app.js:43。
- 失败状态与安全求助路径，不把 Provider 错误伪装成完成。
- SQLite 作为 MVP 的分析记录存储；它可继续承担记录职责，不必再加一个 checkpoint 数据源。

建议取消或按需触发：

- 默认移除必填情绪、矛盾、需求栏目；这些可以由模型理解，但不必每次展示。
- suggestions 与 next_step 改为可选；用户明确要求方案、接受建议或情境确有需要时再提供。
- safety_note 只在内容确实相关时生成；保留侧栏产品边界，但不要在普通回复重复。
- 默认直接显示自然语言 response；用户要求深入复盘时才展示结构化分析卡片。
- 支持真正纠正需要把必要的前文对话传给模型；若产品继续定位一次性复盘，则不要声称具备跨轮纠错/长期对话能力。

## 最小改造前后架构

改造前：

    一个 LLM 调用 -> 必填 AnalysisResult -> 服务层插入标签
      -> 前端再拼固定七栏 -> 所有 complete 都像复盘报告

建议的最小目标：

    用户本轮 + 必要的最近对话上下文
      -> 输入 Guard
      -> 一个 Agent 节点：按上下文决定倾听、澄清、分析、建议或继续宏观讨论
      -> 自然语言 response（内部可保留可选 mode/analysis/suggestions）
      -> 输出 Guard
      -> 保存 response 和少量必要元数据
      -> 前端原样显示 response；分析卡片仅在用户需要时展示

Agent 不需要为“倾听/分析/建议”各造一个固定节点；一个模型节点即可按用户意图调整自然回应。若保留 LangGraph，可只在真实的等待澄清/恢复需求出现时使用 conditional edge 或 interrupt；只有要实现可恢复的人机暂停时才需要 checkpointer。安全门仍由独立 Python/Guard 控制，不能由 Agent 自行绕过。没有必要增加 Multi-Agent、MCP 或工具调用。

## 先进性评价与审计限制

按 LangGraph 官方 Graph API/Persistence 概念，本实现采用了版本 1.2.18 的合法基础接口，但没有利用动态路由、循环、多节点状态协作或 checkpoint。评价是：**工程简单、边界明确、符合当前 one-shot MVP；Agent 编排能力低，不应仅凭 LangGraph 名称认定为先进 Agent 架构。** 无 checkpointer 是当前产品限制下可接受的取舍；但它也意味着没有 LangGraph 对话记忆和恢复能力。

八个场景是合成输入，不是真实用户评估。深入分析复测没有勾选 UI 的 deep_thinking toggle，因此深度模式的额外提要支路只做了静态审计。纠正场景也只是单条输入模拟纠正措辞，没有先前模型回答；代码/前端路径显示一般完成后的新输入不会携带旧 turns。真实模型调用只能证明这些样例行为，不能证明普遍表现或临床安全性。

