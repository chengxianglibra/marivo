---
status: draft
created: 2026-05-11
updated: 2026-05-11
supersedes: 2026-05-10-osi-aoi-static-cutover-design.md (§1 Goals)
---

# AOI 运行时边界设计

**日期：** 2026-05-11
**状态：** Draft
**范围：** Marivo 的分析操作主路径、`attribute` / `diagnose` / `validate` 兼容入口、HTTP 与 MCP 适配层、compiler 内部 IR 边界

---

## 1. 目标

### 核心矛盾

Marivo 的分析操作语义目前有三个平行的定义源：HTTP request model、MCP tool schema、runtime intent handler 内部的 dict 解析逻辑。三者各自演化、互不约束。结果是：

1. **Contract drift** — 修改一处语义，其余两处默默过时，只有运行时才暴露不一致。
2. **校验碎片化** — HTTP 层校验一次（Pydantic），intent handler 再校验一次（手工 dict key 检查），MCP 层又有自己的 DTO 校验。没有一处能完整回答"这个操作的合法输入是什么"。
3. **AOI spec 悬空** — AOI generated model 已经定义了分析操作的具体类型（`Observe1`、`Compare`、`Detect` 等），但这些类型只参与 spec 级校验，不参与运行时主路径。相当于维护了两套语义：一套是 AOI 说的，一套是代码跑的。

### 目标 1：消除 contract drift — 单一类型源

**要解决的问题**：分析操作的合法输入/输出在三处定义，改一处不影响另外两处。

**目标**：AOI generated model（`Observe1`、`Compare`、`Detect`、`Artifact1` 等）成为分析操作的**唯一类型源**。所有入口层（HTTP、MCP）最终都归约到这些类型上，运行时执行路径只接受这些类型的实例。

**设计原理**：当类型源只有一份时，任何语义变更在 AOI schema 修改→重新生成后，不一致会在编译期（import error / type error）暴露，而不是运行时静默 drift。

**成功判据**：修改一个分析操作的字段（如给 `Observe` 加一个可选参数），只需改 AOI schema + 重新生成 + 适配 MCP DTO 转换逻辑。HTTP 层和 runtime 层不需要手动同步，因为它们直接使用生成的类型。

### 目标 2：消除校验碎片化 — 单一校验点

**要解决的问题**：请求合法性在 HTTP model、intent handler、compiler 三层分别检查，职责不清，遗漏和重复并存。

**目标**：AOI generated model 承担**完整的结构校验**（字段存在性、类型、值约束）。运行时只做**语义校验**（metric 是否存在、时间范围是否可查询等业务级检查）。中间不再有手工 dict key 检查。

**设计原理**：Pydantic model 的 `extra="forbid"` + 字段约束已经表达了"结构上什么是合法的"。把这层交给 AOI model 后，runtime 可以假设"如果收到了请求对象，它在结构上一定是对的"，只需要关注业务语义。

**成功判据**：intent handler 内部不再有 `if "metric" not in params` 类的结构检查；结构错误全部在 AOI model 层以 `ValidationError` 形式统一报出。

### 目标 3：AOI spec 不再悬空 — 生成物即运行物

**要解决的问题**：AOI generated model 存在但不在主路径上，形成"文档说的"和"代码跑的"两套语义。

**目标**：AOI generated model 不再只是 spec 校验对象，而是**运行时主路径的语义边界**。这是概念性的：分析操作的语义由 AOI 类型定义，但运行时的实际输入输出可以是包装了 AOI 模型及其他平台必要信息的信封类。关键在于分析语义部分必须由 AOI 类型承载，而不是另起一套手写类型。

**设计原理**：如果生成物只做"旁路校验"，它注定会和实际代码 drift。只有让生成物直接参与执行路径——无论是被直接使用还是被信封类组合使用——才能保证 spec 和实现永远一致。

**成功判据**：删除 `marivo/contracts/generated/aoi.py` 后，所有 intent 的 HTTP 端点和 runtime 执行全部编译失败——说明它们真正依赖了这些类型，即便是通过信封类间接依赖。

