# Factum Semantic Layer 生命周期与 Readiness Todo Task List

## 1. 文档目的

本文档基于 [`plan/semantic-layer-lifecycle-and-readiness.zh.md`](/Users/lichengxiang/source/oss/factum/plan/semantic-layer-lifecycle-and-readiness.zh.md) 生成，目标是将 semantic layer 生命周期与 readiness 重构方案进一步拆解为可直接排期、分配和落地的开发任务清单。

适用范围：

- semantic object 生命周期语义收敛
- object-level readiness 计算与统一返回结构
- catalog / resolve / runtime 对 readiness 的默认消费行为调整
- `/admin`、`/ui` 中与 semantic object 可用性直接相关的展示改造
- readiness 失效传播、`stale` 标记和回归测试补齐
- 与 semantic API / 运行时行为相关的文档同步

不纳入本文档：

- semantic layer typed contract 本身的大规模 schema 重设计
- 新分析 intent、compiler IR 或 evidence engine 主模型扩展
- 新数据源能力、执行引擎能力或 source sync 协议改造
- 面向最终治理流程的审批系统建设
- 阶段 B 中把 `validated` 变成数据库真实持久化状态的全面迁移

## 2. 拆分原则

- 按系统责任边界拆任务，不按单个文件拆任务。
- 先统一后端语义和计算模型，再改读接口、运行入口、前端展示，最后补失效传播和文档收口。
- `lifecycle` 与 `readiness` 必须作为两条独立轴交付，不允许继续混用 `published = ready`。
- 每个任务必须回答四件事：
  - 改哪一层
  - 对谁生效
  - 不覆盖什么
  - 怎么验收
- 默认每个开发任务都应同时覆盖：
  - 服务层实现
  - API 或 UI 契约变更
  - 自动化测试
  - 必要文档更新

## 3. 建议实施顺序

1. T1 统一状态模型与 blocker/capability contract
2. T2 readiness evaluator 基础框架
3. T3 entity / metric / process readiness
4. T4 dimension / time / enum / binding / profile readiness
5. T5 semantic API 列表与详情面收敛
6. T6 catalog / resolve 默认 ready 过滤
7. T7 runtime readiness gate 与结构化错误
8. T8 request-level compatibility 拆分
9. T9 `/admin` semantic catalog 展示改造
10. T10 `/ui` / picker / 运行入口展示改造
11. T11 readiness 失效传播与 `stale`
12. T12 生命周期动作与兼容路由收口
13. T13 测试矩阵补齐
14. T14 文档与迁移说明收尾

说明：

- T3 与 T4 可在 T2 完成后并行，但都依赖统一的 readiness 输出 contract。
- T5 是 T6/T7/T9/T10 的前置，因为前端与运行入口都需要稳定状态字段。
- T8 必须在 T7 之后完成，避免把 object-level readiness 与 request-level compatibility 再次耦合。
- T11 依赖对象级 evaluator 已稳定，否则无法准确定义 `stale`。
- T12 仅收敛 HTTP 动作语义，不要求本轮落库 `validated` 持久化。

## 4. 任务清单

---

## T1. 统一生命周期与 readiness 返回 contract

### 目标

在服务层和 API model 层引入统一的状态表达，建立后续所有 evaluator、API 和 UI 的共同基础。

### 依赖条件

- 已确认短期兼容策略为保留底层 `draft / published / deprecated` 存储。
- 已确认 API 对外新增 `lifecycle_status`、`readiness_status`、`blocking_requirements`、`capabilities`。

### 具体工作内容

- 定义统一枚举或等价常量：
  - `lifecycle_status`: `draft` / `validated` / `active` / `deprecated`
  - `readiness_status`: `not_ready` / `ready` / `stale`
- 定义统一 blocker 结构，至少包含：
  - `code`
  - `message`
  - 可选 `subject_ref`
  - 可选 `dependency_ref`
- 定义统一 capability 容器，允许按对象类型返回不同 capability key。
- 明确现有 `status` 到 `lifecycle_status` 的映射规则：
  - `draft` -> `draft`
  - `published` -> `active`
  - `deprecated` -> `deprecated`
- 在 API model 层增加兼容字段策略，确保旧 `status` 暂不移除。

### 明确边界

- 本任务不实现具体对象的 readiness 计算规则。
- 不改动数据库 schema。
- 不在本任务中修改 catalog 或 runtime 默认过滤逻辑。

