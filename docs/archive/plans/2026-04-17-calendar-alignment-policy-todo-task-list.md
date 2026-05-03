# Calendar Alignment Policy 实施 Todo Task List

## 概述

本文将 `calendar alignment policy` 设计稿拆解为可落地实施的任务清单，目标是在 **v1 固定 catalog、`observe` 唯一暴露入口、compiler-owned policy registry** 的前提下，完成一套边界清晰、可验证、可渐进上线的交付方案。

一句话结论：

- v1 先做“**固定 policy 集 + resolver 确定性展开 + observe 冻结结果 + compare/comparability 复用**”。
- 不做开放式 policy CRUD，不把 policy 塞进 `time_scope` / `binding` / `metric` 主 contract。
- 任务拆解必须围绕五个交付面推进：`calendar data`、policy registry、request/compiler、evidence/comparability、验证与上线。

## 文档依据

- `../factum/docs/semantic/calendar-alignment-policy.zh.md`
- `../factum/docs/semantic/time-schema-contract.zh.md`
- `../factum/docs/semantic/metric-process-contract.zh.md`
- `../factum/docs/semantic/overview.md`

## 实施范围

### 本次必须覆盖

- 落地 v1 默认 policy 集及其内部 registry 语义
- 为 resolver 提供稳定 `calendar data` 输入契约
- 在 `observe` 引入 `calendar_policy_ref`
- 在 compiler / resolver 中生成 resolved alignment plan
- 在 observation artifact / lineage / comparability metadata 中冻结并复用解析结果
- 在 comparability gate 中消费结构化 alignment diagnostics
- 提供最小可用的测试、文档和 rollout 方案

### 本次明确不做

- `calendar_policy.*` 独立 CRUD / validate / activate / publish 生命周期
- 面向调用方开放自定义 policy authoring schema
- 在 `compare` 等下游 intent 再次直接接收 policy 输入
- 运行时网页抓取或临时 holiday API
- 将 policy 写入 `metric` 主 contract 或 `binding` contract
- 处理所有长尾节假日 / 活动类型，仅覆盖 v1 默认 policy 集

## 交付原则

- 粒度以“单个 owner 可独立完成并验收”为准，避免大而化之的任务描述。
- 边界以 contract 分层为准，不让 `time`、`binding`、`metric`、`policy` 职责互相渗透。
- 每个任务都必须有明确产物和验收标准，避免“完成实现但无法判断是否可上线”。
- 先实现 `observation-first` 主链路，再补 agent routing、文档和 rollout 优化项。

## 里程碑

### M1. Contract 冻结

- 完成 v1 scope 收敛，明确只支持 fixed catalog policy refs
- 冻结默认 policy 集、calendar data 字段集、resolved plan 最小输出
- 冻结下游复用边界：仅 `observe` 接收 `calendar_policy_ref`

### M2. 主链路跑通

- `observe` 能接收 policy ref
- resolver 能输出 baseline window 与 bucket pairing
- observation artifact 能携带 resolved policy summary
- `compare` / comparability gate 能消费上游冻结结果

### M3. 可观测与可上线

- diagnostics、错误码、coverage summary 完整可读
- 测试覆盖常见成功/失败场景
- 文档、灰度、回滚策略齐备

## Todo Task List

## 一、Scope 与 Contract 冻结

- [x] 任务 1.1：冻结 v1 产品边界
  - 交付物：一页式 scope note 或 design decision 记录
  - 关键内容：`fixed catalog`、`no public CRUD`、`observe-only input`、`observation-first`
  - 验收标准：相关 owner 对“v1 不支持自定义 policy authoring”无歧义

- [x] 任务 1.2：冻结 v1 默认 policy 集
  - 交付物：policy registry 初版清单
  - 范围：`natural_yoy`、`weekday_yoy`、`holiday_yoy`、`event_yoy`、`natural_mom`、`weekday_mom`、`event_mom`、`weekday_wow`
  - 验收标准：每个 policy 都有 `comparison_basis`、`use_when`、`avoid_when`、内部 matching strategy 摘要

- [x] 任务 1.3：冻结 `calendar_policy_ref` 的 request 边界
  - 交付物：request/intent contract 变更说明
  - 关键内容：仅 `observe` 暴露 `calendar_policy_ref`；下游 typed-ref intent 不再重复接收 policy
  - 验收标准：`compare` / `validate` / `attribute` 的边界描述与设计文档一致

- [x] 任务 1.4：冻结 resolved alignment plan 最小字段集
  - 交付物：compiler/resolver 输出 schema 草案
  - 最小字段：`policy_ref`、`comparison_basis`、`resolved_calendar_source`、`resolved_calendar_version`、`resolved_baseline_generation_rule`、`current_window`、`baseline_window`、`bucket_pairing`、`coverage_summary`、`comparability_warnings`
  - 验收标准：字段定义可直接支撑 runtime、evidence、diagnostics 三方消费

