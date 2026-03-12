# OmniDB 改进路线图

## 1. 文档目的

本文档将对 OmniDB 的设计评估结论转化为一份面向工程执行的改进路线图，目标不是对现有 MVP 做零散增强，而是按依赖顺序把系统逐步演进为一个更稳健的 **agentic analytics runtime**。

路线图聚焦三件事：

- 把核心分析抽象做实，而不是继续叠加场景化逻辑
- 把 planning、governance、multi-engine 纳入统一执行主链
- 在抽象稳定后再推进产品化、运维化和用户体验建设

本文与 `docs/omnidb-design-doc.md` 的关系如下：

- 设计文档描述愿景与目标体系
- 本文档描述从当前仓库状态走向该目标体系的工程推进顺序

## 2. 当前状态总结

当前仓库已经具备较强的概念验证价值：

- 有状态会话（session）
- 类型化步骤（typed steps）
- 语义层基础对象（entity / metric / mapping）
- 证据图（artifact / observation / claim / evidence edge / recommendation）
- source / engine / binding / routing 基础设施
- governance / jobs / approvals / observability 等子系统雏形
- HTTP、MCP、UI 三种交互入口

但从平台成熟度来看，当前实现仍主要是 **结构正确的 MVP**，而不是 **可持续扩展的平台内核**。主要问题不是“缺少功能点”，而是“中间层抽象仍不够稳固”。

## 3. 路线图原则

### 3.1 先补内核，再补外延

如果 IR、semantic execution、evidence packaging 还不稳定，继续增加 planner、policy、UI 功能只会放大耦合。

### 3.2 优先建设统一中间表示

planning、routing、governance、costing、evidence 都应围绕同一套中间表示工作，而不是分别围绕 SQL 字符串或场景化步骤工作。

### 3.3 明确区分“业务模板”和“分析原语”

`watch_time_drop` 这样的工作流应该建立在可复用的分析原语之上，而不是成为系统扩展的主要路径。

### 3.4 设计治理为“执行链能力”，而不是“边界检查”

治理信息需要进入解析、编译、执行、结果标注、审计全过程，而不仅仅是执行前拦截。

### 3.5 多引擎的重点不是“能切换”，而是“语义一致”

QueryRouter、dialect translation 只是起点，真正的难点在于能力模型、成本模型和执行语义统一。

## 4. 目标状态

完成本路线图后，OmniDB 应具备以下特征：

- 语义层不只是元数据登记层，而是可驱动执行的 semantic runtime
- step 不再强依赖少量场景化 handler，而是建立在分析原语之上
- planning 可以做真实的验证、预算检查、重规划和执行反馈
- governance 能参与 query compilation 与 provenance 记录
- multi-engine 具备清晰的能力矩阵与路由决策依据
- UI、MCP、HTTP 只是协议与体验层，不承载领域逻辑

## 5. 分阶段路线图

---

## Phase A：1 个月内 — 夯实分析内核

### 目标

把 OmniDB 从“场景化 MVP”升级为“可扩展分析框架”。

### 核心任务

#### A1. 引入统一分析 IR

建议增加一个显式的分析中间表示，例如：

- target semantic object
- requested metric(s)
- grain
- dimensions
- filters
- comparison window
- execution intent
- expected artifact shape
- evidence extraction hints

这层 IR 的作用是作为以下模块的公共契约：

- PlanningService
- GovernanceService
- QueryRouter
- cost estimation
- step compiler
- evidence pipeline

#### A2. 将 typed steps 拆成两层

建议拆分为：

- **分析原语层**：compare / aggregate / segment / rank / profile / sample / explain
- **场景模板层**：watch time drop、QoE regression、ad timeout investigation 等

这样可以避免后续每增加一个业务场景就增加一批新 step handler。

#### A3. 重构 evidence packaging 为插件式框架

建议引入三个稳定扩展点：

- `ObservationExtractor`
- `ClaimSynthesizer`
- `ConfidenceModel`

并支持按 metric、artifact 类型、domain 注册不同策略。

#### A4. 丰富 semantic object 语义

指标与实体需要补充执行语义，而不只是元数据字段。建议增加：

- grain / level
- aggregation kind
- allowed dimensions
- join constraints
- upstream dependencies
- quality expectations

### 交付物

- 新的分析 IR 定义与核心类型
- 原语层 step schema
- evidence pipeline 接口与默认实现
- semantic object 增强字段设计文档

### 完成标志

- 新增一个业务场景时，无需复制现有大量 step runner
- planner、governance、routing 至少在接口层都围绕 IR 工作
- evidence 抽取逻辑可按插件新增，而不是继续堆在一个模块里

---

## Phase B：3 个月内 — 打通 planning / governance / execution 主链

### 目标

让 OmniDB 从“工作流系统”升级为“规划驱动的分析运行时”。

### 核心任务

#### B1. 强化 plan validation

验证范围从“格式正确”升级为“可执行且合规”：