### 交付物

- 统一状态 contract
- 服务层可复用的 readiness result 数据结构
- API model 扩展后的基础 schema

### 验收标准

- 任一 semantic object 在服务层都能被表达为同一套 readiness result 结构。
- 旧 `status` 字段仍可兼容读取，新字段不会破坏现有序列化。
- 后续 evaluator 与 API 不需要各自重新发明 blocker/capability 表达。

---

## T2. readiness evaluator 基础框架

### 目标

建立按对象类型计算 readiness 的统一执行框架，避免规则散落在 publish、resolve、compiler 和 UI 代码中。

### 依赖条件

- T1 已定义统一状态与 blocker contract。

### 具体工作内容

- 定义 evaluator 注册或分发机制，覆盖：
  - entity
  - metric
  - process
  - dimension
  - time
  - enum
  - binding
  - compatibility profile
- 抽象 evaluator 输入上下文，至少能读取：
  - 对象当前 revision
  - 依赖对象状态
  - binding / imported binding 状态
  - profile 与 subject revision 关系
  - runtime 所需 capability 约束
- 统一 evaluator 输出：
  - `lifecycle_status`
  - `readiness_status`
  - `blocking_requirements`
  - `capabilities`
  - 可选内部 trace，便于调试和测试
- 定义“从未 ready”与“曾 ready 后失效”的内部判定钩子，为后续 `stale` 做准备。

### 明确边界

- 本任务只建设框架和调用链，不要求一次完成全部对象规则。
- 不处理 API 层字段落地。
- 不在本任务中改变 runtime 行为。

### 交付物

- evaluator 框架与类型分发层
- evaluator 单元测试骨架
- 统一 trace / debug 约定

### 验收标准

- 新增一个对象类型的 readiness 规则时，只需实现单一 evaluator 并注册。
- evaluator 调用链不再依赖字符串字面量散落判断。
- 至少已有 smoke test 能覆盖对象进入 evaluator 并返回结构化结果。

---

## T3. Entity / Metric / Process readiness evaluator

### 目标

先实现最影响 runtime 可用性的三类核心对象 readiness 计算，优先修复“已发布但不能用”的主路径。

### 依赖条件

- T2 evaluator 框架已完成。
- 已确认 entity / metric / process 的 ready 条件以方案文档第 7 节为准。

### 具体工作内容

- 为 entity 实现 ready 规则：
  - lifecycle 为 `active`
  - identity contract 完整且合法
  - 若 runtime 需要 physical grounding，则存在满足 identity/time/descriptors 的可用 binding
- 为 metric 实现 ready 规则：
  - lifecycle 为 `active`
  - metric contract 合法
  - 依赖的 entity / time / dimension / process requirement 满足激活条件
  - 至少存在一个 active metric binding
  - binding 覆盖全部 required `metric_input`
  - 覆盖额外 `primary_time_ref`、`population_subject_ref` 等要求
  - 若 intent 依赖 process 或 inferential capability，则能力必须存在
- 为 process 实现 ready 规则：
  - lifecycle 为 `active`
  - process contract 合法
  - 若需要 physical grounding，则存在可用 process binding
  - inferential intent 需要匹配 revision 的 capability profile
  - metric requirement profile 兼容
- 为 metric / process 返回能力标签：
  - metric: `supports_observe`、`supports_detect`、`supports_attribute`、`supports_diagnose`、`supports_validate`、`supports_decompose`
  - process: `supports_time_projection`、`supports_experiment_inference`、`supports_cohort_inference`、`inferential_ready`
- 为典型 blocker 定义稳定 code，例如：
  - `METRIC_BINDING_MISSING`
  - `METRIC_INPUT_COVERAGE_MISSING`
  - `PROCESS_PROFILE_MISMATCH`
  - `ENTITY_GROUNDING_MISSING`

### 明确边界

- 不在本任务实现 dimension 的 request-level compatibility。
- 不处理 UI 展示层。
- 不要求引入 `stale` 传播，只需为后续保留判定信息。

### 交付物

- entity / metric / process evaluator 实现
- 面向主路径 blocker 的稳定错误码
- 关键 capability 输出

### 验收标准

- 现有“metric published 但无 binding”场景不再只能在执行期报错，而能被 evaluator 标成 `active + not_ready`。
- process profile revision mismatch 能被 evaluator 明确表达，不再仅靠隐式忽略。
- metric / process 详情可稳定返回 capability 字段，不依赖运行后推断。