- [x] 任务 1.5：冻结 comparability issue taxonomy
  - 交付物：错误与 warning 字典
  - 起步范围：`calendar_policy_missing`、`calendar_data_missing`、`holiday_cluster_unmapped`、`event_cluster_unmapped`、`weekday_pairing_tie`、`alignment_coverage_insufficient`、`fallback_applied`
  - 验收标准：每个 issue 都有触发条件、归属层、建议处理动作

## 二、Calendar Data 基础设施

- [x] 任务 2.1：定义 `calendar data` 逻辑输入契约
  - 交付物：calendar data schema 文档或内部类型定义
  - 最小字段：`calendar_date`、`region_code`、`calendar_version`、`is_weekend`、`is_workday`、`holiday_name`、`holiday_group_id`、`weekday`、`year_relative_holiday_key`、`event_group_id`、`year_relative_event_key`
  - 验收标准：唯一性约束、weekday 值域、补班/调休表达方式全部明确

- [x] 任务 2.2：确定 v1 calendar data source
  - 交付物：source 选型记录
  - 备选：平台内置表、同步后的权威节假日表、业务活动日历表
  - 验收标准：明确 `CN` 公共节假日与业务活动窗口各自从哪里来，且版本可冻结

- [x] 任务 2.3：建立 calendar version 冻结机制
  - 交付物：versioning 规则与数据发布流程
  - 关键内容：resolver 不允许使用“latest as of now”式动态版本
  - 验收标准：给定同一请求可重放出同一 pairing plan

- [x] 任务 2.4：补齐节假日相对位置与活动相对位置注释生成逻辑
  - 交付物：annotation 生成规则或离线构建任务
  - 范围：`holiday_group_id`、`year_relative_holiday_key`、`event_group_id`、`year_relative_event_key`
  - 验收标准：春节/清明/国庆及至少一个活动窗口能生成稳定相对位置标记

- [x] 任务 2.5：定义 calendar data 缺失时的失败策略
  - 交付物：缺失处理规则
  - 关键内容：缺 annotation 是报错、warning 还是 fallback，必须按 policy 固定语义处理
  - 验收标准：不允许由 runtime/LLM 临场猜测

- [ ] 任务 2.6：实现 calendar data 读取与装配层
  - 交付物：calendar source reader / assembly adapter
  - 关键内容：按 `time_scope`、`region_code`、已冻结 `calendar_version` 读取公共节假日与业务活动 source，并装配为 resolver 可直接消费的统一 `calendar data` 行视图或 annotation rows
  - 验收标准：给定同一请求与同一 source/version 组合，运行时能稳定产出相同输入；lineage / metadata 可反查本次读取使用的 source 与 version；compiler 不再依赖手工注入的临时 snapshot

## 三、Policy Registry 与 Resolution

- [x] 任务 3.1：实现 compiler-owned policy registry
  - 交付物：registry 配置或内置 catalog 条目
  - 每条至少包含：`policy_ref`、`comparison_basis`、`window_tags`、`use_when`、`avoid_when`
  - 验收标准：调用方只能引用白名单 policy refs

- [x] 任务 3.2：为每个 policy 补齐内部 matching strategy
  - 交付物：registry 内部字段定义
  - 关键内容：baseline generation rule、ordered matching strategy、fallback 行为、coverage 行为
  - 验收标准：`holiday_yoy`、`event_yoy`、`weekday_yoy` 之间无语义重叠歧义

- [x] 任务 3.3：实现 policy resolution 优先级
  - 交付物：resolution 逻辑
  - 顺序：显式 request > 上游注入 binding > planner/agent 候选
  - 验收标准：多候选同等合法时返回结构化歧义，不静默猜测

- [x] 任务 3.4：实现 policy 合法性校验
  - 交付物：validation 逻辑
  - 范围：`comparison_basis` 与 policy ref 是否匹配；请求是否命中不支持的 policy
  - 验收标准：`yoy` 请求不能误用 `*_mom` / `*_wow`

- [x] 任务 3.5：沉淀 agent/planner 用的 policy registry 摘要
  - 交付物：供上游路由消费的轻量摘要
  - 验收标准：能支持“法定节假日优先 holiday、活动窗口优先 event、周几对齐优先 weekday、否则 natural”的保守路由

## 四、Observe Request 与 Compiler 主链路

- [x] 任务 4.1：扩展 `observe` 输入 schema
  - 交付物：`observe` contract 更新
  - 关键内容：新增可选 `calendar_policy_ref`
  - 验收标准：未传 policy 时仍兼容原有行为

- [x] 任务 4.2：实现 `observe.time_scope + calendar_policy_ref` 到 resolved plan 的编译入口
  - 交付物：compiler/resolver 主流程
  - 验收标准：能从当前窗口稳定推导 baseline window 与 bucket pairing

