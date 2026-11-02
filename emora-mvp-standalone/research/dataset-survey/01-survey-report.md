# 数据集调研报告：中文情感支持 / 心理咨询 / 共情对话数据

- 调研日期：2026-11-02
- 执行主体：OpenClaw 主会话（QClaw）
- 目标项目：`emora-mvp-standalone`（Emora，情感支持与关系问题引导 Agent）
- 目录：`research/dataset-survey/`
- 性质：调研 + 物料下载；未修改任何生产代码/提示词/数据库/运行环境

---

## 0. 结论速览

| 数据集 | 语言 | 类型 | 授权 | 亲密关系覆盖 | 可下载 | Emora 适用性 |
|---|---|---|---|---|---|---|
| **CPsyCounD** | 中文 | 多轮咨询对话（**模型重构**） | CC-BY-4.0(GitHub)/CC-BY-SA-4.0(HF) | ✅ 有 Love&Marriage/Family 主题 | ✅ 已下载 | 研究多轮策略（非真实记录，需专家审核） |
| **CPsyCounR** | 中文 | 咨询案例报告（**原始真实报告**） | 自定义授权书（非商用+只读+有期限） | ✅ 有 | ⛔ 需申请 | 建知识库的最佳原始素材（需签署授权书） |
| **CPsyCounE** | 中文 | 多轮评测用例 | 同 CPsyCoun | ✅ 有 Love&Marriage | ✅ 已下载 | 评测集（共情/多轮评估） |
| **PsyDial-D1/D4** | 中文 | 长程多轮对话（**半真实重构**） | Apache-2.0（商用 OK） | ⚠️ 通用心理，非专门亲密关系 | ✅ 已下载 | 研究多轮沟通/澄清/引导 |
| **PsyDial-D101** | 中文 | 评测集（101 学术书对话） | Apache-2.0 | ⚠️ 含感情案例 | ✅ 已下载 | 评测 |
| **PsyDial-D0_m** | 中文 | 长程对话（客户端脱敏） | Apache-2.0（gated） | ⚠️ | ⛔ 需申请 | 研究 |
| **ESConv** | 英文 | 情绪支持对话（真实众包） | **CC-BY-NC 4.0（禁商用）** | ✅ 含 breakup with partner(239) | ⛔ 禁商用未下载 | 仅供研究参考，**不可进商用产品** |
| **Therapy Clearinghouse** | 英/中 | 方法/指南资料（非数据集） | 见各来源 | — | 部分 | 方法参考资料 |

**关键一句话**：**可直接商用的中文多轮数据是 PsyDial（Apache-2.0）与 CPsyCounD（CC-BY）；但两者都是「模型重构/半真实」对话，不能当真实专家记录；真实原始素材是 CPsyCounR（真实报告），但需签署授权书且限定非商用+只读。ESConv 是真实众包对话但 CC-BY-NC 禁商用。**

---

## 1. CPsyCoun

- **论文**：ACL 2024 Findings，arXiv:2405.16433
- **官方主页/GitHub**：https://github.com/CAS-SIAT-XinHai/CPsyCoun
- **HuggingFace**：https://huggingface.co/datasets/CAS-SIAT-XinHai/CPsyCounR（R，gated）；`/CPsyCoun`（D，cc-by-sa-4.0）

### 1.1 CPsyCounR（案例报告）
- **内容**：3,134 份心理咨询案例报告，来自中国知名心理社区「壹点灵」「Psy525」，经规则清洗 + **人工改写 + 人工校对**。格式六段：标题/类别/技术/案例简述/咨询经过/经验感想，**第三人称、不含具体对话**。覆盖九大主题（含 Love&Marriage 恋爱婚姻、Family Relationship 家庭关系）。
- **性质**：**原始真实案例报告**（非 LLM 生成）——这是本目录里唯一"原始真实专家素材"。
- **授权**：**自定义《文本数据（案例）研究使用授权书》**，HuggingFace gated。要点：仅限研究机构内部学术研究；非商用产品中数据**只读、不允许二次复制传播**；第三方须向课题组申请；**授权有效期 2024-09-25 ~ 2027-07-01**，到期需续期。
- **下载状态**：⛔ **需申请授权**（gated，需登录 HF 并接受条件 + 分享联系方式）。未绕过。