---

## T4. Dimension / Time / Enum / Binding / Profile readiness evaluator

### 目标

补齐剩余 semantic object 的 readiness 规则，形成完整对象面覆盖。

### 依赖条件

- T2 evaluator 框架已完成。

### 具体工作内容

- 为 dimension 实现 object-level readiness：
  - lifecycle 为 `active`
  - contract 合法
  - 若对象声明可用于 grouping，则 `supports_grouping = true`
  - 若有 `time_derived_requirement`，输出对象级需求信息
- 为 time 实现 ready 规则：
  - lifecycle 为 `active`
  - contract 合法
  - semantic role 合法
- 为 enum set 实现 ready 规则：
  - lifecycle 为 `active`
  - schema 合法
- 为 binding 实现 ready 规则：
  - lifecycle 为 `active`
  - 绑定对象与 imported binding 已 active
  - carrier 可解析到 synced source object
  - field / target mapping 完整
  - metric binding 覆盖 required `metric_input`
- 为 compatibility profile 实现 ready/stale 规则：
  - lifecycle 为 `active`
  - subject 已 `active`
  - `subject_revision` 与当前 resolved revision 一致时为 `ready`
  - 不一致时为 `stale`
- 为 dimension / binding / profile 定义 blocker code 与 capability 输出约定。

### 明确边界

- 本任务只做 object-level readiness，不负责 request-level compatibility 判定。
- 不改变 compiler 中具体维度兼容判断流程。

### 交付物

- 剩余对象 evaluator 实现
- dimension 对象级能力和需求字段
- profile `stale` 基础判定

### 验收标准

- semantic object 八大类型均可输出统一 readiness 结构。
- profile revision mismatch 能直接返回 `stale`，而不是“active 但被静默忽略”。
- binding 缺 mapping 或 source object 不可解析时，能在对象详情直接看到 blocker。

---

## T5. semantic API 列表与详情接口状态收敛

### 目标

把 readiness 结果正式暴露到 HTTP 读接口，使管理员和前端能在目录和详情阶段看见可用性。

### 依赖条件

- T1-T4 已提供稳定 readiness 结果。

### 具体工作内容

- 为 semantic 对象列表接口增加：
  - `lifecycle_status`
  - `readiness_status`
  - `blocking_requirements` 摘要或 blocker count
  - 对象级 `capabilities` 摘要
- 为 semantic 对象详情接口增加完整字段：
  - `lifecycle_status`
  - `readiness_status`
  - `blocking_requirements`
  - `capabilities`
  - 可选 `dependency_refs`
  - 可选 `dependent_refs`
- 明确 API response 中旧 `status` 与新 `lifecycle_status` 的并存语义。
- 如果部分对象类型已有独立 response model，统一补齐相同字段，避免只在个别类型可见。
- 为列表接口定义默认轻量返回和详情接口完整返回的字段差异，防止无边界膨胀。

### 明确边界

- 不改变写接口。
- 不在本任务中调整 catalog / resolve 的默认过滤行为。
- 不做前端展示改造。

### 交付物

- 统一扩展后的 semantic 列表与详情 HTTP 响应
- API contract 测试

### 验收标准

- 任一 semantic 对象在列表页和详情页都能看到 readiness 信息。
- 新字段对所有主要对象类型保持一致命名，不出现局部特例。
- 旧客户端仍可通过旧 `status` 继续工作。

---

## T6. catalog / discovery / resolve 默认 ready 过滤

### 目标

让默认目录、picker 和 resolve 路径只消费真正可用的对象，修复目录语义与执行语义不一致的问题。

### 依赖条件

- T5 已把 readiness 字段暴露到读接口。
- 已明确 admin / modeling 显式查询仍可查看非 ready 对象。

### 具体工作内容

- 收敛 catalog / search / discovery 默认过滤规则为 `active + ready`。
- 为 admin / modeling 视图保留可选参数，以查询 `active + not_ready`、`stale` 对象。
- 调整 runtime resolver 与 planner context 默认只读 ready 对象。
- 如果对象因非 ready 被过滤，显式 resolve 路径应返回结构化 why-not-ready，而不是直接当作不存在。
- 梳理现有 `published_only` 语义，避免继续把“已发布”和“运行可用”当成同一件事。