- [x] 任务 4.3：实现 baseline generation rule 展开
  - 交付物：baseline 生成模块
  - 范围：`previous_year`、`previous_period` 等 v1 所需策略
  - 验收标准：对 `yoy`、`mom`、`wow` 输出正确 baseline window

- [x] 任务 4.4：实现 ordered matching strategy 执行器
  - 交付物：pairing resolver
  - 范围：`holiday_cluster`、`year_relative_holiday_key`、`event_cluster`、`year_relative_event_key`、`same_weekday_nearest`、`natural_date_shift`、`fallback`
  - 验收标准：pairing reason、shift days、issues 都可稳定输出

- [x] 任务 4.5：实现 coverage summary 计算
  - 交付物：coverage 统计逻辑
  - 最小输出：`aligned_bucket_count`、`unpaired_bucket_count`、`aligned_ratio`
  - 验收标准：resolved alignment plan 稳定携带 coverage summary；未配对 bucket 会显式记录 `alignment_coverage_insufficient`；是否升级为 warning / gate 失败由后续 comparability gate 任务处理

- [x] 任务 4.6：实现 tie-breaker 与 max-shift 约束
  - 交付物：pairing 冲突处理逻辑
  - 范围：例如 `same_weekday_nearest + prefer_backward + max_shift_days`
  - 验收标准：等距冲突不会被静默吞掉

## 五、Artifact / Evidence / Downstream 复用

- [x] 任务 5.1：在 observation artifact 中保留 resolved policy summary
  - 交付物：artifact schema 扩展
  - 验收标准：下游无需重复输入 policy 即可读取已冻结结果

- [x] 任务 5.2：在 lineage / metadata 中记录 policy binding
  - 交付物：lineage 字段补充
  - 验收标准：能追溯本次分析最终使用了哪个 `calendar_policy_ref` 和哪个 `calendar_version`

- [x] 任务 5.3：让 `compare` 消费 observation 中已冻结的 comparability metadata
  - 交付物：下游 compare 读取逻辑
  - 验收标准：compare 不再自行重建 holiday / weekday 对齐逻辑

- [x] 任务 5.4：明确 `validate` / `attribute` 等 compare-like intents 的复用路径
  - 交付物：typed-ref 复用规则说明
  - 验收标准：所有 compare-like intents 使用同一份上游 resolved policy summary
  - 落地说明：`attribute` 仅通过内部 `compare` 复用 frozen alignment metadata；`validate` 仅通过内部 `test` 复用同一份 metadata；compare-like intents 不新增平行 `calendar_policy_ref` 输入

- [x] 任务 5.5：评估 bucket pairing plan 是否只做 metadata 还是升格为可引用 artifact
  - 交付物：决策记录
  - 验收标准：v1 至少明确“不做一等 artifact”还是“只做 metadata 输出”
  - 落地说明：v1 明确 `bucket_pairing plan` 只作为 observation artifact `resolved_policy_summary` 的 metadata 输出，不新增独立 artifact、typed ref 或读取面；决策记录见 `docs/semantic/calendar-bucket-pairing-artifact-decision.zh.md`

## 六、Comparability Gate 与 Diagnostics

- [x] 任务 6.1：将 comparability gate 改为消费 resolved alignment plan
  - 交付物：comparability integration 改造
  - 验收标准：gate 不再并行推导第二套 holiday 逻辑

- [x] 任务 6.2：补齐 comparability requirements
  - 交付物：规则集更新
  - 最小范围：`baseline_calendar_policy_resolved`、`holiday_cluster_alignment_complete`、`event_cluster_alignment_complete`、`weekday_pairing_compatible`、`calendar_coverage_sufficient`、`alignment_tie_breaker_resolved`
  - 验收标准：每条 requirement 都能映射到 resolved plan 字段

- [x] 任务 6.3：规范 warning 与 blocking issue 的分层
  - 交付物：diagnostics severity 约定
  - 边界：单边窗口不完整归 `quality_gate`；双边配对不稳定归 `comparability_gate`
  - 验收标准：同类问题不会跨 gate 重复报错

- [x] 任务 6.4：设计用户可读的 failure surface
  - 交付物：错误消息模板与示例
  - 验收标准：出现 coverage 不足、holiday 未映射、tie 未解决时，用户能知道缺什么、下一步做什么

## 七、测试与验证

- [x] 任务 7.1：补齐 policy registry 单元测试
  - 交付物：registry / validation tests
  - 验收标准：非法 ref、basis 不匹配、未知 policy 能稳定失败

- [x] 任务 7.2：补齐 resolver 单元测试
  - 交付物：baseline + pairing tests
  - 场景：natural/weekday/holiday/event 的成功路径
  - 验收标准：输出 baseline、pairing reason、coverage summary 与预期一致

