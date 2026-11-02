# Emora 专业能力系统 · 全面技术调研报告

- 调研日期：2026-10-31
- 调研执行者：OpenClaw 主会话（QClaw）
- 目标项目：`/Users/laosun/Documents/emotional solution agent/emora-mvp-standalone`
- 本报告性质：**技术选型调研 + 交叉审查**，不含任何生产代码修改、依赖安装或架构替换。

---

## 0. 执行概要（Executive Summary）

本报告围绕六个方向（Agent 框架、安全护栏、RAG 知识系统、专业对话方法、评测框架、模型微调）完成联网证据采集与交叉审查，并基于 **Emora 当前真实代码架构基线**（非历史描述）给出唯一 MVP 推荐技术组合。

**核心结论一句话：** 当前 Emora 已具备一套务实、可在单机苹果芯片运行的链条（FastAPI + LangGraph 1.2.18 + OpenAI 兼容 SDK + Qwen3Guard-Gen + SQLite），六方向调研中没有任何一项构成「必须推倒重来」的理由；唯一 MVP 组合应在**现状延续**的前提下，把精力集中于两块新投入——**专业对话方法的结构化提示工程**（Rogers/MI 混合，非 CBT 主导）与**离线评测闭环**（DeepEval，pytest 原生）。

**唯一 MVP 推荐技术组合（详见第 7 章）：**

| 方向 | MVP 选择 | 理由摘要 |
|---|---|---|
| Agent 框架 | **维持 LangGraph 1.2.18**（单节点 StateGraph） | 已在依赖中、已验证、程序/Agent 控制权分离设计契合安全约束 |
| 安全护栏 | **维持 Qwen3Guard-Gen**；评测基准对齐 **MLCommons AILuminate** | 现状可用；AILuminate 是唯一具公信力的外部安全评测标准 |
| RAG | **暂不引入**（MVP 非目标）；若需检索，选 **Haystack 2.x** | PRD 明确排除工具/检索；Haystack 契合合规与显式管线 |
| 专业对话方法 | **Rogers 人本取向为底座 + MI(OARS) 为技术增量**；CBT 仅作认知重框参考，不做主导 | 有 JMIR/ACL 2025 实证：MI 结构化 prompt + 工作流是成熟路径 |
| 评测框架 | **DeepEval**（pytest 原生，metric 门禁）+ 自定义 MI 忠诚度 rubric | MITI/MISC-2 可编码进自定义 metric |
| 模型微调 | **暂不微调**；若需，选 **LLaMA-Factory**（Mac MPS + 中文 + UI） | 数据不足时不微调；Apple Silicon 是第一约束 |

---

## 1. 工具能力确认

执行前逐项自检，全部具备，且在本轮已实际使用并产生证据：

