# 第一轮工程整改实施依据与迁移说明

本轮不修改 System Prompt、专业字段设计、Renderer、图节点/边及专业交互规则。
已按顺序阅读三份 standalone 审计材料、professional-capability/README、双课题 00/01/02 及参考清单。
代码核对、七项整改矩阵及最终验收结果见同目录 [整改报告](整改报告.md)；此文件记录实施依据、持久化变更的必要性和迁移边界。

## 修改前基线

baseline.json 记录原工作区状态和源码哈希；baseline-source.tar.gz 为源码/文档快照，
排除密钥、用户数据库、日志和研究原始数据。新增验证脚本自身也纳入基线，并非原有业务代码。
原测试 14 passed / 2 skipped / 1 failed。原失败是过时的流式 Service 替身/回放预期，
修复测试回调签名并模拟 Guard 后的增量回调，保留原文本及完成事件断言。

## 最小 SQLite 迁移（在实际应用数据库执行前说明）

必要性：analyses 只存单次输入、一轮澄清和结果，无法恢复完成后的追问关联。
复用原记录，不增加长期 Memory、checkpoint 或复制全文的消息表。

1. 缺失时添加 session_id TEXT、turn_index INTEGER NOT NULL DEFAULT 0。
2. 旧记录 session_id=id，每条旧记录独立成会话；不猜测历史关联。
3. 新增唯一索引 analyses_session_turn(session_id, turn_index)，保障顺序唯一。
4. 新分析原子保存该轮内容、状态、会话与序号；澄清仍更新同一轮。
5. 同会话请求串行化；跨进程的过期轮次写入返回冲突，不覆盖已有结果。

迁移通过 init_db 幂等检查执行。先在临时旧版数据库验证旧字段保持、重复迁移及重开恢复。
本轮真实集成测试使用项目证据目录下的独立合成数据库，不打开或迁移现有用户数据库。
用户下一次启动前可用 SQLite backup API 对实际数据库生成一致性备份；如需恢复旧代码，
停服务并恢复备份。新增列无需破坏性回滚，旧代码读取仍可兼容。

## 当前网关验证约束

直接真实探针证明：Chat/Responses 接受严格 Schema 但可输出 HELLO，非法 Schema 亦接受。
不将 HTTP 200 当作 Structured Outputs 生效；保留可选 json_schema 请求模式，禁止自动降级。
JSON mode 对照探针与推理配置实测均单独保存，不能推导上游模型身份或专家能力。
