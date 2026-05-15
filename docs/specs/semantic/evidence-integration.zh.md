# Evidence Engine / Semantic Layer 集成契约

> **过渡说明**：本文档已更新为反映 OSI Dataset-native 模型。主要变更如下：
> - `binding contracts` → Dataset-native grounding（物理接地由 Dataset/Field 行内表达）
> - `metric_ref` / `process_ref` → 更新为 OSI 对象引用（`metric.*` / `dataset.*` / `field.*`）
> - `carrier_row_filter` → Dataset-level row filter 或 Metric.filters
> - `binding_refs` → Dataset/Field 引用
> - Authority Boundary 中 binding layer authority → Dataset schema authority
> - `source_object_ref` → Dataset.source
>
> 核心的 finding/proposition → semantic ref 映射机制仍然有效，只是引用路径从 binding 改为 Dataset-native。

本文定义 Evidence Engine canonical outputs 与 Semantic Layer stable refs 的集成边界。

状态：draft design。本文是规范性集成 contract，负责回答：

- `FindingRef` / `PropositionRef` 如何关联到 `metric_ref` / `dataset.*`
- canonical artifact refs 如何与更广义的 `semantic_ref` 体系协同
- 哪些 ref 负责 canonical evidence identity，哪些 ref 负责治理语义 identity
- 读取面、Dataset grounding、compiler、IR 在这一映射中各自承担什么职责

本文不重写 canonical evidence schema，也不重写 semantic object schema；它只固定二者的分层与映射规则。

## 目标

本文固定以下结论：

1. `FindingRef` / `PropositionRef` 是 **canonical evidence identity**，是 session-local typed refs。
2. `metric_ref` / `dataset.*` / `field.*` / `dimension.*` / `time.*` 是 **stable semantic identity**，是跨 artifact、跨 session、跨编译轮次可复用的治理语义引用。
3. canonical artifact refs 是 **provenance lookup handles**，不是 semantic identity。
4. canonical refs 与 semantic refs 之间允许建立关联，但二者 **不得互相替代**。
5. 这些关联 **只允许** 基于现有 canonical fields、lineage、typed input snapshots、Dataset grounding 推导；本文 **不引入新的统一映射槽位**。

## 非目标

本文不定义：

- 新的 canonical object 类型
- 新的统一 ref registry
- projection/UI handle 到 canonical ref 的升级路径
- 使用自由文本、LLM explanation、SQL 文本推断 semantic identity
- 外部 HTTP wire schema 的新增字段

## Authority Boundary

**Authority** 指对象 identity 的定义责任归属。Evidence Engine 与 Semantic Layer 的 authority boundary 固定如下：

- Evidence Engine 负责 canonical object identity、canonical edge semantics、provenance、lineage、assessment membership closure。
- Semantic Layer 负责 stable semantic contracts、Dataset-native grounding、compiler validation、typed input normalization、IR snapshots。

因此：

- canonical evidence identity **MUST NOT** 由 semantic refs 定义或覆盖。
- semantic identity **MUST NOT** 由 artifact_id、finding_id、proposition_id 这类 session-local handles 定义。
- ref 映射 **MUST** 是 "由 canonical evidence 指向其语义锚点" 或 "由 semantic contract 解释 evidence 的 meaning"，而不是把二者折叠为同一主键。

## 术语与引用分层

### 二层 Taxonomy

| 层级 | 典型 ref | 回答的问题 | 不回答的问题 |
| --- | --- | --- | --- |
| canonical session-local ref | `FindingRef`、`PropositionRef`、`ArtifactRef`、`ArtifactItemRefRef` | "当前 session 中是哪一个 canonical finding / proposition / artifact item" | "它代表哪个治理语义对象" |
| stable semantic ref | `metric_ref`、`dataset.*`、`field.*`、`dimension.*`、`time.*`、其他 `semantic_ref` | "该 evidence / contract 关心的治理语义对象是什么" | "本次 session 中具体是哪条 canonical evidence" |

### Semantic Ref 格式约定

本文中的 `.*` 是 ref family 通配写法，不是实际 wire/ref 字符串的一部分。

