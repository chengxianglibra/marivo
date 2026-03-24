# Factum 问题清单（来源：BI 集群负载分析，2026-03-23）

本次通过实际分析任务（调查 BI 集群本周 vs 上周查询负载变化）发现以下问题。

---

## 🔴 核心问题

- [ ] **synthesize_findings claim 语义贫乏**
  - 当前：所有 claim 均为 `"Signal detected for aggregate / log_date=XXXXXXXX"` 模板文本，无分析意义
  - 当前：`recommendation.action` 字段为 `null`
  - 期望：IncrementalSynthesizer 能结合 aggregate 结果的数值特征生成有意义的 claim 文本；recommendation 应输出可执行的行动描述
  - 影响：synthesize_findings 步骤对真实分析场景几乎无价值，洞察全靠 agent 在 Factum 外自行完成

- [ ] **缺乏跨步骤 WoW / period-over-period 对比机制**
  - 当前：做 WoW 对比需手动跑两条 aggregate_query（本周/上周各一条），再在外部计算差值
  - 当前：`compare_metric` 仅支持已注册 semantic metric，无法用于 `user`、`resource_group` 等 ad-hoc 维度
  - 期望：`aggregate_query` 支持 `compare_period` 参数；或 evidence pipeline 能自动关联同类步骤的观测、计算变化量并生成对比 claim

---

## 🟠 高优先级

- [ ] **`profile_table` 对 Trino/Iceberg 表静默失败**
  - 当前：返回 `row_count: null`、`columns: []`，无任何错误说明
  - 期望：响应 `summary` 字段应明确说明失败原因（adapter 不支持 / 权限不足 / 表不存在）
  - 相关：Trino adapter 的 `profile_table` 能力覆盖不完整

- [ ] **`aggregate_query` GROUP BY 派生表达式静默返回空**
  - 复现：`group_by` 中使用 `CASE WHEN ... THEN ...` 在 Trino 上返回 0 行，无报错
  - 根因：Trino 不支持 GROUP BY 引用 SELECT 别名，Factum 未做检测或兼容
  - 期望：SQL 编译层对此类语法做前置校验并返回可读错误；或对 GROUP BY 做方言适配（用完整表达式替换别名）

---

## 🟡 中优先级

- [ ] **列元数据不携带单位信息**
  - 问题：`elapsed_time` 实际单位为秒，但字段名、profile 响应、sample_rows 均无单位说明
  - 影响：分析过程中误判为毫秒，浪费一个步骤（慢查询阈值设为 60000 返回 0 行）
  - 期望：semantic catalog 中为物理列注册 `unit` 属性（`seconds` / `bytes` / `milliseconds`）；`sample_rows` / `profile_table` 响应中在列元数据里透出

- [ ] **session `raw_filter` 作用范围不透明**
  - 问题：session 设置了 `raw_filter` 后，`profile_table` 步骤不应用该 filter，但响应中无任何提示
  - 期望：每步响应应在 `governance` 或 `constraints_applied` 字段中列出哪些 session filter 被应用、哪些被跳过及原因

---

## 参考

- 触发会话：`sess_7792297a246f`
- 分析目标：BI 集群（k8sbi-bi1 / k8sbi-bi2）本周 vs 上周查询负载对比
- 核心发现：查询量 -11.5% 但单查询 CPU +30%、内存 +29%，根因为 `ai_bi`（扫描 +292%）、`bvc_bi`（+208%）等用户查询扫描量暴增