### 明确边界

- 不在本任务中改动 compiler 执行前 gate。
- 不要求 UI 立即消费新过滤逻辑，但 HTTP 契约应已支持。

### 交付物

- 默认 ready-only 的 discovery / resolve 行为
- 管理视图可选扩展查询开关

### 验收标准

- 普通 catalog / picker 不再返回 `active + not_ready` 的 metric/process。
- 显式查询非 ready 对象时，调用方能得到 why-not-ready，而不是模糊的 not found。
- 现有 runtime 读取路径不再依赖仅 `published` 的旧过滤假设。

---

## T7. runtime readiness gate 与结构化错误

### 目标

把 readiness 错误前移到执行前，避免把 semantic object 不可用问题伪装成普通编译或运行错误。

### 依赖条件

- T3/T4 evaluator 已覆盖运行主路径对象。
- T6 resolve 已能感知 readiness。

### 具体工作内容

- 在 compiler / runtime 入口加入 object-level readiness gate。
- 对 metric/process/dimension 等核心对象，在执行前先校验 readiness。
- 定义统一 readiness error response，至少包含：
  - `code`
  - `subject_ref`
  - `message`
  - `blocking_requirements`
- 替换当前实现导向错误文案，例如：
  - “Resolved metric is not grounded by any published binding”
  - 改为 readiness 语义的结构化错误
- 确保 API 错误码和 HTTP status 行为一致，不让 readiness failure 混入内部异常。

### 明确边界

- 本任务只处理 object-level readiness gate。
- 不在本任务中解决 dimension 与 time anchor 的 request-level compatibility。

### 交付物

- readiness gate 实现
- 结构化 readiness error
- 执行前失败的回归测试

### 验收标准

- 非 ready metric 在执行前就会被拒绝，并带 blocker 列表返回。
- 用户不再需要依赖底层 binding/grounding 报错推断“这个对象其实不能用”。
- readiness failure 与普通 SQL/engine/compile failure 可被稳定区分。

---

## T8. request-level compatibility 与 object-level readiness 拆分

### 目标

把“对象本身不可用”和“这次请求上下文不兼容”拆成两类明确错误，避免 readiness 被过度扩张。

### 依赖条件

- T7 object-level readiness gate 已上线。

### 具体工作内容

- 梳理 dimension/time/process 等对象在请求上下文中的动态兼容条件。
- 为 request-level compatibility 定义独立错误结构，避免复用 `readiness_status`。
- 明确以下示例的归属：
  - dimension 不支持 grouping -> object-level blocker
  - dimension 需要特定 `time anchor`，但本次请求不满足 -> request-level incompatible
  - inferential intent 缺 process capability -> 若对象固有能力缺失则是 readiness；若是本次选择组合不兼容则是 compatibility
- 在 compiler validate 阶段接入 compatibility 校验，但不回写对象 readiness。
- 补齐错误文案与测试，确保用户能区分“修对象”还是“改请求参数”。

### 明确边界

- 不要求引入新的持久化状态。
- 不做 UI 大规模改造，只要 API 契约能区分两类错误即可。

### 交付物

- request-level compatibility 错误 contract
- compiler validate 中的兼容性分层

### 验收标准

- 同一个 dimension 既可在对象详情显示 `ready`，又可在特定请求中返回 compatibility failure，语义不冲突。
- readiness error 与 request compatibility error 拥有不同 code/contract，不再混用。

---

## T9. `/admin` Semantic Catalog readiness 展示改造

### 目标

让后台 semantic catalog 直接展示 lifecycle、readiness、blocker 和 capability，支持建模与排障。

### 依赖条件

- T5 semantic API 已返回 readiness 字段。
- `/admin` semantic catalog 已存在基础列表与详情结构，或已有明确承接方案。

### 具体工作内容

- 在 semantic object 列表中增加：
  - lifecycle badge
  - readiness badge
  - blocker count
- 在详情面板中增加：
  - Summary
  - Lifecycle
  - Readiness
  - Blocking requirements
  - Dependencies
  - Dependents
  - Capabilities
- 为 `stale` 提供可识别的视觉提示与说明文案。
- 提供显式筛选项：
  - lifecycle
  - readiness
  - has blockers
- 在详情页中把“为什么不能用”直接前置，不要求用户去运行一次才知道。

### 明确边界