- [x] 任务 7.3：补齐异常与边界场景测试
  - 交付物：negative tests
  - 场景：calendar data 缺失、holiday cluster 未映射、event cluster 未映射、weekday tie、coverage 不足、fallback 生效
  - 验收标准：issue taxonomy 与 severity 输出稳定
  - 落地说明：pairing / compiler / compare-like reuse 三层测试已覆盖 `calendar_data_missing`、`holiday_cluster_unmapped`、`event_cluster_unmapped`、`weekday_pairing_tie`、`alignment_coverage_insufficient`、`fallback_applied`

- [x] 任务 7.4：补齐 observe -> compare 端到端测试
  - 交付物：integration tests
  - 验收标准：下游 compare 复用上游 observation metadata，而不是重算 policy
  - 落地说明：HTTP 集成测试已覆盖真实 `observe -> compare` 链路，并断言 `resolved_input_summary.calendar_alignment.reuse_source = observation_resolved_policy_summary`

- [x] 任务 7.5：准备最小验收样例集
  - 交付物：golden cases
  - 建议样例：普通同比、清明同比、618 活动同比、工作日效应同比、月环比、周环比
  - 验收标准：每个样例都能对应一个明确 policy 与预期 pairing 行为
  - 落地说明：新增 `docs/semantic/calendar-alignment-golden-cases.zh.md`，以关键行为级样例表冻结 6 个最小验收 case，并为每个 case 绑定现有测试锚点；不额外引入独立 fixture 格式或全量 bucket 明细

## 八、文档、治理与上线

- [ ] 任务 8.1：更新 intent / semantic / comparability 相关文档
  - 交付物：文档 PR 清单
  - 最少涉及：`observe`、`compare`、comparability gate、semantic overview
  - 验收标准：外部文档不再把 holiday alignment 描述成 prompt 层临时能力

- [ ] 任务 8.2：补一份“v1 policy selection guide”
  - 交付物：面向 agent / 调用方的选型说明
  - 验收标准：能清楚回答什么时候选 `natural` / `weekday` / `holiday` / `event`

- [ ] 任务 8.3：定义灰度发布策略
  - 交付物：rollout 计划
  - 关键内容：先内测 `holiday_yoy` / `weekday_yoy`，再逐步放开其余 policy
  - 验收标准：出现 pairing 误配时可快速回滚到未启用 policy 的路径

- [ ] 任务 8.4：建立运行监控指标
  - 交付物：运行观测项
  - 最小范围：policy 使用分布、fallback 比例、coverage 不足比例、comparability failure 比例
  - 验收标准：能在灰度期快速发现 policy 误路由或 calendar data 质量问题

- [ ] 任务 8.5：定义后续版本升级门槛
  - 交付物：v2 进入条件
  - 条件：contract 稳定、多团队持续新增 policy 需求、确需独立 CRUD / lifecycle
  - 验收标准：团队对“何时从 compiler-owned catalog 升级为 top-level object family”有统一判断口径

## 推荐实施顺序

1. 先完成第 1 章和第 2 章，冻结范围、数据输入和错误 taxonomy。
2. 再完成第 3 章和第 4 章，打通 policy registry 与 `observe` 编译主链路。
3. 然后完成第 5 章和第 6 章，确保下游复用和 comparability diagnostics 边界闭合。
4. 最后完成第 7 章和第 8 章，补齐测试、文档、灰度与监控。

## 最小可上线验收口径

- 至少支持 `calendar_policy.natural_yoy`、`calendar_policy.weekday_yoy`、`calendar_policy.holiday_yoy`。
- `observe` 能接收并冻结 `calendar_policy_ref`。
- resolver 能输出 baseline window、bucket pairing、coverage summary、comparability warnings。
- `compare` 能复用 observation metadata，不重复推导对齐逻辑。
- comparability gate 能区分 `calendar_data_missing`、`weekday_pairing_tie`、`alignment_coverage_insufficient` 等关键失败类型。
- 至少有 1 组法定节假日样例、1 组 weekday 对齐样例、1 组普通同比样例通过端到端测试。

## 风险与前置依赖

- 最大风险不是 compiler 逻辑，而是 calendar data 的版本化和注释完整度不够。
- `holiday_yoy` 与 `event_yoy` 的边界若不先冻结，后续 agent 路由会频繁产生歧义。
- 若 observation artifact 不保留足够的 resolved policy summary，下游 compare-like intents 会重新发明平行逻辑，直接破坏设计目标。
- 若 diagnostics taxonomy 不先统一，后续 quality/comparability 两条 gate 会互相覆盖、重复报错。

## 建议的 owner 切分

- Semantic / compiler owner：第 1、3、4、6 章
- Data / platform owner：第 2 章
- Evidence / runtime owner：第 5、6 章
- QA / rollout owner：第 7、8 章