### 1.2 CPsyCounD（多轮咨询对话）
- **内容**：3,134 段多轮咨询对话，覆盖九大主题、七大咨询流派（精神分析/CBT/人本/家庭/后现代/整合等）。
- **性质**：⚠️ **模型重构资料**——论文方法 Memo2Demo 把"报告"两阶段转成"对话"，即**对话是 LLM 生成的，不是真实咨询逐字稿**。不能当真实专家会谈记录。
- **授权**：GitHub `LICENSE`=CC-BY-4.0；HF card=CC-BY-SA-4.0（**两处协议不一致，需记录并使用时以署名+SA 从严处理**）。
- **下载状态**：✅ 已下载。① GitHub `CPsyCounD/Data/case_*.txt`（3,084 个 txt，逐案例）；② HF `CPsyCounD.json`（3,134 条，`instruction/input/output/history` 格式）。

### 1.3 CPsyCounE（评测用例）
- **内容**：九大主题 × 各 5 例 = **45 个评测用例**（每个是"求助者/支持者"交替的完整多轮），含 Love&Marriage 主题。
- **下载状态**：✅ 已下载（GitHub `CPsyCounE/` 按主题分文件夹）。

### 1.4 亲密关系覆盖评估
CPsyCoun 三件套**明确含 Love&Marriage / Family Relationship 主题**，是四条指定资源里中文亲密关系覆盖最强的。但**对话层是重构的**，真实层在 R（需申请）。

---

## 2. PsyDial

- **论文**：ACL 2025 main，https://github.com/qiuhuachuan/PsyDial（浙大）
- **定位**：「首个完全开源的中文心理健康领域长程对话数据集」，平均 **37.8 轮/对话**。

| 子集 | 内容 | 规模 | gated | 大小 | 说明 |
|---|---|---|---|---|---|
| D101 | 评测集（人工从心理咨询专业书籍收集 101 对话） | 1,278 测试样例 | 否 | 4.2MB | `idx/messages/golden` |
| D0_m | 长程对话（仅来访者 utterance 脱敏） | 2,382 | **是(manual)** | 18.8MB | 需申请 |
| D1 | 长程咨询对话 | 2,382 | 否 | 33.7MB | `messages` |
| D4 | 长程咨询对话 | 2,382 | 否 | 35.5MB | `messages`（含咨询师 system 角色设定） |

- **性质**：⚠️ **半真实（semi-real）**——基于真实长程咨询对话，经 RMRR（Retrieve-Mask-Reconstruct-Refine）**隐私保护重构**。比 CPsyCounD 更接近真实（有真实咨询底本），但仍是重构产物，非原样逐字稿。含咨询师 system prompt（"虚拟心理咨询师小天"、人本/精神分析/CBT、前中后三期策略）。
- **授权**：Apache-2.0（**允许商用**）。D0_m 额外要求"acknowledge license + 不用于伤害人类受试的实验"。
- **下载状态**：✅ 已下载 D101 / D1 / D4（经 hf-mirror）；⛔ D0_m 需申请（gated manual）。**注：HuggingFace 直连不可达，已用镜像 `hf-mirror.com` 解决**。
- **亲密关系覆盖**：通用心理支持（含"我和男朋友感情"类案例），但**非专门恋爱/婚姻数据集**；需实际抽样评估覆盖率（见 02 推荐清单）。

---

## 3. ESConv（Emotional Support Conversation Corpus）

- **论文**：ACL 2021，arXiv:2106.01144（清华 CoAI）
- **官方源**：https://github.com/thu-coai/Emotional-Support-Conversation；ConvoKit 版：https://convokit.cornell.edu/documentation/support.html（`download("emotional-support")`）
- **内容**：约 **1,300 段**寻求支持者(seeker)↔支持者(supporter)对话，**英文**，众包收集，**真实人类对话**（非模型生成）。8 类支持策略标注（Question 20.7%、Affirmation 15.4%、Providing Suggestions 16.1%、Reflection of feelings 7.8%、Restatement/Paraphrasing 5.9%、Self-disclosure 9.3%…），并含 seeker 逐轮反馈分、情绪类型、问题类型、满意度问卷。
- **问题类型含**：breakup with partner（239）、issues with parent、problems with friends、ongoing depression 等——**分手/亲密关系是明确覆盖类别**。
- **理论底座**：Hill Helping Skills Theory（Exploration→Comforting→Action），与本项目上一轮「专业能力蒸馏」的高度一致。
- **授权**：`LICENSE` = **CC-BY-NC 4.0** + README 明确"**academic research use only**"。→ **禁止商业使用**。
- **下载状态**：⛔ **未下载**（禁商用；Emora 是商业产品，不得纳入）。原因记录，不绕过。可用于**纯研究**参考多轮情绪支持策略，但不可进入 Emora 商用知识库。
- **价值**：策略标注 + 逐轮反馈是研究"共情/提问/复述时机"的高质量素材，但**英文** + **禁商用**，两重限制。