- 本任务只改 semantic catalog 展示，不新增 object authoring 流程。
- 不承担 `/ui` picker 的终端用户简化视图。

### 交付物

- 后台 semantic catalog 状态展示改造
- 后台回归测试

### 验收标准

- 管理员在列表页即可区分 `Active + Ready`、`Active + Not Ready`、`Stale`。
- 详情页能直接定位 blocker，不需要跨页面搜 runtime 报错。
- `stale` 不会继续以“published”或“active”单独展示，避免误导。

---

## T10. `/ui` / picker / 运行入口 readiness 展示改造

### 目标

把终端用户默认体验收敛为“只看到 ready 对象”，并在显式查看不可用对象时直接看到 blocker。

### 依赖条件

- T6 默认 ready 过滤已生效。
- T7 readiness error 与 why-not-ready 已可返回。

### 具体工作内容

- 修改 metric/process/dimension picker 默认查询逻辑，仅展示 `active + ready`。
- 若存在“显示不可用对象”开关，则在候选项旁直接展示 blocker 摘要。
- 在 `/ui` 或相关运行入口中，若用户打开非 ready 对象详情，显示 why-not-ready，而不是仅展示失败提示。
- 对 capability-sensitive 入口增加弱提示，例如：
  - metric ready，但 `supports_validate = false`
  - process ready，但 `inferential_ready = false`
- 收敛用户文案，避免继续出现“published 就能用”的暗示。

### 明确边界

- 不要求本任务重做整套前端信息架构。
- 不修改 `/admin` 页面职责。

### 交付物

- 面向终端用户的 ready-first picker 与详情提示
- 前端回归测试

### 验收标准

- 默认选择器不再出现一选就报错的对象。
- 用户显式查看不可用对象时，可以直接看到 blocker，而不是只能去读服务端错误。
- capability 限制能在选择阶段暴露，不再完全延迟到执行后。

---

## T11. readiness 失效传播与 `stale` 管理

### 目标

在依赖更新后自动传播 readiness 失效，把“之前可用、现在失效”的对象显式标记为 `stale`。

### 依赖条件

- T3/T4 evaluator 已稳定。
- 已存在对象更新、binding 更新、profile 更新等事件入口。

### 具体工作内容

- 定义 readiness 重算触发源：
  - semantic object 更新
  - binding 更新、激活、失效
  - imported binding 状态变化
  - profile 更新
  - profile subject revision 变化
- 明确依赖传播方向：
  - entity -> metric / process / binding
  - metric -> metric binding / profile / downstream resolve
  - process -> process profile
  - binding -> metric / process / entity
- 实现“曾 ready 后失效”到 `stale` 的判定与落地策略。
- 为 `stale` 维护必要的原因信息，避免只有状态没有原因。
- 补齐 dependent invalidation 测试，尤其覆盖 profile revision mismatch 与 binding 失效。

### 明确边界

- 不要求本任务引入完整异步任务编排系统；可先使用同步重算或局部失效机制。
- 不在本任务中优化大规模批量重算性能到最终形态。

### 交付物

- readiness 失效传播机制
- `stale` 原因表达
- 依赖传播测试

### 验收标准

- 已 ready 的 metric 因 binding 失效后能变成 `stale`，而不是继续表现为可用。
- profile subject revision 变化后，profile 会显式标为 `stale`。
- dependent invalidation 不再依赖用户手动重新发布对象才能被发现。

---

## T12. 生命周期动作与兼容路由收口

### 目标

在不立即改造底层存储状态机的前提下，统一对外动作语义，减少 `publish` 的歧义。

### 依赖条件

- T5 API 状态字段已稳定。

### 具体工作内容

- 设计并实现兼容动作语义：
  - `validate`
  - `activate`
  - `deprecate`
- 如果短期保留 `publish` 路由，则：
  - 明确其内部等价于 `activate`
  - 响应中必须带出 readiness 字段
  - 文档中明确“activate != ready”
- 收敛服务层对状态转换的校验规则，避免各对象独立实现。
- 为 `/admin` 按钮文案和确认文案更新动作语义。

### 明确边界

- 不要求本轮把 `validated` 写入数据库主状态。
- 不做审批流、review 流建设。

### 交付物

- 统一的生命周期动作语义
- 兼容旧 `publish` 的过渡行为

### 验收标准

