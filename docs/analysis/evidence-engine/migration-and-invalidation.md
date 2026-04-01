# Evidence Engine Migration And Invalidation

本文档定义 Factum Evidence Engine 的 version migration、mixed-version state 边界，以及 invalidation / redaction / deletion lifecycle。

状态：draft design。本文不替代各对象 schema，也不直接定义存储层迁移脚本；它只固定版本升级与对象失效治理的统一语义，避免运行时和读取面各自发明隐式规则。

## 目的

固定以下问题的统一答案：

- 哪些 version bump 只影响新写入，哪些必须触发 replay / reseed / recompute
- session 内是否允许 mixed-version canonical state，允许到什么边界
- invalidation、redaction、deletion、tombstone 的默认语义分别是什么
- 上游对象失效后，下游应如何暴露影响：reopen gap、membership 收缩、proposal suppression 还是 bundle 回退

## Version Axes

Evidence Engine v1 至少存在以下 version 轴：

- `artifact_schema_version`
- `extractor_version`
- `template_version`
- `derivation_version`
- `assessment_registry_version` / `rule_version`
- `policy_version`

这些 version 轴的责任不同：

- contract version：定义输入/输出 shape
- derivation version：定义 identity boundary
- rule / policy version：定义 judgment 或 proposal 语义

固定要求：

- version 变化必须是显式声明的语义变更，不得通过“实现细节换了”隐式改变结果
- 若变更改变 canonical identity 或 latest bundle 选择语义，必须可审计、可重放、可解释

## Version Bump Classes

### 1. forward-compatible bump

满足以下条件时，视为 forward-compatible：

- 不改变 canonical identity boundary
- 不改变 latest selection / publish semantics
- 不要求旧 session 重放即可保持语义一致

默认行为：

- 只影响新写入
- 旧对象可继续保留
- 不要求 session 进入 migration-blocked

典型例子：

- explanation 文案优化
- 不影响 identity 的模板注释更新
- 不改变 canonical fields 的排序实现细节

### 2. replay-required bump

满足以下条件之一时，视为 replay-required：

- 不改 identity boundary，但改变 canonical output 的 materialization 结果
- 会改变 gap / confidence / proposal ranking 的 deterministic 结果
- 会让新旧结果在同一 session 中形成可见语义漂移

默认行为：

- 允许保留既有历史对象
- 但 session 的 latest/live 结果需要经过定域 replay 才能继续视为最新
- runtime status surface 应显式暴露 `migration_required`

典型例子：

- extractor 同 identity 边界下修正 finding payload 规范化逻辑
- assessment rule registry 升级导致同一 context 的 judgment 结果改变
- proposal policy 调整导致 canonical proposal set 改变

### 3. identity-breaking bump

满足以下条件之一时，视为 identity-breaking：

- 改变 finding / proposition / proposal 的 identity normalization 输入
- 改变 `derivation_version`
- 改变 assessment latest selection 或 graph closure 的 identity anchor

默认行为：

- 必须显式切分新旧 identity 边界
- 不得原地把旧对象“解释成新版本对象”
- 新旧对象可以共存于历史层，但不得在同一 externally visible bundle 中形成未声明的语义混合

典型例子：

- proposition identity normalization 变化
- finding canonical item key 规则变化
- proposal payload semantic fields 变化导致 action identity 改变

## Session Migration Boundary

v1 不要求把 migration marker 塞进 `session` canonical root。

固定基线：

- migration status 属于 runtime / operator truth，而不是 session canonical truth
- session 可以继续存在并保持可读
- 但若当前 latest/live 结果不再满足当前 version policy，runtime status surface 应显式标记：
  - `ready`
  - `migration_required`
  - `migration_in_progress`
  - `migration_blocked`

这避免把 version rollout 状态混入 `AnalysisSession` 根对象。

## Mixed-Version State Rules

### Allowed boundary

以下 mixed-version 组合在 v1 允许存在于历史层：