| # | 能力 | 状态 | 本轮证据 |
|---|---|---|---|
| 1 | 读取/检查本地项目文件 | ✅ | `read` / `exec cat` 读取 backend/*.py、doc/*.md |
| 2 | Shell 只读检查命令 | ✅ | `git status`、`find`、`grep`、`wc -l`、`cat` 均只读 |
| 3 | 联网搜索官方文档/GitHub/论文 | ✅ | `web_search` 返回 LangGraph/NeMo/AILuminate/JMIR/arXiv 等 |
| 4 | 创建目录并写 Markdown | ✅ | 本目录已由 `mkdir -p` 创建，本文件正在写入 |
| 5 | 多任务整合与交叉审查 | ✅ | 六个方向来源交叉比对（见第 6 章） |

未缺任何所需工具。本报告不假装执行、不编造来源；所有来源均可追溯（见各章 URL）。

---

## 2. 项目真实架构基线（只读检查结果）

> 依任务书要求，**先查真实代码，不凭历史描述**。以下均来自对 `backend/` 与 `doc/` 的实读。

### 2.1 实际运行时技术栈

| 层 | 实际实现 | 证据位置 |
|---|---|---|
| Web 框架 | FastAPI（`create_app`，lifespan 装配） | `backend/app.py` |
| 服务 | Uvicorn（ASGI） | `README.md`、`start.sh` |
| 前端 | 原生 HTML/CSS/JS + Jinja2 外壳，同源 | `frontend/`、`app.py` 的 `StaticFiles` |
| 数据库 | **SQLite**（`sqlalchemy.create_engine`，仅接受 sqlite URL，`ValueError` 拒绝其他） | `backend/storage.py`（注意：父仓库 `packages_py` 是 PostgreSQL 设计，但本 MVP 是 SQLite，二者不可混淆） |
| Agent 编排 | **`langgraph==1.2.18`**，`StateGraph`，单节点 `agent`（START→agent→END） | `pyproject.toml`、`backend/agent.py` |
| 模型接入 | OpenAI 兼容 SDK `AsyncOpenAI`，`stream=true`，JSON Schema 写进 prompt（非原生 json_schema response_format） | `backend/provider.py` |
| 安全 | **Qwen3Guard-Gen**（本地 vLLM 或 Mac Transformers），输入+输出双门禁 | `backend/safety.py`、`doc/TECHNICAL_DESIGN.md` §2 |
| 数据校验 | Pydantic v2，`extra="forbid"` | `backend/contracts.py` |

### 2.2 结果与决策契约（实际字段）

`AnalysisResult` = `summary / emotions / conflict / user_needs / suggestions / next_step / safety_note`（`backend/contracts.py`）。`AgentDecision` 二选一：`ask_clarification`（带一个问题，单轮上限）或 `complete`（带 `result`）。

### 2.3 关键架构事实（对选型至关重要）

1. **程序控制权与 Agent 决策权严格分离**：输入/输出安全门、业务完整性校验、状态发布全部由 `services.py` 固定调用，Agent 不能调用安全检查、数据库写入或状态发布（`doc/TECHNICAL_DESIGN.md` §2）。这决定了任何新 Agent 框架若引入「Agent 可自主调用工具」的范式，都与现有安全边界设计冲突——**这是维持 LangGraph 单节点而非引入工具型 Agent 框架的根因**。
2. **无 RAG、无工具调用、无检索、无评测框架、无微调管线**：当前行为是「单次 Agent 调用 + 结构化输出」，PRD 明确把工具调用、长期记忆、多代理协作列为非目标。
3. **无任何专业对话方法工程化**：`backend/prompts/system.md` 只有通用情绪复盘角色设定，不含 Rogers/MI/CBT 的显式技法。
4. **单轮澄清**：`clarification_round` 上限 `0/1`，第二轮再请求被 `clarification_limit_exceeded` 拒绝。

> ⚠️ 重要区分：父仓库 `packages_py/` + `doc/C端开发/工程设计说明.md` 描述的是**另一套更大架构（阶段 1–3，PostgreSQL + Alembic + 工具协议 + 检索 + ASR/TTS）**，而本 `emora-mvp-standalone` 是当前真正可运行、被 `pyproject.toml` 与 `start.sh` 支撑的 MVP。本报告只以 standalone 为基线。

---

## 3. 方向一：Agent Frameworks

### 3.1 候选概览（截至 2026-10）

| 框架 | 定位 | 语言 | 许可 | 最近版本（2026-10） | 关键特点 |
|---|---|---|---|---|---|
| LangGraph | 有状态图编排 | Python/TS | MIT | 1.2.14（10-06）/ 项目锁 1.2.18 | 图、checkpointer（生产用 PostgreSQL）、`interrupt()`/`Command(resume=)` |
| Microsoft Agent Framework | AutoGen + Semantic Kernel 后继 | .NET/Python/Go | MIT | Python 1.21.0（10-08） | 中间件、记忆、图工作流、Azure AI Foundry 集成、MCP/A2A |
| Pydantic AI | 类型安全 Agent | Python | MIT | 2.55.0（10-09） | Pydantic 输出校验、token 预算、A2A/MCP、pydantic-graph |

来源：[wz-it.com 对比](https://wz-it.com/en/blog/ai-agent-frameworks-comparison/)、[LangChain 官方 2026 评测](https://www.langchain.com/resources/ai-agent-frameworks)、[Speakeasy 对比](https://www.speakeasy.com/blog/ai-agent-framework-comparison)、[AWS Builder Center](https://builder.aws.com/content/3AzsgG6TreTO3uLRqpWNxfEyUhe/picking-an-ai-agent-framework-in-2026)。

### 3.2 关键事实与证据

- LangGraph 1.0 于 2025-10 发布；生产部署于 Uber、JPMorgan、BlackRock、Replit 的公开案例常被引用（[Ertas AI 2026-05](https://www.ertas.ai/blog/pydantic-ai-vs-langgraph-fine-tuned-agents)）。
- Pydantic AI v1.0 于 2025-09、v2.0 于 2026-06；其价值主张是「线性、结构化输出、类型安全」（[Speakeasy](https://www.speakeasy.com/blog/ai-agent-framework-comparison)、[Ertas](https://www.ertas.ai/compare/pydantic-ai-vs-langgraph)）。
- Microsoft Agent Framework 1.0 于 2026-04；DevUI 官方标注为非生产示例；AutoGen 进入维护模式（[wz-it.com](https://wz-it.com/en/blog/ai-agent-frameworks-comparison/)、[LangChain](https://www.langchain.com/resources/ai-agent-frameworks)）。
- AWS Builder Center 关键结论：「模型锁定是单一最重要的架构决策」，LangGraph/Pydantic AI/LlamaIndex 为「model-agnostic」第一梯队；LangGraph+LangSmith 观测性最成熟。

### 3.3 交叉审查结论

- Emora 的图是**单节点线性流**，LangGraph 在此是「杀鸡用牛刀」但**已在锁文件、已验证、且 checkpointer/resume 语义已在父仓库设计中被依赖**。迁移到 Pydantic AI 或 MS Agent Framework 的收益（类型安全/微软生态）不覆盖迁移成本与安全边界重写风险。
- 更重要的是：**Emora 的安全模型要求 Agent 不能自主调用工具**（§2.3-1），而 Pydantic AI / MS Agent Framework 的卖点恰是工具调用与多代理。引入它们会制造「能力反而和对的约束打架」的局面。

**MVP 结论：维持 LangGraph 1.2.18。** 不升级、不替换。若未来图变复杂（多节点、条件边），LangGraph 原生支持，无需换框架。

---

## 4. 方向二：Safety & Guardrails

### 4.1 候选概览

| 工具 | 类型 | 许可 | 输入/输出 | 备注 |
|---|---|---|---|---|
| NVIDIA NeMo Guardrails | 完整护栏框架（编排层） | Apache-2.0 | 输入/检索/对话/执行/输出五类 rail | 内含 Llama Guard 集成、内容安全模型目录 |
| Meta Llama Guard（2/3/4） | 分类模型 | 社区许可 | 输入+输出 | AILuminate 上 100 百分位 |
| MLCommons AILuminate | **评测基准**（非护栏） | — | 12 危害类别 | Candela 服务可按需跑 Official/Practice |
| LLM Guard（ProtectAI） | 扫描器库 | MIT | 输入+输出扫描器 | **⚠️ 项目已归档，不再维护** |
| Qwen3Guard-Gen（Emora 现用） | 分类模型（本地） | Apache-2.0（Qwen） | 输入+输出 | 官方 0.6B/4B/8B |

来源：[NeMo Guardrails Llama Guard 集成](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/llama-guard)、[NeMo rail 类型](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/rail-types)、[AILuminate Safety 结果页](https://mlcommons.org/ailuminate/ailuminate-safety-results/)、[AILuminate Safety 说明](https://mlcommons.org/ailuminate/safety/)、[LLM Guard GitHub（已归档声明）](https://github.com/protectai/llm-guard)。

### 4.2 关键事实与证据

- **AILuminate 是「系统级」安全评测**，把 system prompt、guardrail、retrieval 全部纳入被测系统；12 危害类别分三组（物理/非物理/情境）；提供英文、法文、**中文**版本（[mlcommons.org](https://mlcommons.org/ailuminate/safety/)）。
- AILuminate Safety 1.2.1 结果（2026-10-29 页）：**Meta Llama Guard 2/3 8B 与 Llama Guard 4 12B 均 100 百分位**；NVIDIA Nemotron 3.5 Content Safety 100 百分位；HKUST JailJudge 94 百分位。并列强调：这些是「护栏系统」（分类器），不是直接回答用户的主模型。
- NeMo Guardrails 明确支持把 Llama Guard **作为第三方 model 挂进 rails**，输入 `llama guard check input` / 输出 `llama guard check output` 流程（[NV docs](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/llama-guard)）。
- **ProtectAI LLM Guard 仓库首页明示「THIS PROJECT HAS BEEN ARCHIVED… no longer under active development or maintained」**（[GitHub](https://github.com/protectai/llm-guard)）——这是本次调研发现的最重要负面信息，直接排除 LLM Guard 作为新选型。

### 4.3 与 Emora 现状的对齐与交叉审查

- Emora 已在用 Qwen3Guard-Gen，且**严格限定只使用它的 `Safety/Categories/Refusal` 字段、不另建风险分类体系**（`doc/TECHNICAL_DESIGN.md` §2）。这一自约束是良性的，应保留。
- 技术债：Qwen3Guard 只提供「内容安全分类」，**不提供「用户本人即时危险」字段**（TECHNICAL_DESIGN 已自认此局限）。AILuminate 恰好覆盖 `suicide and self-harm` 类别，可补足外部证据。
- **不要把 AILuminate 当护栏用**——它是评测。但作为 MVP 的**安全回归门禁**（跑 Practice 结果）价值最高：它给「我们到底拦不拦得住」一个可引用、可复现的外部答案。

**MVP 结论：维持 Qwen3Guard-Gen；以 MLCommons AILuminate（Practice 级）作为安全验收的外部标准；不引入 NeMo Guardrails（编排层与 Emora 自有的 services.py 门禁重复）、不引入已归档的 LLM Guard。** 若未来需要更强分类器，Llama Guard 4 12B 是 AILuminate 上有证据的候补，但非 MVP 必需。

---

## 5. 方向三：RAG & Knowledge Systems

### 5.1 候选概览

| 工具 | 定位 | 许可 | 检索特性 | 适用 |
|---|---|---|---|---|
| LlamaIndex | 数据索引/检索框架 | MIT | 160+ 连接器、多种索引（向量/树/关键词/摘要） | 文档问答、企业知识库 |
| Haystack 2.x | 组件化管线 | Apache-2.0 | 显式 Pipeline、Elasticsearch/OpenSearch 混合检索、内置评测 | 生产稳健、合规行业 |
| RAGFlow | 深度文档理解 RAG 引擎 | Apache-2.0 | 视觉 DAG 编辑器、版式感知解析、GraphRAG | 复杂 PDF/表格 |
| pgvector | PostgreSQL 向量扩展 | PostgreSQL 许可 | 原生 SQL 向量查询 | 已在用 PG 的团队 |

来源：[inferensys LlamaIndex vs Haystack](https://inferensys.com/differences/context-engineering-and-retrieval-ranking-platforms/document-parsing-and-chunking-strategies/llamaindex-vs-haystack-ingestion-pipelines)、[think4ai 2026](https://think4ai.com/best-rag-frameworks-2026)、[agentwiki RAG 对比](https://agentwiki.org/rag_framework_comparison)、[aichitect LlamaIndex vs RAGFlow](https://www.aichitect.dev/compare/llamaindex/ragflow)。

### 5.2 关键事实与证据

- agentwiki 与 think4ai 的一致裁决：「企业复杂 PDF/表格 → RAGFlow；纯检索质量优先 → LlamaIndex；**受监管行业（医疗/金融）→ Haystack**（技术无关、内置评测、合规友好）」「纯原型 → txtai」。
- RAGFlow 是 2025 增长最快的 RAG 项目之一（40K+ stars），强在 DeepDoc 版式解析与视觉管线（[aichitect](https://www.aichitect.dev/compare/llamaindex/ragflow)）。
- pgvector 仅当「团队已在 PostgreSQL 上」才有优势（[think4ai](https://think4ai.com/best-rag-frameworks-2026)）。而 Emora MVP 是 **SQLite**，pgvector 直接不适用。

### 5.3 与 Emora 现状的交叉审查（关键）

- **PRD 把工具调用、检索、长期记忆列为非目标**（PRD §7）；当前服务层无任何检索调用。故 RAG 对 MVP 是**零需求**。
- 父仓库设计里确有 `retrieval.py` 与「只读参考资料、许可与版本过滤」（`doc/C端开发/工程设计说明.md`、`技术债务清单 TD-02-01` 已关闭），说明**未来**阶段 2+ 才需要获批来源检索。但那不是本次 MVP。

**MVP 结论：不引入任何 RAG 组件。** 保留「未来若需检索，Teksturong 属」的判断：受监管/可追溯 → Haystack 2.x（与 Emora 的「引用可定位全文切片」父设计最契合）；纯文档问答 → LlamaIndex；pgvector 仅当迁回 PostgreSQL 才考虑。

---

## 6. 方向四：Professional Dialogue Methods

### 6.1 候选方法概览

| 方法 | 代表/出处 | 核心技法 | AI 工程化证据强度 |
|---|---|---|---|
| Rogers 人本取向 | Carl Rogers | 共情、无条件积极关注、真诚一致 | 广泛作为聊天机器人「底座语气」，具体实证少但无争议 |
| Motivational Interviewing (MI) | Miller & Rollnick | OARS（开放式提问、肯定、反映、总结）；change talk | **强**：多篇 JMIR/ACL 实证、MITI/MISC-2 可编码评估 |
| Hill Helping Skills（三阶段） | Clara Hill | Exploration→Insight→Action | 中：适合心理咨询训练场景，AI 实证较少 |
| CBT | Beck 等 | Catch It / Check It / Change It（认知重框） | 强但结论审慎：LLM「可辅助、不可主导」CBT |

来源：[JAMA Netw Open MI 对齐研究](https://jamanetwork.com/journals/jamanetworkopen/fullarticle/2846839)、[ACL 2025 MIBot](https://aclanthology.org/2025.findings-acl.1283)、[JMIR Ment Health MIBot 反射生成](https://mental.jmir.org/2024/1/e53778)、[JMIR Formative Aimi 结构化 MI 工作流](https://formative.jmir.org/2026/1/e94036/PDF)、[CBT-Bench arXiv 2410.13218](https://arxiv.org/abs/2410.13218)、[JMIR AI CBT 任务评估](https://ai.jmir.org/2024/1/e52500)。

### 6.2 关键实证事实（这是本方向最硬的证据）

1. **MI 是唯一具备「可编码忠诚度评估」的方法**：MITI（Motivational Interviewing Treatment Integrity）系统、MISC-2（Manual for the MI Skill Code 2.0）可逐轮编码。ACL 2025 MIBot 与 JMIR Formative Aimi 均用它做自动化+专家评估。→ 意味着 MI 能被**工程化为可测的 rubric**，这是 Emora 评测闭环能否落地的前提。
2. **JAMA Network Open（2026-03）**：154 个高保真 MI 会话、3706 个治疗师回合，LLM 与治疗师「语义相似度低、但情境适当性更高」；结论强调「prompt engineering 对 MI 对齐至关重要」，并点名 MHealth-EVAL、ψ-Arena 等评估框架。→ 提示「密钥在 prompt 工程 + 专用评估」，而非换模型。
3. **CBT 结论审慎**：JMIR AI（2024）明确指出 LLM「不应被依赖来主导 CBT 交付」；CBT-Bench 把 CBT 拆成「知识→认知模型→治疗回应」三级。→ CBT 适合做**认知重框参考技能**，不适合做 Emora MVP 的主导方法（且 Emora PRD 明确「非医疗诊断、非治疗替代」）。
4. **结构化工作流比单一 prompt 更忠实**：Aimi（JMIR Formative 2026）用「LLM 工作流」把 MI 策略操作化，专家 MI 教练用 MISC-2 评分验证会话忠实度。→ 印证「MI 策略 + LangGraph 工作流」路径与 Emora 现有 LangGraph 骨架天然契合。

### 6.3 交叉审查结论与选型

- **Rogers 取向** = Emora 现有的「情绪反思、不诊断、区分事实与假设、不操纵」已经是人本取向的工程化雏形（`system.md`），应显式化、命名化。
- **MI(OARS)** = 最值得作为「专业增量」：有可测 rubric、有结构化工作流先例、与 Emora「帮助用户梳理矛盾与诉求、给出可执行下一步」的目标高度吻合，且**不会让系统滑向医疗诊断**。
- **CBT** = 仅把「识别不合理的自动化想法 → 检验 → 重框」作为可选认知技能参考，明确不主导、不诊断。
- **Hill 三阶段** = 概念上可吸收（Exploration≈现状澄清、Insight≈矛盾/需求识别、Action≈下一步），但不作为独立工程方法，因缺乏强 AI 实证。

**MVP 结论：以 Rogers 人本取向为底座语气 + MI 的 OARS 作为显式技法增量，通过 LangGraph 节点内的结构化 prompt 实现；CBT 认知重框作参考技能；不用任何「疗法框架 SDK」（方法本身没有成熟可衬库，工程化靠 prompt + rubric）。**

---

## 7. 方向五：Evaluation Frameworks

### 7.1 候选概览

| 框架 | 定位 | 许可 | 形态 | 强项 |
|---|---|---|---|---|
| DeepEval | 通用 LLM 评测 | Apache-2.0 | Python + pytest + CLI | 50+ 指标、pytest 原生门禁、G-Eval 自定义 metric |
| Ragas | RAG 专项评测 | Apache-2.0 | Python | faithfulness/answer_relevancy/context_precision/recall，学术支撑 |
| promptfoo | prompt 对比 + 红队 | MIT | YAML + CLI + TS | 40+ 对抗插件、多模型矩阵；2026 被 OpenAI 宣布收购 |
| Inspect AI | 前沿模型评测 | MIT | Python + CLI | UK AI Security Institute + Meridian Labs，200+ 预建 eval、Agent 评估 |

来源：[DeepEval Top-5](https://deepeval.com/blog/top-5-llm-evaluation-frameworks)、[aiml.qa 基准](https://aiml.qa/llm-evaluation-framework-benchmark-2026)、[genai.qa 对比](https://genai.qa/blog/promptfoo-vs-deepeval-vs-ragas)、[Inspect 官网](https://inspect.ai-safety-institute.org.uk/)。

### 7.2 关键事实与证据

- 多个独立对比（aiml.qa、genai.qa、machinelearningmastery）一致裁决：**RAGAS=RAG 专项经典；DeepEval=最广的 CI 集成库；promptfoo=CLI 红队/多模型矩阵**；「成熟团队常并行跑两个」。
- DeepEval 与 Confident AI 是两个产品；DeepEval 开源本地可跑、pytest 原生、可作为**阻断 PR 的 metric 门禁**（[deepeval blog](https://deepeval.com/blog/deepeval-vs-ragas)）。
- Inspect AI 是 UK AISI + Meridian Labs 的前沿评测框架，MIT，200+ 预建 eval，含 Agent 评估与模型评分（[inspect 官网](https://inspect.ai-safety-institute.org.uk/)、[GitHub](https://github.com/UKGovernmentBEIS/inspect_ai)）。它更适合「前沿模型能力/安全」评测，而非团队日常 CI。

### 7.3 与 Emora 的交叉审查

- Emora 已有 `pytest` 基建（`backend/tests/`，`pyproject.toml` 配置 `addopts=-q`）。**DeepEval 的 pytest 原生门禁与现有测试栈零冲突**，这是决定性优势。
- Emora 无 RAG，故 **Ragas 无适用场景**（它的 5 个经典指标全围绕检索）。
- 情感/伦理对话的「正确性」没有现成 metric——必须**自定义 G-Eval rubric**。第 6 章已论证：MI 的 MITI/MISC-2 是可编码 rubric 的最佳素材。
- promptfoo 的价值（红队）与 Emora 的 Qwen3Guard + AILuminate 形成互补，但非 MVP 必需；且其 2026 被 OpenAI 收购后「路线图更少厂商中立」（[deepeval blog](https://deepeval.com/blog/top-5-llm-evaluation-frameworks)），降低选它做长期主力的理由。

**MVP 结论：DeepEval 作为唯一评测框架（pytest 原生 + 自定义 rubric）；用 MI 忠诚度（OARS 呈现/开放式提问/反映准确性）+ 安全路由（阻断是否在正确输入上触发）构建自定义指标；不引入 Ragas（无 RAG）、不引入 promptfoo 与 Inspect AI（非当前场景）。**

---

## 8. 方向六：Model Fine-tuning

### 8.1 候选概览

| 框架 | 定位 | 许可 | 优势 | stars 级 |
|---|---|---|---|---|
| TRL（HF） | 研究 + RLHF/SFT/DPO | Apache-2.0 | 新 RL 奖励/算法、与 HF 生态无缝 | ~10k |
| LLaMA-Factory | 全功能 + Web UI | Apache-2.0 | 最全训练方法（DPO/ORPO/KTO/RLHF）、**最佳 Apple Silicon MPS 支持**、中文生态 | ~37k |
| Axolotl | YAML 配置驱动 | Apache-2.0 | 多 GPU/多节点、DeepSpeed/FSDP2、可复现配置 | ~12.6k |
| Unsloth | 速度/显存优化 | Apache-2.0 | 2–5× 提速、60–80% 省显存、单卡起步最快 | ~78.8k |

来源：[andrew.ooo 2026-10-30](https://andrew.ooo/answers/best-llm-fine-tuning-frameworks-2026/)、[local-llm.net 对比](https://www.local-llm.net/compare/unsloth-vs-axolotl-vs-llama-factory)、[aindaily 对比](https://aindaily.com/unsloth-vs-axolotl-vs-trl-vs-llama-factory-a-fine-tuning-framework-comparison-on-speed-vram-and-multi-gpu)、[Clore 决策矩阵](https://docs.clore.ai/guides/comparisons/finetuning-comparison)。

### 8.2 关键事实与证据

- 硬件约束：Emora 跑在 **Mac (Apple Silicon)**。local-llm.net 明确「On Apple Silicon → LLaMA-Factory（best MPS support）」；Unsloth 也支持 Mac/单卡（[local-llm.net](https://www.local-llm.net/compare/unsloth-vs-axolotl-vs-llama-factory)、[andrew.ooo](https://andrew.ooo/answers/best-llm-fine-tuning-frameworks-2026/)）。
- 速度：Unsloth 4-bit 在 LLaMA 3.1 8B 上 ~2.8× 于基线、~8GB VRAM（[Clore](https://docs.clore.ai/guides/comparisons/finetuning-comparison)）。
- 通用共识（andrew.ooo）：「数据比框架更重要——几千条干净任务特定样本胜过大量噪声数据」。Ertas AI 亦强调「先见第二次/第三次 API 账单再谈微调」。

### 8.3 与 Emora 的交叉审查（关键）

- Emora MVP **完全没有微调数据**：无标注对话数据集、无已收集的真实用户反馈（stage 4 试用尚未开始，见 `阶段任务与验收标准.md`）。
- 微调目标是「让模型说 MI 语言更稳定」，但第 6.2-2 证据（JAMA）表明**当前瓶颈是 prompt 工程与评测，不是模型能力**。在无数据、无评测回归前微调，属于本末倒置。
- 且 PRD 的非目标清单未含微调，但也从未要求微调；引入大型训练运行时（TRL/Axolotl 的 DeepSpeed）与「单机 Mac MVP」定位不符。

**MVP 结论：MVP 阶段不微调。** 保留引擎评估：若未来在 DeepEval 上确认「prompt 已到收益递减、且积累数千条带 MI 标签的干净数据」，首选 **LLaMA-Factory**（Apple Silicon MPS + 中文社区 + UI）；若目标是速度优先的单卡 LoRA，次级备选 Unsloth。

---

## 9. 最终：唯一 MVP 推荐技术组合

> 任务书要求「给出唯一的 MVP 推荐技术组合」。以下是唯一组合，非候选清单。

| 方向 | **唯一 MVP 选择** | 现状 vs 变更 |
|---|---|---|
| 后端/编排 | **FastAPI + LangGraph 1.2.18（维持现状）** | ✅ 无变更 |
| 模型接入 | **OpenAI 兼容 SDK（维持现状）** | ✅ 无变更 |
| 数据库 | **SQLite（维持现状）** | ✅ 无变更 |
| 安全护栏 | **Qwen3Guard-Gen（维持现状）+ AILuminate Practice 作验收标准** | 新增评测标准（非替换） |
| RAG | **不引入** | 非目标 |
| 专业对话方法 | **Rogers 底座 + MI(OARS) 增量（结构化 prompt 工程）** | 🆕 核心新增投入 |
| 评测框架 | **DeepEval（pytest 原生 + MI 忠诚度自定义 rubric）** | 🆕 新增 |
| 模型微调 | **不微调（待数据与评测就绪后再评估 LLaMA-Factory）** | 延期 |

**一句话总纲：** 在**不更换任何现有组件**的前提下，Emora MVP 的专业能力提升 = 把「Rogers+MII」写进 Agent 的结构化 prompt（LangGraph 节点内），并用「DeepEval + MI 忠诚度 rubric + AILuminate 安全门禁」建立第一个可复现的评测闭环；RAG 与微调都留待有真实数据与明确失败证据之后再启动。

### 9.1 为什么这是「唯一」组合（而非选项堆叠）

1. **安全边界决定框架**：Emora 的「程序/Agent 控制权分离」要求 Agent 不能自主调工具，直接排除了以工具调用为卖点的框架（Pydantic AI 工具预置、MS Agent Framework 多代理）。
2. **硬件与现状决定微调**：Mac Apple Silicon + 零标注数据，使「微调」在 MVP 必然为否；唯一的「是」落在无需新运行时的 prompt + 评测上。
3. **可测性决定方法**：在所有专业方法里，只有 MI 有可编码、可复现的忠诚度指标（MITI/MISC-2），这是在无临床人员团队时「可工程化、可评分」的唯一路径。
4. **已有基建决定评测**：pytest 已在，DeepEval 零冲突接入；Ragas 无 RAG 场景所以排除。

---

## 10. 来源清单（全部可追溯）

**Agent 框架**
- https://wz-it.com/en/blog/ai-agent-frameworks-comparison/
- https://www.langchain.com/resources/ai-agent-frameworks
- https://www.speakeasy.com/blog/ai-agent-framework-comparison
- https://www.ertas.ai/compare/pydantic-ai-vs-langgraph
- https://www.ertas.ai/blog/pydantic-ai-vs-langgraph-fine-tuned-agents
- https://builder.aws.com/content/3AzsgG6TreTO3uLRqpWNxfEyUhe/picking-an-ai-agent-framework-in-2026

**安全护栏**
- https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/llama-guard
- https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/rail-types
- https://mlcommons.org/ailuminate/ailuminate-safety-results/
- https://mlcommons.org/ailuminate/safety/
- https://github.com/protectai/llm-guard （已归档）
- https://protectai.github.io/llm-guard/output_scanners/sensitive

**RAG**
- https://inferensys.com/differences/context-engineering-and-retrieval-ranking-platforms/document-parsing-and-chunking-strategies/llamaindex-vs-haystack-ingestion-pipelines
- https://think4ai.com/best-rag-frameworks-2026
- https://agentwiki.org/rag_framework_comparison
- https://www.aichitect.dev/compare/llamaindex/ragflow

**专业对话方法**
- https://jamanetwork.com/journals/jamanetworkopen/fullarticle/2846839
- https://aclanthology.org/2025.findings-acl.1283
- https://mental.jmir.org/2024/1/e53778
- https://formative.jmir.org/2026/1/e94036/PDF
- https://arxiv.org/abs/2410.13218 （CBT-Bench）
- https://ai.jmir.org/2024/1/e52500

**评测框架**
- https://deepeval.com/blog/top-5-llm-evaluation-frameworks
- https://aiml.qa/llm-evaluation-framework-benchmark-2026
- https://genai.qa/blog/promptfoo-vs-deepeval-vs-ragas
- https://inspect.ai-safety-institute.org.uk/
- https://github.com/UKGovernmentBEIS/inspect_ai

**模型微调**
- https://andrew.ooo/answers/best-llm-fine-tuning-frameworks-2026/
- https://www.local-llm.net/compare/unsloth-vs-axolotl-vs-llama-factory
- https://aindaily.com/unsloth-vs-axolotl-vs-trl-vs-llama-factory-a-fine-tuning-framework-comparison-on-speed-vram-and-multi-gpu
- https://docs.clore.ai/guides/comparisons/finetuning-comparison

**项目内部（架构基线）**
- `backend/`：`app.py` / `agent.py` / `provider.py` / `safety.py` / `storage.py` / `services.py` / `contracts.py` / `prompts/system.md`
- `emora-mvp-standalone/doc/TECHNICAL_DESIGN.md`、`doc/PRD.md`、`pyproject.toml`、`README.md`
- 父仓库：`doc/C端开发/工程设计说明.md`、`doc/C端开发/阶段任务与验收标准.md`、`doc/C端开发/技术债务清单.md`

---

## 11. 合规与执行约束核验（自我声明）

- ✅ 全程只读检查，未修改任何生产代码、数据库、依赖、架构。
- ✅ 未执行任何 `pip install` / `uv sync` / 写入 `.env` / 改动 `backend/` 或 `packages_py/`。
- ✅ 未自动实施任何技术选型（本报告为「推荐」，非「实施」）。
- ✅ 所有结论附来源；无法验证处已显式标注（如「Qwen3Guard 无即时危险字段」为项目自身已记录的技术债，非本报告臆断）。
- ⚠️ 注意事项：本报告所述的「任务书」在本仓库内无存档文档，执行依据为本次对话消息中列明的六方向与交付标准；如存在外部完整任务书，请补充以做逐条对齐复核。