- `metric_ref` 采用 `metric.{metric_key}`，例如 `metric.orders_gmv`
- `dataset.*` 表示 `dataset.{dataset_key}` 家族，例如 `dataset.user`
- `field.*` 表示 `dataset.{dataset_key}.field.{field_key}` 家族，例如 `dataset.user.field.created_at`
- `dimension.*` 表示 `dimension.{dimension_key}` 家族，例如 `dimension.country`
- `time.*` 表示 `time.{time_key}` 家族，例如 `time.order_created_at`

### Canonical Artifact Refs 粒度说明

`ArtifactRef = { artifact_id: string }` 指向完整 artifact。

`ArtifactItemRefRef = { artifact_id: string; item_ref: ArtifactItemRef }` 指向 artifact 内的特定 item（如某个 row、bucket、candidate）。`ArtifactItemRef = { collection, index | key }` 是其内部结构，不作为独立顶层类型使用。

本文以当前 runtime canonical finding contract 为准，采用扁平化的 `artifact_id` 字段而非嵌套 `artifact_ref` 包装；二者语义都表示"指向特定 artifact 内的特定 item"，但本文固定前者作为集成边界。

二者粒度不同，但都属于 session-local canonical refs。用途由上下文决定：
- `ArtifactRef` 用于整体 lineage、provenance 入口
- `ArtifactItemRefRef` 用于 payload 内的 item-level 引用（如 `left_ref`、`right_ref`、`candidate_ref`）

## 用途边界与语义锚点

### Canonical Artifact Refs 的多重用途

同一 canonical ref 可在不同上下文承担不同职责：

- `ArtifactItemRefRef` 在 finding payload 中承载分析语义（"比较左侧引用哪个 item"）
- 同一 ref 在 provenance 字段中承担溯源职责（"该 finding 从哪个 item 抽取"）

用途由字段位置与 schema contract 决定，不强制归入单一层。

### 语义锚点（Semantic Anchor）定义

**语义锚点（semantic anchor）** 指 canonical object 中承载稳定语义引用的字段或结构。例如：
- `FindingSubject.metric` 是 finding 的 metric 语义锚点
- `FindingSubject.slice` 是 finding 的 dimension 语义锚点集合
- `PropositionSeedRef.finding_ref` 是 proposition 的 seed 语义锚点
- `lineage.source_artifact_lineages` 是 provenance-based 语义锚点入口

语义锚点本身是 canonical 字段，从中可推导出 semantic refs，但 anchor ≠ semantic ref。

## 规范性结论

- canonical refs **MUST** 保持最小自包含的 typed ref 形状，不依赖外层上下文猜测 target。
- semantic refs **MUST** 继续表达稳定治理语义，而不是回退成 artifact locator 或 session-local object id。
- projection refs、top-k handles、UI row keys **MUST NOT** 伪装成 canonical refs，也 **MUST NOT** 成为 semantic mapping 的 authority。

## Canonical Evidence Ref -> Semantic Ref 的通用推导规则

`FindingRef = { session_id, finding_id }` 和 `PropositionRef = { session_id, proposition_id }` 只标识 canonical evidence。

它们与 semantic refs 的关联 **MUST** 通过既有字段或 lineage 解析，允许的来源如下：

1. **finding/proposition 自身的 subject**
   - `FindingSubject.metric` → `metric_ref`
   - `FindingSubject.slice` → `dimension.*` refs
   - `FindingSubject.entity` → `dataset.*` refs
   - `PropositionSubject` 同理

2. **step_ref**
   - `step_ref` 本身是 session-local provenance handle，指向 step metadata
   - semantic identity 来自该 step metadata 中的 `typed_inputs`、`dataset_refs`、`filter_context` 等字段
   - 推导路径可表示为：`step_ref -> step metadata -> typed_inputs / dataset_refs / filter_context -> semantic refs`
   - `step_ref` 本身不内联承载 semantic ref 值

3. **artifact_id / lineage.source_artifact_lineages**
   - 通过 artifact contract、binding lineage 可推导 semantic refs
   - artifact 本身是 provenance 入口，不是 semantic identity

4. **payload 中显式携带的 typed refs**
   - `FindingRef`、`ArtifactRef`、`ArtifactItemRefRef` 可作为语义锚点入口