### 目标 4：定义 Marivo 执行信封 — AOI 与平台元数据分离

**要解决的问题**：运行时的返回值不只是 AOI artifact。当前代码在同一个 dict 中混杂了 AOI 分析结果（`result`）、Marivo 平台元数据（`step_ref`、`artifact_id`、`provenance`、`execution_metadata`）和产品级语义（`validation.status`、`issues`）。边界不清导致两种风险：要么把平台元数据泄漏进 AOI，要么为了"纯 AOI 返回"而丢失 lineage。

**目标**：定义一个明确的 **Marivo 执行信封（execution envelope）** contract，负责承载：
- `step_ref` / `artifact_id` / `session_id`（lineage 与 composition 所需）
- `provenance`（可追溯性）
- 产品级语义（derived intent 的 `validation.status`、`issues` 等）

AOI artifact 作为信封内的 `result` 字段嵌套存在。信封是 Marivo 的平台契约，AOI 是分析操作的语义契约，两者正交。

**设计原理**：AOI 定义的是"分析操作产出什么数据"，Marivo 需要额外回答"这个产出属于哪个 session、由哪些步骤推导而来、执行状态如何"。后者不应污染 AOI 语义，但也不能不定义——否则实现时必然回到"所有东西混在一个 dict 里"的状态。

**成功判据**：执行信封有独立的 Pydantic model 定义；derived intent（attribute/diagnose/validate）的产品级语义（如 `validation.status`）住在信封层而非 AOI artifact 内；下游消费者可以只取 `envelope.result` 得到纯 AOI artifact，或取整个 envelope 得到完整 lineage。

### 前提假设

- **AOI Observe union 统一**：当前 AOI schema 有 `Observe1`..`Observe4` 四个变体（字段组合差异）。本设计假设 AOI schema 将统一为单一 `Observe` 类型（带可选字段），消除 transport 层选择变体的负担。这是 AOI spec 演进议题，不在本设计实现范围内，但影响目标 1 中"唯一类型源"的落地方式。
- **Session/actor context 不属于 AOI**：session_id、cross-session ref 校验、actor 权限等属于 Marivo 执行信封和 transport 层职责，AOI contract 不感知这些。

---

## 2. 约束

### 2.1 已知前提

- 这是破坏性切换，不考虑向前兼容。
- AOI spec 修改需要严格 review 流程；AOI generated model 类必须由脚本工具根据 spec 直接生成，不允许手工修改生成物，以保证代码和 spec 的一致性。
- 实现阶段 AOI spec 冻结。讨论阶段可以提议增强 AOI spec，但进入实现后以当前 spec 为准。
- Marivo 自身代码（HTTP/MCP/runtime/compiler 等）可以自由修改以适配 AOI 边界。
- 如果 AOI 不能表达 Marivo 现有的高阶能力，优先砍掉 Marivo 对该能力的支持。
- 测试用例要适配 AOI 能力，而不是反向要求 AOI 迁就 Marivo 旧行为。

### 2.2 不做的事

- 不把 compiler 改成对外 contract 的第二语义层。
- 不在 runtime 里继续保留 typed-intent 作为主路径。
- 不把 MCP 变成直接暴露 AOI 原始对象的薄透传层。
- 不为保留旧高阶行为而扩展 AOI spec。

---

## 3. 现状判断

当前仓库的真实路径仍然是：

- HTTP 入口把 request model `model_dump()` 成 dict，再交给 runtime。
- MCP 工具层使用专门 DTO，再把参数转成 dict 交给 runtime。
- runtime 的 intent runner 继续从 dict 里手工提取字段。
- `observe` / `compare` / `decompose` 等 intent 在 runtime 内部再组 `AnalysisStepIR`，交给 `compile_step`。
- compiler 只接收内部 IR，并不直接消费 AOI generated model。

这意味着 AOI generated model 目前更多是 spec 校验对象，而不是主执行链的统一边界。

---

## 4. 目标架构

### 4.1 分层职责

