# 规范 Schema 设计原则

本文档定义 Factum 中所有规范 schema（canonical schema）设计必须遵守的通用原则。

状态：规范设计原则（canonical design principles）。本文是跨领域的设计约束，适用于所有面向 agent 的结构化输出契约。

## 目的

Factum 的核心设计目标之一是提供 agent-first、machine-readable、可审计的分析状态接口。

为了实现这一目标，所有规范 schema（canonical schema）（无论是证据引擎（Evidence Engine）的内部实体、分析意图的输出契约，还是其他结构化输出）都必须遵守统一的设计原则。

本文档的目标是：
- 确保所有规范 schema（canonical schema）保持一致的设计质量
- 避免每个 schema 文档各自发明标识（identity）、null 语义、溯源信息（provenance）约定
- 为 schema 设计者提供明确的设计约束和检查清单
- 支持 schema 的长期演进和跨系统互操作

## 适用范围

这些原则适用于：

- **证据引擎实体（Evidence Engine 实体）**：finding, proposition, assessment, action proposal
- **分析意图输出**：原子意图和派生意图的响应契约
- **其他规范输出（canonical 输出）**：任何需要 agent 直接消费、可追溯、可演进的结构化输出

这些原则不适用于：

- 临时性的内部数据结构
- 纯展示层的 UI 格式
- 不需要跨会话稳定性的瞬态对象

## 核心原则

### 1. 标识（Identity）与谱系（Lineage）必须显式声明

每个规范实体（canonical 实体）都必须显式回答：

- 它的标识边界（identity boundary）是什么
- 它绑定哪条谱系（lineage）
- 它是否允许跨谱系（lineage）复用标识（identity）
- 它是 immutable 还是会话内局部（session-local）可演化状态

推荐默认值：

- 工件（`artifact`）与从工件（artifact）派生的 fact objects 绑定 source artifact lineage
- fact objects 默认 immutable
- judgment / assessment objects 可随新证据进入而更新
- 规范层（canonical layer）默认不做跨谱系（lineage）的隐式标识（identity）合并

必须明确区分：

- 重读同一个规范对象（canonical object）
- 重新执行产生新对象（object）
- 语义相似但谱系（lineage）不同的对象（object）

若这三者未被区分，后续几乎一定会在 ID、去重、缓存、审计和 agent 引用上出现歧义。

#### 1.1 标识（Identity）输入必须克制

规范 ID（canonical ID）默认只应绑定语义标识边界（identity boundary），不应混入仅用于解释、渲染或版本隔离的字段。

默认不应进入标识（identity）输入的字段包括但不限于：

- `schema_version`
- projection version
- explanation / rationale 文本
- 当前排序位置
- 不改变标识边界（identity boundary）的冗余类型锚点

只有在某字段的变化会明确改变规范对象（canonical object）的标识边界（identity boundary）时，才应进入 ID 生成输入；否则应作为独立字段存储。

#### 1.2 可演化状态对象必须定义版本化规则

若某对象不是 immutable fact，而是会随 session 演化的 state object，则 schema 文档必须额外回答：

- 什么算"重读同一 snapshot"
- 什么算"重新计算但不产生新 snapshot"
- 什么变化会强制创建新 snapshot / 新 version
- 是否允许 history 分叉；若不允许，由谁线性化
- supersede / revision 链是否必须连续

若这些规则缺失，实现层通常会把"重算""重读""升级""覆盖"混成同一件事。

### 2. Nullability 与 Empty Semantics 必须单义

每个 nullable 字段都必须声明 `null` 的唯一语义。

推荐只允许以下几类：

- `unknown`
- `not_applicable`
- `not_yet_resolved`

同一个字段不得在不同场景下混用多种 `null` 语义。

同样，每个空对象 / 空数组字段都必须声明 canonical empty semantics。例如：

- `{}` 是否表示 overall / unsliced / no-extra-constraint
- `[]` 是否表示 no-supporting-items / no-known-gaps / no-applicable-options

面向 agent 的关键定位字段应尽量 total，而不是 nullable。只有在"该字段对该对象无法定义"时，才应允许 `null`。

#### 2.1 Composite View 的 null 语义应由主状态字段支配

对面向 agent 的复合读取视图，不应让多个关键字段各自发明独立的 null 语义。

更推荐：

- 先定义一个主状态字段
- 其他辅助字段的 `null` / `[]` / 缺失语义尽量由该主字段统一支配

这样可以避免 consumer 必须联合判断多个 nullable 字段，才能推断对象是否"尚未进入某流程"。

### 3. Base 字段与 Subtype 字段的分配必须可解释

base schema 只应承载跨 subtype 稳定存在、且对 agent 通用有意义的轴。

若某字段只对部分 subtype 有意义，则有两种合法处理方式：

1. 放入 base，并明确规定"不适用时为 `null`"
2. 放入 subtype payload，不做伪统一

不应仅为了 schema 外观整齐，把强 subtype-specific 的字段提升到 base。

反过来，也不应把本应作为统一读取轴的字段全部塞进 subtype payload，导致 agent 无法稳定过滤、排序或聚合。

#### 3.1 禁止 base schema 出现语义垃圾桶字段

canonical base schema 不应使用语义模糊的兜底字段来暂存尚未想清楚的信息，例如：

- `related_*`
- `misc_*`
- `other_context_*`
- `extra_metadata` 一类未受约束的自由 map

若某信息不能稳定归类，合法做法只有三种：