5. **seed_finding_refs / lineage fields**
   - `seed_finding_refs`、`source_step_refs`、`source_artifact_lineages` 都可作为上游 anchor 入口
   - 通过 seed findings、step lineage、artifact lineage 可回溯 semantic refs
   - 字段结构与约束见下文"关键字段语义"

### Provenance 范围限定

本文中的 provenance 需要区分两层：

- **artifact locator provenance**：指 `artifact_id`、`artifact_item_ref` 这类来源定位字段，回答"证据从哪个 artifact/item 来"
- **full runtime provenance / lineage**：指 `extractor_version`、`artifact_schema_version`、seed lineage、step lineage 等完整溯源信息，回答"证据如何产生、如何演进"

完整的 provenance 信息见：
- `finding.md` 的 `FindingProvenance` 定义
- `proposition.md` 的 `PropositionLineage` 定义

### 关键字段语义详解

#### `FindingSubject.metric` → `metric_ref`

当 `FindingSubject.metric = "orders_gmv"`（非 null 字符串）时：
- 对应的 `metric_ref` 格式为 `metric.orders_gmv`（`metric.` 前缀 + metric key）
- 若 `FindingSubject.metric = null`，该 finding 不提供 metric 语义锚点，无法直接推导 `metric_ref`

禁止：
- 从 artifact 名称、projection 行键、自由文本推断 `metric_ref`
- 空字符串或 `"unknown"` 作为 metric anchor 值

#### `FindingSubject.slice` → `dimension.*` refs

`FindingSubject.slice = { "country": "US", "device": "iOS" }` 时：
- `slice` 的每个 key 映射到 dimension schema 中已定义的 dimension
- `"country"` → `dimension.country`，`"device"` → `dimension.device`
- 若 key 未在 dimension schema 中定义，读取方 **MUST NOT** 自行猜测语义

**校验要求：**

- `slice` 的每个 key **MUST** 在 [`dimension-schema-contract.zh.md`](./dimension-schema-contract.zh.md) 中对应到已发布的 `dimension.{dimension_key}`
- compiler **MUST** 在 dimension compatibility gate 中验证这些 key 是否都能归一化为存在的 `dimension.*`
- evidence registration / write path **MUST** 拒绝写入无法映射到已定义 dimension 的 `slice` key
- 若读取方遇到未知 key，**MUST** 返回显式的 derivation/validation failure，而不是静默忽略或猜测语义

#### `step_ref` → `dataset.*` / 其他 semantic refs

`step_ref` 不是 semantic identity 本身，而是到 step metadata 的稳定入口。读取方可按如下路径推导：

1. 使用 `step_ref = { session_id, step_id, step_type }` 定位 step metadata
2. 读取 step 关联的 typed inputs / normalized input snapshots
3. 从 `dataset_refs` 找到 Dataset grounding，从 `filter_context` 找到过滤语义上下文
4. 最终推导 `metric_ref`、`dataset.*`、`dimension.*`、`time.*`

典型路径：

- `step_ref -> typed_inputs.metric_ref_snapshot -> metric_ref`
- `step_ref -> filter_context -> dataset.* | dimension.*`
- `step_ref -> dataset_refs -> dataset grounding -> dimension.* | time.*`

Dataset grounding 与编译期 resolved snapshots 的 ref-only 约束分别见：

- ~~[`typed-binding-contract.zh.md`](./typed-binding-contract.zh.md)~~（已废弃：SUPERSEDED，物理接地由 Dataset/Field 行内表达）
- [`ir-schema-contract.zh.md`](./ir-schema-contract.zh.md)

#### `seed_finding_refs` 结构

`seed_finding_refs` 是 `PropositionSeedRef[]`，每个元素结构为：
```ts
type PropositionSeedRef = {
  finding_ref: FindingRef;
  role: "primary" | "secondary" | "context";
};
```

详见 `proposition.md` 中的 `PropositionSeedRef` 定义。

#### `lineage.source_artifact_lineages` / `lineage.source_step_refs`

`source_artifact_lineages` 是 `ArtifactLineageRef[]`：
```ts
type ArtifactLineageRef = {
  artifact_id: string;
  artifact_schema_version: string | null;
  extractor_version: string | null;
};
```