- 历史 assessment snapshots 来自旧 `rule_version`
- 历史 proposition 来自旧 `template_version`
- 历史 findings 来自旧 `extractor_version`

前提是：

- 它们不会共同组成当前 externally visible latest bundle
- 读取面不会把不同 identity boundary 的对象拼成当前有效 closure

### Forbidden boundary

以下 mixed-version 组合在 v1 禁止进入 externally visible state：

- 新 `latest_assessment` 依赖旧 identity boundary 的 proposition，但该 proposition 在当前 policy 下本应重建
- 新 proposal set 基于新 policy 生成，但对应 latest assessment 仍来自未迁移的旧 rule semantics
- 同一 proposition context 中把已失效的 seed identity 与新 derivation identity 混合作为当前活跃判断基础

原则：

- mixed-version 可以存在于历史真相
- 不能无声明地存在于当前真相

## Invalidation Taxonomy

### 1. invalidation

表示某个上游 canonical object 仍被保留，但当前不应继续作为有效上游 authority。

默认行为：

- 对象保留
- 相关 refs 不静默删除
- 下游通过 missing refs、membership 收缩、gap reopen 或 latest bundle 回退暴露影响

### 2. redaction

表示对象仍有存在痕迹，但部分内容因治理/合规要求被移除或遮蔽。

默认行为：

- 保留最小必要的 typed ref / tombstone 能力
- 不要求继续保留完整 payload
- 读取面应显式暴露“对象存在但内容已 redacted”

### 3. controlled deletion

表示在明确的 compliance / retention 边界下，允许做受控物理删除。

默认行为：

- 不是 v1 默认路径
- 必须先经过 tombstone / invalidation 语义建模
- 删除后若造成 hard ref 不可解引用，读取面不得静默伪装完整性

## Tombstone-First Baseline

v1 默认采用 tombstone-first：

- 先保留对象存在性与最小 lineage/ref 可见性
- 再由下游读取与规则系统显式处理对象缺损
- 最后才在受控场景下考虑物理删除

这样做的目的：

- 避免“像从未发生过一样”抹掉历史
- 保证 replay / audit / debugging 仍有最小锚点
- 让 invalidation 对 latest/live 结果的影响可解释

## Downstream Repair Rules

当上游对象被 invalidated / redacted / deleted 后，下游默认按以下优先级修复：

1. 若只是 lineage/soft ref 缺失：保留对象，显式暴露 missing ref
2. 若影响 current assessment closure：收缩 membership，必要时 reopen gap
3. 若影响 proposal 输入闭包：抑制 proposal refresh 或生成新空 proposal set
4. 若影响当前 latest bundle 的完整性：触发 recompute / republish，必要时回退到上一个仍完整的 externally visible bundle

固定要求：

- 不得静默硬删下游对象来掩盖影响
- 不得把 invalidation 自动解释成“support 变 oppose”之类的 judgment 翻转
- 下游修复必须通过显式 recompute / publish 轨道完成

## Read-Time Exposure Rules

读取面固定遵守：

- canonical state/context 只暴露当前 externally visible truth
- 缺损必须显式暴露，不能静默修补
- operator-facing runtime status surface 负责解释“为什么还没迁移完成 / 为什么当前 bundle 被回退”

这意味着：

- `latest_assessment = null` 仍不足以表达所有迁移或失效原因
- 更细原因必须进入 runtime status surface，而不是塞进 assessment status lattice

## Related Documents

- [`runtime-lifecycle.md`](runtime-lifecycle.md)
- [`runtime-status-surface.md`](runtime-status-surface.md)
- [`runtime-pipeline.md`](runtime-pipeline.md)
- [`graph-and-reference-semantics.md`](graph-and-reference-semantics.md)
- [`schemas/assessment.md`](schemas/assessment.md)
- [`finding-proposition-seeding.md`](finding-proposition-seeding.md)
