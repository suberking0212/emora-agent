# 03 · 案例数据库（Case Database）

- 数据文件：`03-case-database.jsonl`（每行一条 JSON，字段说明见下表）。
- 本文件为字段说明 + 分类统计 + 提取方法说明。
- 研究日期：2026-10-31。

## 1. 字段说明（JSONL schema）

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | string | 案例唯一编号 C001… |
| `category` | string | 主分类：个人心理 / 恋爱关系 / 婚姻家庭 / 人际关系 / 原生家庭 |
| `subcategory` | string | 子类（如"焦虑""出轨""婆媳"） |
| `source_ids` | array\<string\> | 追溯来源 ID（对应 `02-source-catalog.md`） |
| `evidence_grade` | string | A / B / C / D / E |
| `user_initial_expression` | string | 用户最初如何表达（尽量原文摘录，缺失标"未记录"） |
| `surface_request` | string | 用户明确提出的诉求（若有） |
| `inferred_real_concern` | string | 表面诉求 vs 真实困扰的推断，必须标 `[推论]` 且给出依据 |
| `professional_first_response` | string | 专业人士最初的回应（倾听/反映/提问/建议） |
| `key_questions_asked` | array\<string\> | 专业人士提出的关键问题及（若有）原因 |
| `decision_forks` | array\<string\> | 决策分叉点：面前有多种可选动作的时刻 |
| `new_info_changed_judgment` | string | 哪些新信息改变了原有判断（无则"无记录"） |
| `reactance_handling` | string | 对否认/反驳/回避/矛盾/沉默的处理 |
| `outcome_feedback` | string | 后续反馈与结果 |
| `missing_info` | array\<string\> | 显式缺失项 |

## 2. 分类统计

（结论在 `08-final-report.md` §2 汇总；此处给原始计数。）

| 主分类 | 可提取案例数 | 证据分布 |
|---|---|---|
| 个人心理状态 | 6 | B1 + C3 + D2 |
| 恋爱关系 | 5 | C2 + D3 |
| 婚姻家庭 | 5 | A/B2 + C3 |
| 人际关系 | 2 | C2 |
| 原生家庭与个人成长 | 6 | B1 + C5 |
| **合计** | **~20**（去重启后） | — |

> 重要说明：这些案例来自公开材料的**部分还原**，绝大多数**不含完整逐字对话**（仅 S08、S19 等少量接近完整）。每条案例的 `missing_info` 字段如实列出无法获取的部分，绝不补全对话或虚构咨询过程。**20 条与任务书建议的 100+ 有差距，如实报告**——公开网络缺乏经授权脱敏的完整咨询逐字稿，这一客观边界详见 `08-final-report.md` §6。

## 3. 提取方法

1. 只从 `02-source-catalog.md` 已收录来源提取，每条案例与来源一一对应。
2. `inferred_real_concern` 一律标注 `[推论]` 并写明依据（不得无依据推断），与 `[事实]`/`[专家经验]`/`[研究结论]` 显式区分。
3. 无法获取的信息写入 `missing_info`，绝不虚构。
4. 隐私：所有案例已去标识化；C/D 级案例不保留任何可识别个人身份的信息。