1. 明确放入 subtype payload
2. 建模为独立 support object
3. 承认该语义尚未进入 canonical schema，而不是先塞进垃圾桶字段

### 4. Provenance 与 Reference 必须 machine-readable

canonical provenance 的首要目标是支持 agent 与规则系统稳定消费，而不是方便人类临时调试。

因此：

- provenance 应优先使用结构化 ref，而不是自由文本 locator
- source lineage ref 与 consumer projection ref 必须区分
- provenance 应能回答"源自哪里""源内哪一项""依据哪个 contract version 解释"

推荐 provenance 至少显式覆盖：

- source object lineage
- in-source item locator
- source schema / contract version
- derivation / extractor version

projection ref 只能指向 bounded consumer view，不得替代 canonical source ref。

#### 4.1 Reference 图约束必须显式声明

若某 canonical object 可以引用其他 canonical objects，schema 文档必须说明：

- 是否允许跨 session 引用
- 是否允许跨 proposition / subject / lineage 引用
- 是否允许引用同类历史对象
- 引用图是否必须保持 DAG
- 哪些对象允许原子共同创建，以避免伪循环依赖

如果这些规则未声明，support object 一旦增多，canonical graph 很快会变得不可审计、不可裁剪、也不可稳定消费。

### 5. Agent Consumption Contract 也是 canonical schema 的一部分

若某个 canonical object 被声明为 agent 可直接消费，则其 schema 文档不能只定义字段，还必须定义最小读取规则。

至少应包括：

- 可查询轴
- 默认排序规则
- 稳定截断规则
- 引用格式
- 局部最小闭包读取方式

如果缺少这些规则，schema 虽然 machine-readable，但对 agent 仍然不是低歧义、可规划的 contract。

#### 5.1 聚合判断字段必须定义推导约束

凡是会被 agent 直接据此决策的汇总字段，例如：

- `status`
- `confidence_grade`
- `priority_grade`

schema 文档都不应只给枚举值，而应进一步说明：

- 它由规则推导，还是由固定公式推导
- 是否存在全局 veto / cap / floor 规则
- 解释字段与结果字段之间如何对应

否则这些字段虽然结构化，但仍然属于黑盒输出。

### 5.2 执行面、状态面与投影视图必须显式分离

若某个 schema 文档属于面向 agent 的交互契约，它还必须说明自己位于哪一层交互面：

- analysis action surface
- analysis state surface（分析状态面）
- consumer projection surface

同一 canonical object 不应同时承担以下多种职责：

- 执行动作输入
- 当前判断状态
- 动作建议
- 报告式摘要

更具体地说：

- action surface 文档应定义 agent 如何发起 typed analysis actions
- state surface 文档应定义 agent 如何读取可决策的 canonical analysis state
- projection surface 文档应定义如何对 canonical action / state 做 bounded consumer view

如果一个对象必须同时混装这些职责，通常说明 schema 边界已经失真。

### 6. Versioning 与 Evolution 必须进入设计，而不是留给实现兜底

canonical layer 不能假定 source contract 永远不变。

因此在 schema 设计时必须区分：

- source schema / artifact contract version
- derivation logic / extractor version
- consumer projection version（若存在）

当 source contract 出现 breaking change 时，不应依赖隐式兼容推断；应通过显式 version boundary 处理。

否则：

- 同一个字段名可能承载不同语义
- 旧对象可能被新规则误读
- lineage 与 audit 会失去可解释性

### 7. Projection 只能压缩，不得重定义

projection 是 canonical state 的消费视图，不是新的证据层。

因此 projection 可以：

- 截断
- 排序
- 聚合展示
- 做 token-budget 压缩

但 projection 不得：

- 发明 canonical state 中不存在的新事实
- 改写 canonical object 的 identity
- 覆盖或偷换原语义

若某个 consumer view 需要新的事实语义，它应回到 canonical layer 显式建模，而不是在 projection 中偷偷长出来。

### 8. Canonical Schema 必须包含负向契约

canonical schema 文档不能只写"合法对象长什么样"，还必须写"哪些状态或引用是非法的"。

至少应覆盖：

- 非法 identity 组合
- 非法状态转换
- 非法 cross-reference
- 非法 `null` / empty 语义
- version boundary 冲突

如果没有这些负向契约，schema-level test 很容易只覆盖 happy path，而把真正危险的歧义留到实现期。

## 经验法则

在设计新实体或新边类型时，可用以下问题做自检：

1. 这是事实，还是判断，还是动作候选？
2. 它会随着新证据变化吗？
3. agent 会读取它，还是操作它，还是只在溯源时查看它？
4. 它是否混合了承诺不同生命周期的字段？
5. 它是否可以不依赖自由文本而被稳定消费？

若无法清晰回答，通常说明抽象边界还不够干净。

在撰写具体 schema 文档时，还应额外检查以下 checklist：

1. 这个对象的 identity 绑定什么？
2. 它绑定哪条 lineage？
3. 它是 immutable，还是会随 session 演化？
4. 哪些字段允许 `null`？每个 `null` 各自表示什么？
5. 空对象 / 空数组在该 schema 中表示什么？
6. 哪些字段属于 base？哪些字段属于 subtype？理由是什么？
7. provenance 是否结构化且 machine-readable？
8. agent 如何查询、排序、截断和引用它？
9. source contract 变化时，它通过什么 version boundary 保持可解释？

若一篇 schema 文档无法回答这些问题，则它通常还不算 decision-complete 的 canonical contract。
