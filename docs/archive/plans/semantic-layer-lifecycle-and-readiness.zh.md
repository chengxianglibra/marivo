# Factum Semantic Layer 生命周期与 Readiness 设计

## 1. 背景

当前 Factum semantic layer 的对象生命周期主要使用 `draft / published / deprecated`。

这个状态模型对实现层是可用的，但对用户并不清晰，因为它把几类不同含义混在了一起：

- 对象 schema 是否已经写完
- 跨对象引用是否已经合法
- 是否已经具备 runtime grounding
- 是否能被默认 discovery / resolve / intent execution 使用
- 某些附加能力（例如 inferential capability）是否已经具备
- profile 是否仍然匹配当前 subject revision

结果是，系统可能出现“对象已经 published，但用户在使用时仍然报错或不可执行”的情况。当前仓库里最明显的是 metric：

- metric 可以单独 publish
- 但如果没有 published metric binding，则运行时会报 `Resolved metric is not grounded by any published binding`

这会让用户形成错误预期：用户看到“已发布”会自然理解为“可用”，而不是“合同已冻结但可能还不能跑”。

本文档的目标是重新整理 semantic layer 各类对象的创建、发布、使用生命周期，使用户的认知成本最低，同时保留建模与治理所需的分阶段能力。

## 2. 设计目标

### 2.1 用户目标

用户只需要理解三个问题：

1. 这个对象现在能不能用？
2. 如果不能用，还差什么？
3. 它能用到什么程度？

用户不应该被迫理解：

- compiler gate
- binding_scope
- requirement profile / capability profile
- revision mismatch
- grounding 与 publish 的内部实现顺序

### 2.2 系统目标

新的生命周期模型需要满足以下要求：

1. **区分合同完成与运行可用**
2. **允许分阶段建模**
3. **把不可用原因前移到目录、详情与 resolve**
4. **在对象更新后自动传播失效**
5. **让 runtime 默认只消费真正可用的对象**
6. **尽量减少对现有存储状态机的一次性破坏**

## 3. 当前问题总结

### 3.1 生命周期语义混乱

`published` 同时承担了以下几种语义：

- 已通过引用校验
- 已进入对外目录
- 可以被 runtime resolution 看见
- 可能能运行
- 可能还不能运行

这会导致目录语义和执行语义不一致。

### 3.2 不可用错误暴露过晚

很多问题要到分析编译或执行时才暴露，例如：

- metric 没有 grounding binding
- process 缺 inferential capability profile
- dimension 虽然存在，但并不支持 grouping
- time anchor 不匹配

这对用户而言属于“选择阶段缺少反馈，执行阶段才爆炸”。

### 3.3 不同对象类型缺少统一心智模型

当前各对象的“能否使用”规则分散在：

- object publish validation
- binding publish validation
- runtime resolution
- compiler validation
- capability profile loading

用户无法从一个统一状态知道对象是否 ready。

## 4. 核心设计：分离 Lifecycle 与 Readiness

本设计的核心是把对象状态拆成两条轴：

- **Lifecycle**：对象编辑与治理生命周期
- **Readiness**：对象在当前 revision 下的运行可用状态

两者不可混用。

### 4.1 Lifecycle 状态

Lifecycle 用来回答“对象处于哪个治理阶段”。

统一状态如下：

- `draft`
- `validated`
- `active`
- `deprecated`

解释如下：

#### `draft`

- 对象正在创建或编辑中
- 不保证引用完整
- 不参与运行时目录

#### `validated`

- 对象自身 contract 合法
- 引用合法
- 但不保证运行可用
- 适合 review、审批、继续补充依赖

#### `active`

- 对象已进入正式可引用目录
- 这是“治理上已发布”的状态
- 但是否运行可用仍需看 readiness

#### `deprecated`

- 历史对象
- 默认不推荐新请求使用
- 保留解析与追溯能力

### 4.2 Readiness 状态

Readiness 用来回答“对象现在能不能被实际使用”。

统一状态如下：

- `not_ready`
- `ready`
- `stale`

解释如下：

#### `not_ready`

- 对象尚未满足运行条件
- 需要明确返回阻塞原因

#### `ready`

- 对该对象所属的 runtime 使用场景，前置条件已经满足
- 用户看到 `ready` 应理解为“默认可用”

#### `stale`

