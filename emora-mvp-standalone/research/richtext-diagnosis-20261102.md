# 正文富文本显示排查（2026-11-02）

## 结论

当前证据支持：已有回复的正文没有 Markdown 格式标记，前端又把固定栏目名拼接成普通文字，因此完成后的页面主要呈现普通段落和编号列表。不能据此判断 Provider 或模型不具备生成 Markdown 的能力。

另有明确的前端实现差异：流式增量只追加文本节点，直到 complete 后才调用 Markdown 渲染。当前实现并非实时富文本渲染。

本次仅排查并记录证据，未修改 System Prompt、Provider 请求、固定报告字段、专业策略或 Renderer，未重启服务。

## Provider 与输出字段

- 当前 `/api/runtime` 返回模型 `gpt-5.6-terra`，`response_format=json_object`。现有运行日志记录 Provider 主机为 `ruoli.dev`。
- `backend/provider.py` 的 `complete()` 请求 JSON 对象，并要求不要输出 Markdown 围栏、推理或 JSON 对象之外的文字；它没有要求正文字符串使用 Markdown。
- `backend/prompts/system.md` 要求固定结构化字段，没有富文本表达要求。
- `backend/contracts.py` 中正文值为字符串或字符串数组，没有禁止 Markdown 的字段约束。
- JSON 是传输结构，Markdown 是字符串内的文本语法，两者可以共存。API 不会返回已由浏览器渲染的字号、粗体或图形；相应呈现由前端负责。
- `complete()` 将收到的 content 拼接后做 `json.loads()`；没有发现主动删除字符串内 Markdown 标记的转换。
- 围栏禁令不应被解读为“接口技术上不支持 Markdown”。措辞可能影响生成倾向，但现有证据不足以确定其影响程度。

只读检查实际配置的 SQLite 中最近一条有 `result_json` 的记录，更新时间为 `2026-11-02T06:47:33.721250+00:00`。仅输出字段长度和标记存在性，不复制原始故事或正文：

| 字段 | 字符串数 | 字符总数 | 标题/粗体/代码围栏/已检查公式分隔符/表格分隔线 |
|---|---:|---:|---|
| summary | 1 | 72 | 均未发现 |
| emotions | 4 | 8 | 均未发现 |
| conflict | 1 | 62 | 均未发现 |
| user_needs | 1 | 52 | 均未发现 |
| suggestions | 5 | 334 | 均未发现 |
| next_step | 1 | 59 | 均未发现 |
| safety_note | 1 | 69 | 均未发现 |

这是已有数据的格式观察，不是业务测试，也不是完整 Markdown 语法检测。该记录与截图表现一致，但没有用截图对应的请求标识做一一绑定。

已有日志显示相邻的真实生成调用返回模型为 `gpt-5.6-terra`，JSON 解析完成；请求耗时约 10409.4 ms，首段内容约 4679.9 ms，上游报告 prompt/completion/total tokens 为 19116/526/19642。这些是既有调用数据，本次没有产生生成调用或新增 Token 消耗，亦不将该日志视为 Markdown 能力验证。

## 前端链路

1. `frontend/static/app.js:257`：`resultText()` 用“栏目名 + 换行 + 字段值”拼接结果。栏目名没有 Markdown 标题前缀或独立标题 DOM，所以不会因栏目语义而自动加粗、变大。
2. `frontend/static/app.js:79`：`renderRichText()` 已接入 Marked、DOMPurify、KaTeX 和 Mermaid。普通文字经 Markdown 解析后仍是普通段落；不会自动推断重点、创造公式或生成图表。
3. `renderResponse()` 在请求完成或历史恢复时调用 `renderRichText()`，没有直接把整个结果 JSON 当作正文显示。截图里的栏目及编号来自本地字段组装，不代表模型返回了完整 Markdown 文档。
4. SSE 的 delta 分支调用 `appendStreamText()`，直接追加文本节点。流式阶段不调用 `renderRichText()`；回复结束后才替换为最终渲染结果。
5. `frontend/templates/index.html:10` 起的依赖使用按顺序执行的 defer 脚本，Mermaid 位于 app.js 前；静态检查未发现此处加载顺序倒置。
6. `frontend/static/styles.css` 已定义 rich-text 标题、列表、表格、代码、公式样式。缺少标题语法时，相应标题样式没有匹配元素。

## 本次允许范围内的 HTTP 检查

| 地址 | 结果 |
|---|---|
| `/health` | 200 |
| `/api/runtime` | 200 |
| `/` | 200，返回页面引用富文本依赖及 app.js v11 |
| `/static/app.js?v=11` | 200，响应内容哈希与工作区文件一致 |
| `/static/styles.css?v=11` | 200，响应内容哈希与工作区文件一致 |
| `/static/vendor/marked.min.js?v=1` | 200，响应内容哈希与工作区文件一致 |
| `/static/vendor/dompurify.min.js?v=1` | 200，响应内容哈希与工作区文件一致 |

这些结果证明当前服务器可提供相应资源，不能证明用户浏览器实际执行成功，也不能排除浏览器缓存或控制台错误。未使用浏览器自动操作或渲染测试，未运行 pytest，未提交分析、标题、风险或其他生成请求。

## 能力判断与后续修改边界

- 已确认：当前配置下最近保存的正文缺少上述格式语法；固定栏目标题使用普通文本；流式阶段没有 Markdown 渲染。
- 未确认：当前网关在明确要求格式时能否稳定生成带 Markdown 的 JSON 字符串，以及公式和图表的生成质量。模型名、接口连通、官方文档或普通 JSON 成功均不能替代这项证据。
- 本次遵守用户的验证限制，不编造输入或模型输出，不以自动测试验证业务或代替真实用户操作。因此没有为了补齐该项证据另发生成探测。
- 若后续调整显示，可在保留字段和内容的前提下，为现有栏目提供语义标题排版，并完善流式文本到最终富文本的呈现；这只解决排版，不能宣称模型已具备格式生成能力。
- 正文主动使用粗体、公式、图表等涉及生成要求；本次不以修复显示为名提前改写专业 Prompt、删除固定字段或重构报告设计。