`source_step_refs` 是 `StepRef[]`：
```ts
type StepRef = {
  session_id: string;
  step_id: string;
  step_type: string;
};
```

二者指向 artifact/step lineage，需通过 step binding 或 artifact contract 间接推导 semantic refs。

#### `derived_from_proposition_ref`

`PropositionLineage.derived_from_proposition_ref` 是 `PropositionRef | null`，表达 proposition 间派生关系。

它属于 creation-time seed lineage：
- 不等价于 semantic identity
- 不等价于 runtime judgment dependency
- 读取方 **MUST NOT** 通过 `derived_from` 替代 Dataset-native 语义推导

## FindingRef 与 PropositionRef 的特化关联约束

### FindingRef 特化规则

当 finding 表达某个 measurement 结果时：

- 关联的 `metric_ref` **MUST** 来自 `FindingSubject.metric` 或来源编译输入中的 metric snapshot。
- 若一个 finding 无法从既有字段稳定推导某个 `metric_ref`，读取方 **MUST NOT** 自行猜测。
- Dataset 语义 **MUST** 通过 `step_ref` 对应的 typed inputs、Dataset snapshots、Dataset grounding lineage 推导。
- 若一个 finding 同时依赖多个 Datasets，这种多重关联 **MUST** 保留在 lineage 语义中，不得压扁成单个伪主键。

### PropositionRef 特化规则

- `PropositionRef` **MUST NOT** 被解释为 `metric_ref` 或 `dataset.*` 的别名。
- proposition 的 semantic meaning **MUST** 继承或聚合自其 seed findings、lineage、payload subtype。
- 若 proposition 对多个 semantic refs 建立关系，这种多对多关系 **MUST** 保持显式可审计。
- system-seeded proposition **MUST** 复用其 seed/lineage 中已存在的 semantic anchors。
- assessment runtime membership（support / oppose / gaps / records）**MUST NOT** 改写 proposition 的 creation-time semantic anchors。

### 约束执行机制

上述约束不是文档性建议，而是集成 contract 的执行要求：

1. **Compiler**
   - 负责 typed input resolution、dimension normalization、Dataset grounding compatibility 与 metric 兼容校验
   - 若无法从 canonical fields、typed inputs、Dataset grounding 稳定推导 semantic refs，**MUST** 在编译阶段失败
2. **Evidence registration / write path**
   - 负责校验 subject、seed refs、lineage refs 的结构完整性与可解析性
   - 若写入对象包含无法映射的 anchor，**MUST** 拒绝注册，不得静默降级
3. **Read surfaces / consumers**
   - 负责基于既有 canonical fields 解释 semantic meaning
   - 若推导失败，**MUST** 暴露显式 failure/diagnostic，而不是自行补全伪 semantic refs

## Canonical Artifact Refs 与 Semantic Refs 的映射关系

### 基本分工

canonical artifact refs 回答：
- evidence 从哪个 artifact 来
- 若需要继续向上追溯，应从哪个 artifact handle 开始

semantic refs 回答：
- artifact / finding / proposition 关心的治理语义对象是什么
- 这些语义对象之间是否兼容、可比较、可组合

因此：
- `artifact_ref -> semantic_ref` 是 **可映射关系**
- `semantic_ref -> artifact_ref` 不是一一映射，也不是 identity 反查键

### 允许的映射方向

允许的规范路径：

1. `ArtifactRef / ArtifactItemRefRef -> source step / lineage -> typed input snapshots / binding refs -> semantic refs`
2. `FindingRef -> artifact_id / step_ref / subject / payload typed refs -> semantic refs`
3. `PropositionRef -> seed_finding_refs / source_artifact_lineages / source_step_refs / payload typed refs -> semantic refs`

### 禁止的映射方式

以下做法 **MUST NOT** 作为规范映射：

- 仅凭 `artifact_id` 命名约定推断 `metric_ref` 或 `dataset.*`
- 仅凭 projection 行键、排序位置、UI summary、LLM explanation 反推 semantic refs
- 把 `ArtifactRef` 当作读取面的 semantic summary
- 把 semantic refs 直接回写成 canonical ref 字段
- 把 canonical artifact refs 当作 compiler/IR 中的稳定 semantic object id