- 对象曾经 ready
- 但由于依赖 revision 变化、binding 失效、profile 不再匹配等原因，当前 revision 下不再可靠

`stale` 必须显式化，不能只在运行时隐式忽略。

## 5. 用户可见心智模型

为了降低认知成本，用户视角下只展示两类信息：

1. 生命周期：`Draft / Active / Deprecated`
2. 准备度：`Ready / Not Ready / Stale`

其中 `validated` 可以作为建模视图中的中间态展示，但不要求所有终端用户理解。

推荐的默认展示规则：

- 建模后台：显示 lifecycle + readiness
- 分析入口、metric picker、process picker：默认只显示 `active + ready`
- 如果用户显式访问一个非 ready 对象，必须返回 why-not-ready

## 6. 统一状态流转

推荐状态流转如下：

```text
draft -> validated -> active
active -> deprecated

readiness:
not_ready <-> ready
ready -> stale
stale -> ready
```

### 6.1 创建

新对象创建后进入：

- lifecycle = `draft`
- readiness = `not_ready`

### 6.2 校验

用户或系统执行 validate 后：

- 若 contract 与引用合法，则 lifecycle 进入 `validated`
- readiness 仍可能是 `not_ready`

### 6.3 激活

当对象达到治理发布条件后：

- lifecycle 进入 `active`

注意：

- `active` 不等于 `ready`
- `active` 只表示它是正式目录的一部分

### 6.4 Readiness 计算

对象一旦进入 `validated` 或 `active`，系统就持续计算 readiness。

当运行前置条件满足时：

- readiness = `ready`

当依赖对象、binding、profile、revision 变化导致条件不再满足时：

- readiness = `stale` 或 `not_ready`

建议区分规则：

- 曾经 ready 后失效 -> `stale`
- 从未满足过运行条件 -> `not_ready`

## 7. 各类对象的 Readiness 定义

下面定义各对象的“ready”语义。

### 7.1 Entity

#### Entity 的作用

Entity 表示业务对象本体，不一定直接执行，但它通常是 metric、process、binding 的依赖。

#### Entity ready 条件

一个 entity 要进入 `ready`，至少需要：

1. lifecycle 为 `active`
2. identity contract 完整且合法
3. 若 runtime 需要 physical grounding，则存在至少一个可用的 published entity binding，覆盖：
   - identity keys
   - required primary_time_ref
   - required stable descriptors

#### 用户语义

- `entity active + not_ready`：概念建好了，但还没有可靠落地
- `entity active + ready`：概念和物理 grounding 都齐了

### 7.2 Metric

Metric 是当前最关键的对象。

#### Metric ready 条件

一个 metric 要进入 `ready`，至少需要：

1. lifecycle 为 `active`
2. metric contract 合法
3. 引用的 entity / time / dimension / process requirement 都已满足激活条件
4. 至少有一个 `binding_scope = metric` 的 active binding
5. 该 binding 覆盖 metric 所需的全部 `metric_input`
6. 若 metric 声明 `primary_time_ref`、`population_subject_ref` 等额外要求，binding 也必须覆盖
7. 若 metric 的某些 intent 依赖 process 或 inferential capability，则相应能力必须存在

#### Metric 能力标签

metric 除了 ready 状态，还应附带 capability：

- `supports_observe`
- `supports_detect`
- `supports_attribute`
- `supports_diagnose`
- `supports_validate`
- `supports_decompose`

这样可以避免“metric 是 ready 的，但这个 intent 不支持”的额外困惑。

#### 用户语义

- `metric active + not_ready`：语义合同已激活，但还不能用于分析
- `metric active + ready`：可被 picker 与 runtime 默认使用

### 7.3 Process

#### Process ready 条件

一个 process 要进入 `ready`，至少需要：

1. lifecycle 为 `active`
2. process contract 合法
3. 若 process 需要 physical grounding，则存在满足要求的 process binding
4. 若用于 inferential intents，则存在匹配当前 revision 的 capability profile
5. process 与 metric requirement profile 兼容

#### Process 能力标签

推荐返回：

- `supports_time_projection`
- `supports_experiment_inference`
- `supports_cohort_inference`
- `inferential_ready`

#### 用户语义

不要把 process 的所有能力压成一个状态位。

更好的表达是：

- 状态：`ready / not_ready / stale`
- 能力：`basic`, `inferential`

