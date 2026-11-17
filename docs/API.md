# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file，可附parameters字段（JSON文本）。

## 节点口径

- GET /api/node-policy 返回版本化口径注册表、切换时刻、注册表指纹与可选模式（event_time/force_legacy/force_current）。
- POST /api/runs 与 /api/upload 的 parameters 支持：node_policy（默认event_time）、node_cutover（覆盖切换时刻，需带时区偏移）。非法模式返回422 BAD_NODE_POLICY。
- 路径阶段 rows 含 node_policy_result（primary/fallback）、node_versions_applied、node_policy_detail 与逐条 interpretations（原始代码、锚点版本、采用版本、overridden、备选解释）；隔离扫描在 node_policy_attempts 中保留每个候选口径方案的路径和失败原因。
- 口径模式或切换时刻进入运行摘要，改变参数即改变 run_id；同输入同参数幂等。
/api/runs/<id>/archive只导出一份带清单与证据的CSV。