### 多对多关系

映射默认是多对多：

- 一个 artifact 可能承载多个 semantic refs（如 `metric_ref` + 多个 `dimension.*` + `time.*` + `dataset.*`）
- 同一个 `metric_ref` 可能在多个 session、多个 artifact、多个 proposition 中重复 materialize
- 同一个 `dataset.*` 也可能跨多个 findings / propositions 复用

因此：
- canonical artifact refs **MUST NOT** 被规范化为 semantic refs 的实例 id
- semantic refs **MUST NOT** 被视为某个 artifact lineage 的唯一反查键

## Read Surfaces、Dataset Grounding、Compiler、IR 的职责

### Read Surfaces

- state/context/focus 等读取面的 `artifact_refs` **MUST** 只返回最小 provenance handles。
- read surfaces **MUST NOT** 为了方便消费而把 `artifact_refs` 膨胀成 semantic contract payload。
- 若读取面需要暴露 semantic meaning，**MUST** 基于既有 canonical fields 与 semantic contracts 解释，而不是改写 canonical refs。

### Dataset Grounding

> **注意**：原 "Typed Binding" 层已删除，替换为 Dataset-native grounding。物理接地由 Dataset/Field 行内表达，不再通过独立的 binding 对象。

- Dataset/Field 中的语义属性 **MUST** 继续表达字段服务的语义目标。
- Dataset grounding **MUST NOT** 把语义引用降级为 artifact-local 字段 locator。
- Dataset grounding 负责说明 "这个 Dataset/Field 服务哪个 semantic target"，不负责重新发明 canonical evidence id。

### Compiler / IR

- compiler **MUST** 把 semantic refs 解析为 typed input snapshots、Dataset refs、resolved semantic fields。
- IR **MUST** 继续"使用引用而非复制"：保留 `metric_ref` / `dataset.*` / `field.*` 等语义快照。
- compiler/IR **MUST NOT** 把 canonical refs 当作 semantic schema 的替代主键。

## 示例

### 1. Metric observation finding

一个 observation finding 围绕 `metric.orders_gmv` 展开，subject 中已经锚定 metric 与 slice：

```json
{
  "finding_id": "finding_01",
  "finding_type": "observation",
  "subject": {
    "metric": "orders_gmv",
    "slice": { "country": "US" }
  },
  "observed_window": { "kind": "range", "start": "2024-03-01", "end": "2024-03-08" }
}
```

**推导路径**：
- `FindingRef` = `{ session_id: "sess_01", finding_id: "finding_01" }` — 由 session_id + finding_id 直接构造
- `metric_ref` = `metric.orders_gmv` — 由 `subject.metric = "orders_gmv"` 添加 `metric.` 前缀推导
- `dimension.country` — 由 `subject.slice` 中 `"country": "US"` 映射到已定义的 `dimension.country` schema
- `time.order_created_at` — 由 `observed_window` 结合 Dataset grounding（时间语义由 AOI `time_scope.field` 解析）推导

**边界强调**：
- `FindingRef` 不能替代 `metric.orders_gmv`
- `metric.orders_gmv` 也不能替代 `FindingRef`

### 1.1. Observation finding without metric anchor

当 finding 不提供 metric 语义锚点时：

```json
{
  "finding_id": "finding_02",
  "finding_type": "observation",
  "subject": {
    "metric": null,
    "entity": "user",
    "slice": { "cohort": "new_users" }
  }
}
```

**推导路径**：
- `FindingRef` = `{ session_id: "sess_01", finding_id: "finding_02" }`
- `metric_ref` = `null` — `subject.metric` 为空，不能推导为任何 `metric.*`
- `dataset.user` — 由 `subject.entity` 推导
- `dimension.cohort` — 若 `cohort` 已在 dimension schema 中定义，则可由 `subject.slice` 推导

**边界强调**：
- 读取方 **MUST NOT** 从 artifact 名称、payload 文本或上游 UI summary 猜测 `metric_ref`
- 此类 finding 可以承载 entity / dimension 语义，但不自动承载 measurement metric 语义