### 7.4 Dimension

#### Dimension ready 条件

一个 dimension 要进入 `ready`，至少需要：

1. lifecycle 为 `active`
2. dimension contract 合法
3. 如果打算用于 grouping，则 `supports_grouping = true`
4. 如果有 `time_derived_requirement`，则其 required anchor 能被某类 metric/process/time 组合满足

#### Dimension 的特殊性

dimension 的“可用”往往依赖上下文，因此建议拆成：

- object-level readiness：这个 dimension 是不是基本可用
- request-level compatibility：在这次请求里是不是可用

#### 用户语义

目录里显示：

- `ready for grouping`
- `requires time anchor: time.xxx`

而不是把所有兼容性问题都延迟到执行时。

### 7.5 Time Semantic

#### Time ready 条件

Time semantic 较为自包含。通常：

1. lifecycle 为 `active`
2. time contract 合法
3. semantic role 合法

即可视为 `ready`。

#### 说明

time object 本身 ready，并不意味着任意请求 time_scope 都能成功解析；后者属于 request-level validation。

### 7.6 Enum Set

Enum set 也属于接近自包含的对象。

#### Enum ready 条件

1. lifecycle 为 `active`
2. enum schema 合法

通常即可进入 `ready`。

### 7.7 Binding

Binding 是 grounding 对象，本身必须更严格。

#### Binding ready 条件

1. lifecycle 为 `active`
2. 绑定对象本身已 `active`
3. imported bindings 已 `active`
4. carrier 可解析到 synced source_object
5. field / target mapping 完整
6. 若是 metric binding，覆盖全部 required `metric_input`

#### 用户语义

Binding 一旦 `ready`，就应被视为“可参与 runtime grounding”。

### 7.8 Compatibility Profile

profile 不应被用户理解为普通 semantic object，而应理解为“对某个 revision 的能力认证”。

#### Profile ready 条件

1. lifecycle 为 `active`
2. subject 已 `active`
3. profile 绑定的 `subject_revision` 与当前 resolved revision 一致

#### 失效行为

如果 subject revision 变化：

- profile lifecycle 保持 `active`
- profile readiness 自动变为 `stale`

这比“published 但 silently ignored”更直观。

## 8. API 设计建议

### 8.1 所有 semantic object 返回统一状态字段

每个对象详情与列表都返回：

```json
{
  "lifecycle_status": "active",
  "readiness_status": "not_ready",
  "blocking_requirements": [
    {
      "code": "METRIC_BINDING_MISSING",
      "message": "No active metric binding grounds this metric"
    }
  ],
  "capabilities": {
    "supports_observe": true,
    "supports_validate": false
  }
}
```

### 8.2 Resolve 默认行为

建议区分两个 surface：

1. **catalog / picker / discovery**
   - 默认只返回 `active + ready`

2. **admin / modeling / explicit resolve**
   - 可返回 `active + not_ready`
   - 但必须带 why-not-ready

### 8.3 状态相关 API

建议新增三个显式动作：

- `POST /semantic/.../{id}/validate`
- `POST /semantic/.../{id}/activate`
- `POST /semantic/.../{id}/deprecate`

而不是继续把一切都压进 `publish`。

如果短期不想大改路由，可兼容：

- `publish` 内部等价于 `activate`
- 但 API response 明确带出 readiness

## 9. UI / UX 设计建议

### 9.1 列表页

列表页每个对象至少显示：

- 名称
- object type
- lifecycle badge
- readiness badge
- blocker count

例如：

- `Metric / Active / Not Ready / 2 blockers`

### 9.2 详情页

详情页必须回答：

1. 这个对象能不能用？
2. 哪些地方已经完成？
3. 哪些依赖还缺？
4. 如果它曾经可用，现在为什么 stale？

#### 推荐展示结构

- Summary
- Lifecycle
- Readiness
- Blocking requirements
- Dependencies
- Dependents
- Capabilities

### 9.3 Picker / 运行入口

运行入口默认只显示 `ready` 对象。

如果用户切换“显示不可用对象”，则应在候选项旁边直接显示 blocker，例如：

- `metric.cpu_time (not ready: no metric binding)`

## 10. 编译器与运行时行为调整

### 10.1 运行入口不再把 readiness 错误伪装成普通执行错误

对于 metric/process/dimension 这类核心对象：