| 层 | 负责什么 | 不负责什么 |
|---|---|---|
| AOI spec / generated model | 分析操作的唯一公开契约 | Marivo 私有运行逻辑 |
| HTTP transport | 直接收发 AOI 语义，必要时只保留轻薄包装 | 自定义一套 parallel intent contract |
| MCP adapter DTO | agent-friendly 入参、简化工具形状 | 业务语义、编译语义、artifact 语义 |
| Marivo 执行信封 | 承载 step_ref / artifact_id / provenance / 产品级语义，内嵌 AOI artifact 作为 result | 重新定义分析操作语义 |
| runtime intent layer | 校验、编排、执行 AOI 原子操作，组装执行信封 | 自定义高阶 intent 语义主路径 |
| compiler | 接收归一化后的内部 IR，做 lowering / SQL 生成 | 对外 contract 建模 |

### 4.2 数据流

```text
HTTP request / MCP DTO
        ↓
AOI generated request model（结构校验完成）
        ↓
共享 normalization 层（语义归一化）
        ↓
runtime intent orchestration
        ↓
internal IR / AnalysisStepIR
        ↓
compiler lowering / SQL generation
        ↓
engine execution
        ↓
AOI artifact → Marivo 执行信封
```

这里的关键点是：

- AOI model 是 runtime 的语义边界，不是 compiler 的外层包装。
- compiler 可以知道 AOI 请求的语义结果，但只通过 runtime 归一化后的内部输入得知，不直接绑定 AOI 对象结构。
- Marivo 的 step/session/artifact 元数据仍然由 Marivo 负责，它们住在执行信封层，不是 AOI contract 本体。

### 4.3 共享 normalization 层

当前的 intent handler 中存在大量重复的归一化/守卫逻辑：metric ref 归一化、dimension 去重与空列表清理、time scope 边界校验（如 hour granularity 需要 datetime 而非 date）、limit 范围约束、calendar policy ref 校验等。这些逻辑散布在 `observe.py`、`attribute.py`、`diagnose.py`、`validate.py` 的入口段中，每处独立维护。

将 AOI model 设为结构校验层后，这些归一化逻辑应当收敛为**共享的 normalization slice**，在 AOI 结构校验通过后、intent handler 业务逻辑之前统一执行。这样做的好处是：

- 归一化规则只定义一次，不会在 intent 之间 drift
- intent handler 可以假设输入已经归一化完毕，只关注编排逻辑
- 新增 intent 时不需要复制粘贴同一套守卫代码

---

## 5. HTTP 与 MCP 的分工

### 5.1 HTTP

HTTP 层应当成为 AOI contract 的直接 API 面。

目标是：

- 请求验证靠 AOI generated model。
- 响应模型靠 Marivo 执行信封（内嵌 AOI generated artifact 作为 `result` 字段）。
- HTTP 层最多保留少量路由级包装，但不能重新定义 intent 语义。

### 5.2 MCP

MCP 层不直接暴露 AOI 原始模型。

原因很简单：

- MCP 需要更短、更 agent-friendly 的工具参数。
- MCP 需要保留工具命名、参数顺序、默认值、局部补全等交互优化。
- MCP 适合做 DTO adapter，不适合做主契约层。

因此，MCP 的职责只是一件事：**把工具 DTO 转成 AOI generated model**，再调用 runtime。

---

## 6. Derived Intent 处理

`attribute`、`diagnose`、`validate` 继续保留为 Marivo 兼容入口，但它们不是 AOI core。

### 6.1 统一原则

derived intent 只做 orchestration，不做独立语义发明。

### 6.2 具体映射

- `attribute`：拆成 AOI `observe` + `compare` + `decompose` 的组合。
- `diagnose`：优先走 AOI `detect`，再根据结果补做 `compare` / `decompose`。
- `validate`：直接收敛到 AOI `test`。

### 6.3 取舍规则

如果某个旧 derived 能力无法用 AOI 原子操作自然表达，那么：

1. 先判断是否可以删掉。
2. 如果不能删，再判断是否可以缩窄。
3. 只有在讨论阶段才考虑是否值得推动 AOI spec 增强。