### 2. Dataset-backed experiment proposition

一个 proposition 由实验上下文驱动，claim 围绕 `metric.conversion_rate` 展开：

```json
{
  "proposition_id": "prop_01",
  "proposition_type": "change",
  "subject": { "metric": "conversion_rate" },
  "origin": { "kind": "system_seeded" },
  "lineage": {
    "source_step_refs": [{ "step_id": "step_cmp_01", "step_type": "compare" }]
  }
}
```

**推导路径**：
- `PropositionRef` = `{ session_id: "sess_01", proposition_id: "prop_01" }`
- `metric_ref` = `metric.conversion_rate` — 由 `subject.metric` 推导
- `dataset.*` 语义 — 由 `lineage.source_step_refs` 中的 step Dataset grounding 推导
- `dimension.variant` — 由 seed findings 的 `subject.slice` 或 Dataset grounding 推导

**边界强调**：
- `derived_from_proposition_ref` 不能替代 Dataset-native 语义推导
- Dataset-level 语义也不能替代 `PropositionRef`

### 3. Seeded proposition lineage with upstream artifact refs

一个 decomposition proposition 通过 seed finding 与上游 decompose artifact 建立 lineage：

```json
{
  "proposition_id": "prop_decomp_01",
  "proposition_type": "decomposition",
  "seed_finding_refs": [
    { "finding_ref": { "finding_id": "finding_decomp_item_01" }, "role": "primary" }
  ],
  "lineage": {
    "source_artifact_lineages": [{ "artifact_id": "art_decomp_01" }]
  }
}
```

**推导路径**：
- payload 中的 `FindingRef` / `ArtifactItemRefRef` 只负责回指上游 canonical evidence
- 相关 `metric_ref` / `dimension.*` 需通过 seed finding 的 `subject`、source step lineage 推导
- 读取面返回的 `artifact_refs` 只提供 provenance 入口，不提供新的 semantic authority

## 一致性规则汇总

1. **canonical refs 与 semantic refs 不得互相替代** — 两者回答不同问题，允许建立关联映射但禁止 identity 折叠。
2. **映射只能从 canonical 字段推导** — 禁止新增统一映射槽位或凭命名约定、自由文本、projection 反推。
3. **assessment runtime membership 不得覆盖 creation-time seed semantics** — 支持反驳集合是实时状态，seed anchors 是创建时谱系。
4. **读取层/Dataset grounding/compiler/IR 必须遵守各自边界** — provenance、semantic identity、runtime membership 不得混为一谈。

## 交叉引用

| 文档 | 核心内容 |
| --- | --- |
| [`graph-and-reference-semantics.md`](../analysis/evidence-engine/graph-and-reference-semantics.md) | canonical refs 的 hard/soft 分类、dangling 处理、完整性规则 |
| [`finding.md`](../analysis/evidence-engine/schemas/finding.md) | finding schema、`FindingRef`、`ArtifactItemRefRef` 类型定义、`FindingProvenance` 结构 |
| [`proposition.md`](../analysis/evidence-engine/schemas/proposition.md) | proposition schema、`PropositionSeedRef` 结构、`PropositionLineage` 定义 |
| [`read-surfaces.md`](../analysis/evidence-engine/read-surfaces.md) | state/context surface 中 `artifact_refs` 的边界 |
| [`metric-process-contract.zh.md`](./metric-process-contract.zh.md) | `metric` 的语义分工与 stable refs 定义 |
| [`dimension-schema-contract.zh.md`](./dimension-schema-contract.zh.md) | `dimension.*` 的定义边界、归一化与治理要求 |
| ~~[`typed-binding-contract.zh.md`](./typed-binding-contract.zh.md)~~ | ~~binding 如何承载 refs 的物理落地~~（已废弃：SUPERSEDED，物理接地由 Dataset/Field 行内表达） |
| [`compiler-spec.zh.md`](./compiler-spec.zh.md) | compiler 如何解析 typed refs、normalize、validation、IR assembly |
| [`ir-schema-contract.zh.md`](./ir-schema-contract.zh.md) | IR 如何使用引用而非复制，与 compiler/lowering 的分层 |