- 外部调用方可以理解“activate 只是进入正式目录，不代表 ready”。
- 旧 `publish` 路由若继续保留，也不会再暗示“发布后即可运行”。
- 生命周期动作在服务层有一致的状态校验。

---

## T13. 测试矩阵补齐

### 目标

为新的 lifecycle/readiness 行为建立稳定回归矩阵，避免规则重新散落或回退。

### 依赖条件

- T1-T12 主要实现已到位。

### 具体工作内容

- 补齐 evaluator 单元测试，覆盖八类对象的：
  - `draft`
  - `active + ready`
  - `active + not_ready`
  - `stale`
  - `deprecated`
- 补齐 API contract 测试，覆盖：
  - 列表字段
  - 详情字段
  - blocker / capability 返回
  - ready-only 默认过滤
- 补齐 runtime 行为测试，覆盖：
  - 执行前 readiness gate
  - 结构化 readiness error
  - request-level compatibility 与 readiness 区分
- 补齐前端或 UI 集成测试，覆盖：
  - `/admin` 状态 badge 与 blocker 展示
  - picker 默认仅显示 ready 对象
  - why-not-ready 展示

### 明确边界

- 不要求一次补全所有历史遗留弱测试；只覆盖本次状态模型变更的关键路径。

### 交付物

- 完整的后端与前端回归测试矩阵

### 验收标准

- 任一核心对象状态语义回退时，都有自动化测试能直接报警。
- 新增对象或状态逻辑时，开发者可以复用现有测试模板补覆盖。

---

## T14. 文档与迁移说明收尾

### 目标

同步所有对外文档和仓库内 shared guide，确保新语义在 API、UI 和运行时说明中一致。

### 依赖条件

- T5-T12 的对外行为已稳定。

### 具体工作内容

- 更新 [`docs/api/semantic.md`](/Users/lichengxiang/source/oss/factum/docs/api/semantic.md)，明确：
  - `status` 与 `lifecycle_status` 的关系
  - readiness 字段
  - blocker/capability 结构
  - ready-only catalog/resolve 规则
  - `publish` 与 `activate` 的过渡语义
- 更新相关 API 文档与 README，避免继续使用“published objects are available to runtime resolution”这类已过时表述。
- 如实现涉及 `/admin` 或 `/ui` 行为变化，同步更新相应方案或使用文档。
- 更新 [`docs/agent-guide.md`](/Users/lichengxiang/source/oss/factum/docs/agent-guide.md) 中与 shared boundary 相关的说明，但不写入实现细节。
- 为迁移阶段补一节简明说明：
  - 阶段 A 保留旧存储状态
  - 阶段 B 再决定是否持久化 `validated`

### 明确边界

- 不要求重写所有历史设计文档。
- 不在本任务中追加新的产品方案。

### 交付物

- 更新后的 API / guide / 迁移说明文档

### 验收标准

- 仓库内主要文档不再把 `published` 与“默认可运行”画等号。
- API、UI、运行时文档对 lifecycle/readiness 的表述一致。
- 新成员只看文档也能理解 `Active != Ready` 和 `Stale` 的含义。

## 5. 里程碑建议

### 里程碑 M1：后端状态模型可用

包含任务：

- T1
- T2
- T3
- T4

完成标准：

- 八类对象都能输出统一 readiness 结果。
- 核心 blocker/capability 已稳定。

### 里程碑 M2：HTTP 与 runtime 语义收敛

包含任务：

- T5
- T6
- T7
- T8

完成标准：

- API 已公开 readiness 字段。
- catalog / resolve / runtime 默认只消费 ready 对象。
- readiness failure 与 request compatibility failure 明确分离。

### 里程碑 M3：用户可见面与失效管理完成

包含任务：

- T9
- T10
- T11
- T12
- T13
- T14

完成标准：

- 管理后台和用户入口都能正确展示 readiness。
- `stale` 可被显式发现并追因。
- 测试与文档已经覆盖新的状态模型。

## 6. 建议并行分工

- 后端状态模型线：T1-T4、T11
- API / runtime 线：T5-T8、T12
- 前端与文档线：T9-T10、T14
- 测试收口线：T13

并行约束：

- 前端线在 T5 之前不应固定字段名或 badge 文案。
- runtime 线在 T3/T4 之前不应自行硬编码 blocker 规则。
- 文档线应以最终 API contract 为准，避免提前冻结过渡字段。