- 编译前先做 object-level readiness gate
- 若不 ready，则直接返回结构化 readiness error

错误示例：

```json
{
  "code": "SEMANTIC_OBJECT_NOT_READY",
  "subject_ref": "metric.cpu_time",
  "message": "Metric is active but not ready for runtime use",
  "blocking_requirements": [
    {
      "code": "METRIC_BINDING_MISSING",
      "message": "No active metric binding grounds this metric"
    }
  ]
}
```

### 10.2 request-level compatibility 与 object-level readiness 分开

必须区分：

- object-level readiness：对象本身可不可用
- request-level compatibility：在这次请求上下文里能不能用

例如：

- dimension 不支持 grouping -> object-level not_ready
- dimension 需要 `time.signup_time` 但这次 metric 没有对应 anchor -> request-level incompatible

这两个错误不能混在一起。

## 11. 依赖传播与失效管理

对象 readiness 必须支持依赖传播。

### 11.1 触发源

以下事件会触发 readiness 重新计算：

- semantic object 更新
- binding 更新
- binding 激活 / 失效
- profile 更新
- profile subject revision 变化
- imported binding 状态变化

### 11.2 传播方向

示例：

- entity 变化 -> 影响 metric / process / binding
- metric 变化 -> 影响 metric binding / profile / downstream picker
- process 变化 -> 影响 process capability profile
- binding 变化 -> 影响 metric / process / entity readiness

### 11.3 `stale` 规则

满足以下条件之一时，推荐标记为 `stale`：

- readiness 曾经是 `ready`
- 现在因为依赖 revision 或 binding/profile 不匹配而失效

如果对象从未 ready，则保留 `not_ready` 即可。

## 12. 向后兼容迁移方案

本设计建议分两阶段落地。

### 阶段 A：先引入 readiness，不立即替换底层 lifecycle

短期保持现有数据库主状态：

- `draft / published / deprecated`

在 API 与服务层新增：

- `lifecycle_status`
- `readiness_status`
- `blocking_requirements`
- `capabilities`

映射规则：

- `published` -> lifecycle `active`
- `draft` -> lifecycle `draft`
- `deprecated` -> lifecycle `deprecated`

这样可以先修复用户心智，不必一次性重写所有持久化逻辑。

### 阶段 B：再决定是否把 `validated` 显式持久化

如果后续治理流程需要：

- review
- approval
- staged activation

则再把 `validated` 变成真实持久化状态。

如果不需要，也可以让 `validated` 保留为内部派生阶段。

## 13. 推荐实施顺序

### 阶段 1：统一 readiness 计算

1. 为 entity / metric / process / dimension / time / enum / binding / profile 定义 readiness evaluator
2. 输出统一 `blocking_requirements`
3. 输出统一 `capabilities`

### 阶段 2：修改 API response

1. 列表接口增加 readiness 字段
2. 详情接口增加 readiness 字段
3. resolve / catalog 默认按 ready 过滤

### 阶段 3：修改运行入口

1. metric/process picker 只显示 ready
2. compiler 在执行前返回 readiness error
3. 替换“缺少 measure 绑定”这类实现导向错误文案

### 阶段 4：补充 stale 传播

1. profile revision mismatch 显式转成 stale
2. binding 失效传播到 metric/process/entity
3. 对象更新后的 dependent invalidation

### 阶段 5：视需要再收敛 lifecycle

若治理流程成熟，再决定是否引入真实的 `validated` 持久化状态与专门路由。

## 14. 设计结论

semantic layer 不应该再让用户把“已发布”理解为“可用”。

更低认知成本的做法是：

1. 把治理生命周期与运行准备度分开
2. 用 `readiness_status` 明确表达对象是否真正可用
3. 让目录、picker、resolve 默认只展示 `ready`
4. 把 blocker 前移到对象详情与选择阶段
5. 用 `stale` 显式表达 revision / profile / binding 失效

对用户而言，最终心智模型应收敛为：

- `Draft`：还在编辑
- `Active + Ready`：可以直接用
- `Active + Not Ready`：概念存在，但还差依赖
- `Stale`：之前能用，现在因依赖变化需要修复
- `Deprecated`：历史对象，不建议新用

这套模型既保留 semantic layer 的分阶段建模能力，也避免了“publish 了但其实不能用”的误导。