实现阶段不允许为了保留旧行为去改 AOI spec。

---

## 7. compiler 边界

compiler 维持为内部 lowering 层，职责只有两类：

1. 把 runtime 已经归一化的意图输入转成内部 IR。
2. 把内部 IR lowering 为 SQL / 查询执行计划。

compiler 不应再承担这些职责：

- 对外 intent contract 的定义
- MCP/HTTP 入参语义校验
- derived intent 的业务编排

这样做的好处是边界稳定：

- AOI 决定“Marivo 提供什么分析操作”。
- runtime 决定“怎么执行”。
- compiler 决定“怎么 lower 成可执行查询”。

---

## 8. 测试策略

测试必须跟着 AOI 走，同时保护已有的边界校验回归路径。

### 8.1 必须覆盖

- AOI examples 能通过 generated model 校验。
- HTTP intent 路由直接以 AOI contract 为主路径。
- MCP DTO 到 AOI model 的转换正确。
- runtime 能从 AOI 请求走到 AOI artifact（嵌套在执行信封中）。
- compiler 只接收内部 IR，不直接依赖外部 contract 类。
- `attribute` / `diagnose` / `validate` 的测试验证其正确编排 AOI 原子操作，并验证执行信封中产品级语义字段的正确性。

### 8.2 回归矩阵

以下路径是当前测试已保护的边界行为，本次重构最可能破坏。迁移后必须保持等价覆盖：

| 回归路径 | 当前测试位置 | 风险点 |
|---|---|---|
| cross-session ref rejection（compare/correlate/test） | `test_intent_api.py` | 校验逻辑从 HTTP model 迁移到 AOI model 或 envelope 时可能遗漏 |
| hour granularity 要求 datetime 边界 | `test_intent_api.py` | 这是 Marivo normalization 层逻辑，不在 AOI schema 中 |
| 空 dimensions 归一化为 None | `test_intent_api.py` | 归一化收敛到共享层时行为可能改变 |
| granularity 与 dimensions 互斥 | `test_intent_api.py` | AOI schema 可能不表达此约束，需要 runtime 守卫 |
| diagnose invalid request → 422 | `test_intent_api.py` | derived intent 校验路径变更 |
| validate invalid request → 422 | `test_intent_api.py` | derived intent 校验路径变更 |
| forecast missing horizon → rejection | `test_intent_api.py` | 如果 AOI model 接管校验，错误格式可能变化 |
| closed session → 422（diagnose/validate） | `test_intent_api.py` | session 状态检查属于 envelope 层 |
| AOI generated model schema 一致性 | `test_generated_models.py` | 重新生成后 schema 需与 spec 匹配 |

### 8.3 端到端 derived intent 路径

迁移完成后，至少需要一条完整的 derived intent 端到端路径作为集成验证：

1. **validate 完整路径**：HTTP 请求 → AOI model 校验 → normalization → observe(left) + observe(right) → test → 执行信封（含 `validation.status` + AOI `HypothesisTestResult`）→ HTTP 响应

这条路径覆盖了所有层的交互：AOI 结构校验、共享 normalization、原子 intent 编排、AOI artifact 生成、执行信封组装。

### 8.4 允许删除的测试

- 绑定旧 typed-intent 语义的测试。
- 依赖旧高阶能力、而 AOI 已不支持的测试。
- 把 compiler 当作对外语义层的测试。

---

## 9. 结论

这条边界的最终形态是：

- **AOI generated model = 分析操作的唯一语义契约**
- **Marivo 执行信封 = 平台元数据（lineage / provenance / 产品级语义）+ 内嵌 AOI artifact**
- **MCP = 适配 DTO**
- **runtime = AOI 编排与执行，组装执行信封**
- **共享 normalization 层 = AOI 结构校验之后、intent 编排之前的语义归一化**
- **compiler = 内部 IR lowering**
- **derived intents = Marivo 兼容入口，内部只编排 AOI 原子操作，产品级语义住在信封层**

这比“继续保留 typed-intent 主路径，再外挂 AOI 校验”更干净，也更符合当前的破坏性切换前提。