---

## 4. Evidence-based Principles of Therapy Clearinghouse

- **性质**：这是**方法/指南类资源，不是可下载的对话数据集**。检索未发现以该确切名称命名的单一可下载数据集；对应权威公开资源为：
  - **SAMHSA Evidence-Based Practices Resource Center**（https://www.samhsa.gov/resource-search/ebp）——官方 .gov，含 CBT/家庭治疗/动机访谈等方法原则。
  - **APA 循证心理实践指南（EBPP）**（https://www.apa.org/about/policy/psychological-practice-health-care.pdf）——含共情、治疗关系、改变原则、认知重构等方法；⚠️ 该 PDF 受 Incapsula 反爬保护，**程序化下载失败**（可浏览器访问），本次未强行绕过的动机明确记录为"下载失败"。
  - **APA Division 12 心理治疗列表**（https://div12.org/psychological-treatments/）。
- **与 Emora 的关联**：这些"专业方法资料"在上一轮 `research/expert-distillation/` 已系统化为 MI/OARS、Gottman、EFT、Socratic、依恋、安全评估等证据链，可与此处数据集互相印证。
- **下载状态**：SAMHSA 资源页可访问；APA EBPP PDF 程序化下载失败（反爬）。

---

## 5. 主动补充的候选资源（供后续扩展）

以下来自 qiuhuachuan（PsyDial 作者，浙大）同一团队、且均有官方 GitHub/HF，但主要为 HF 托管（需镜像下载）或模型为主，本轮仅记录、不下载：

| 资源 | 说明 | 备注 |
|---|---|---|
| MeChat / SMILE（EMNLP 2024） | 中文心理健康对话大模型 + 数据 | 数据集在 HF（SMILECHAT 被 CPsyCounE 引用） |
| PsyChat | 客户端为中心的心理支持对话系统 | 数据 HF |
| DialogueSafety | 中文心理咨询对话安全检测数据集 | 对 Emora 安全层有价值 |
| PsyGUARD（EMNLP 2024 Oral） | 自杀检测与风险分级 | 与 Emora 安全/转介能力直接相关 |

> 上述未展开下载：一是避免扩张任务范围，二是多为 HF 托管（需镜像逐一核实 gated 状态）。如需可继续。

---

## 6. 数据真实性分级（重要，务必区分）

| 分级 | 数据集 | 说明 |
|---|---|---|
| **原始真实资料** | CPsyCounR（真实案例报告，人工校对） | 需授权书 |
| **真实对话（英文，禁商用）** | ESConv（众包真实对话） | 禁商用 |
| **半真实重构** | PsyDial D1/D4/D0_m（真实底本 + RMRR 重构） | 商用 OK |
| **模型重构** | CPsyCounD（Memo2Demo 报告→对话） | 商用 OK（署名/SA） |
| **方法/指南资料** | Therapy Clearinghouse 对应资源 | 参考 |

**核心红线（承接任务边界）**：模型重构对话 ≠ 真实专家记录；博主观点 ≠ 专业结论；心理咨询数据 ≠ 恋爱咨询数据（PsyDial 是通用心理支持，需实测亲密关系占比）。

---

## 7. 完整来源链接（官方）

- CPsyCoun GitHub：https://github.com/CAS-SIAT-XinHai/CPsyCoun
- CPsyCounR HF：https://huggingface.co/datasets/CAS-SIAT-XinHai/CPsyCounR
- CPsyCounD HF：https://huggingface.co/datasets/CAS-SIAT-XinHai/CPsyCoun
- CPsyCoun 论文：https://aclanthology.org/2024.findings-acl.830/
- PsyDial GitHub：https://github.com/qiuhuachuan/PsyDial
- PsyDial 论文：https://aclanthology.org/2025.acl-long.1049.pdf
- ESConv GitHub：https://github.com/thu-coai/Emotional-Support-Conversation
- ESConv ConvoKit：https://convokit.cornell.edu/documentation/support.html
- ESConv 论文：https://aclanthology.org/2021.acl-long.269/
- SAMHSA EBP：https://www.samhsa.gov/resource-search/ebp
- APA EBPP：https://www.apa.org/about/policy/psychological-practice-health-care.pdf