- semantic object 是否存在且已发布
- metric 与 dimension 是否兼容
- source / engine 是否支持所需操作
- 依赖步骤输出是否满足后续步骤输入
- governance 是否允许
- 预算是否可能超限

#### B2. 引入反馈驱动的 re-planning

让计划具备最小闭环能力：

- 证据不足时插入补充步骤
- 发现冲突证据时切换假设
- 成本超限时降级执行路径
- 某引擎不可用时重路由

#### B3. 让 governance 进入编译与执行主链

建议增加 policy application pipeline：

- semantic resolution 时应用访问边界
- query compile 时注入 masking / row filters / limits
- result shaping 时执行 aggregate-only 约束
- provenance 中记录 policy decisions

#### B4. 建立更真实的 cost model

成本估计至少要考虑：

- rows / bytes scanned
- local vs remote engine latency
- join fanout risk
- engine capability / cost profile
- whether cached / materialized

### 交付物

- enhanced plan validator
- re-planning 决策规则
- policy application pipeline
- v1 cost model 与预算校验规范

### 完成标志

- 计划验证失败时能给出明确语义原因，而不是仅参数错误
- 执行过程可以根据反馈调整下一步
- policy 不再只是 check，而是会影响实际编译结果
- 预算校验具备一定可信度

---

## Phase C：6 个月内 — 做实多引擎与平台边界

### 目标

把 OmniDB 从“单引擎 MVP + 多引擎接口”升级为“具备统一执行语义的平台”。

### 核心任务

#### C1. 建立 engine capability model

每个 engine 应显式暴露：

- supported SQL features
- supported step primitives
- pushdown ability
- latency profile
- cost profile
- governance compatibility
- materialization strategy support

#### C2. 让 routing 以 semantic intent 驱动

路由输入不再只是 table name，而是：

- requested semantic operation
- cost / latency constraints
- governance constraints
- required freshness / quality guarantees

#### C3. 设计联邦执行预留层

即使短期不做完整 federation，也建议预留三种模式：

- single-engine execution
- staged materialization and handoff
- partial result merge

#### C4. 重构核心服务边界

建议将当前中心化的 orchestration 拆成更清晰的模块：

- planner
- compiler
- executor
- evidence engine
- governance engine
- session orchestrator

### 交付物

- engine capability schema
- semantic-driven routing spec
- federated execution draft design
- service boundary refactor plan

### 完成标志

- 引擎选择有明确的能力与成本依据
- 多引擎支持不再只是“能切换”
- 代码结构可以支持更多业务域和更多执行后端

## 6. 优先级排序

如果只能做最关键的五件事，建议顺序如下：

1. **统一分析 IR**
2. **分析原语层与场景模板层拆分**
3. **Evidence pipeline 插件化**
4. **Planning / governance / cost 接入 IR**
5. **Engine capability model + semantic-driven routing**

这五项决定了 OmniDB 是继续停留在“高质量 MVP”，还是进入“可演进平台”的轨道。

## 7. 推荐的工程组织方式

### 7.1 并行工作流

建议分三条并行线：

- **内核线**：IR、step primitives、semantic execution
- **控制线**：planning、governance、costing、approvals
- **平台线**：engine capability、routing、jobs、observability

### 7.2 文档驱动推进

建议每个阶段先补设计文档，再落代码：

- IR spec
- step primitive catalog
- evidence framework spec
- policy application spec
- engine capability matrix

### 7.3 保留 MVP 兼容层

短期不要一次性移除现有场景化步骤。建议：

- 对旧 step 保留兼容
- 在新原语层之上重写旧 workflow
- 用 feature flag 或 versioned planner 切换

## 8. 风险与取舍

### 8.1 最大风险：过早产品化

如果在 IR 稳定前投入大量 UI、审批流、运维化工作，会放大后续重构成本。

### 8.2 第二大风险：继续围绕 SQL 字符串扩展

如果 planner、governance、routing 仍各自围绕 SQL 或场景 handler 工作，系统会逐渐失去架构优势。

### 8.3 第三大风险：把多引擎理解成“多个适配器”

真正困难的是统一语义与治理边界，而不只是连通更多后端。

## 9. 建议的成果评估指标

建议用以下指标评估路线图推进效果：

- 新增一个分析场景需要新增多少专有代码
- 同一 semantic request 是否可在多个 engine 上稳定执行
- planner 是否能解释为什么选择某个 plan / engine / policy path
- evidence graph 是否能稳定追踪 claim 来源与策略影响
- governance 是否能从“拦截”升级为“执行链约束”

## 10. 结论

OmniDB 当前最宝贵的部分不是某个单点功能，而是它已经把一条优于传统 text-to-SQL 的演进路线表达得很清楚。接下来的关键，不是继续堆能力点，而是**把中间层抽象做实**。

一句话总结本路线图：

**先补分析内核，再打通 planning / governance / routing，最后做多引擎与产品化。**
