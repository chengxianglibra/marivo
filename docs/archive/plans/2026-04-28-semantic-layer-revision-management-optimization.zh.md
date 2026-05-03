# Marivo Semantic Layer Revision Management 总体优化方案

## 背景与问题

Marivo semantic layer 需要同时满足两类目标：一方面，已发布语义对象必须可审计、可回溯，历史 artifact 不能因为后续维护而改变解释；另一方面，agent 需要能低成本维护 semantic layer 对象，不能因为一次描述、单位标注或轻量结构修正就被迫 `deprecate + create *_v2 + 手动迁移 binding/profile`。

现有 metric revision v1 已经把 `metric.*` 从单行不可变对象推进到 stable ref 下的多 revision 模型：`metric_ref` 表示稳定业务 identity，`revision` 表示同一 identity 的冻结定义版本，默认 resolver 读取 latest active revision，历史读取可以显式指定 revision。这个方向是正确的，但当前设计仍要求 agent 在 create revision 时输入 `compatibility = compatible | breaking`。当 server 已经有能力读取 base revision、新 revision、依赖图和 readiness 结果时，让 agent 先判断兼容性会增加不必要的认知负担，也容易造成“agent 声明”和“实际 diff”不一致。

binding、compiler profile 以及 entity / dimension / time / process 等 semantic family 也存在类似维护问题。binding 尤其明显：metric 发生修订后，agent 往往需要复制 `carrier_bindings`、`field_bindings`、`time_bindings`、`imports` 等完整 grounding JSON；profile 也会因为 subject revision 或 metric revision drift 进入 stale，但缺少统一的 dependency action plan 告诉 agent 哪些可复用、哪些需要重验、哪些必须补建。

本方案作为现有 `plan/2026-04-28-semantic-layer-metric-binding-revision-todo-task-list.zh.md` 的全量替代目标设计。旧方案中的 metric revision v1 可以作为已落地阶段参考，但目标态应从“agent 声明 compatibility”升级为“server 确定性分类并生成依赖计划”。

## 目标形态

Semantic layer revision management 的统一语义如下：

- `*.ref` 表示 stable semantic identity，是 agent、API、runtime 和用户共同使用的业务语义接口。
- `revision` 表示同一 stable identity 在某个时间点的冻结定义版本。
- object id 只作为服务端内部实例定位符，不作为外部业务语义接口。
- 默认 semantic resolver、catalog search 和 runtime 编译读取 latest active revision。
- artifact / step metadata 必须冻结 resolved ref、resolved object id 和 resolved revision，确保历史证据可以按当时定义回放。
- `deprecate` 表示 stable identity 退出新引用，不释放 ref，也不用于普通修订。
- `create` 只用于创建新的 stable identity；已有 identity 的演进必须走 revision。

目标不是把所有 semantic object family 做成完全相同的浅层 CRUD。统一的是 identity / revision / activation gate / dependency plan 模型；不同 family 仍需要各自的结构化 diff、兼容性判定和 readiness 规则。

## Agent 友好维护设计准则

Semantic layer 的增删改查不能按普通资源 CRUD 暴露给 agent。agent 可以用“创建、查看、修改、删除”的意图发起操作，但 public contract 必须把这些意图规约到 stable identity、revision、dependency action 和 evidence freeze 上。

### 决策协议

agent 维护 semantic layer 对象时，应按以下决策协议工作：

1. `Discover`：先查找 stable identity、latest active revision、readiness、blocking requirements 和 dependents。agent 不能先假设对象不存在，也不能靠命名猜测 `_v2`。
2. `Create Identity`：只有业务语义确实是新的 stable identity 时才创建新 `*.ref`。同义修正、描述补充、单位展示、binding coverage 修补都不属于 create identity。
3. `Revise Identity`：同一 stable identity 的定义演进必须创建 revision。agent 提交 replacement 和 change summary；server 负责 canonical diff、classification 和 dependency plan。
4. `Derive Dependency`：当 revision 影响 binding / compiler profile 等依赖时，agent 按 server action plan 派生依赖 revision 或触发 revalidate，不复制完整 grounding JSON。
5. `Activate`：只有 validate 证明 required actions、readiness、base revision 和 guardrail 都满足时，revision 才能成为 latest active。activate 是 fail-closed gate，不是 agent 确认按钮。
6. `Deprecate Identity`：只用于 stable identity 退出未来引用。不释放 ref，不用于普通修改，也不作为删除历史证据的手段。
7. `Cleanup Draft`：只能清理从未 active / published 的 draft revision、过期 dependency plan 或未发布草稿；不能清理已进入历史证据链的 active revision。

这个协议的核心边界是：agent 只表达维护意图和受控补充；server 负责确定性分类、依赖计划、可激活性判定和证据记录。

### CRUD 语义映射

面向 agent 可以保留 CRUD 心智模型，但 contract 语义必须重新映射：

- `Create` = 创建新 stable identity。判断标准不是 payload 是否不同，而是是否代表新的可命名业务语义接口。
- `Read` = resolve + inspect + explain。read surface 必须返回 lifecycle、readiness、blocking requirements、dependents、explicit revision 和 artifact / session 中冻结的 resolved snapshot。
- `Update` = revise / derive / revalidate。active object 不允许原地 update；同 identity 本体变化走 revision，依赖 grounding 变化走 derive，readiness / capability drift 走 revalidate。
- `Delete` = deprecate identity 或 cleanup draft。已发布 identity 不做物理 delete；deprecated ref 不释放；历史 artifact 仍按冻结 revision 回放。
- `Batch` = 有顺序的 semantic transaction，不是批量 CRUD。batch / `create_validate_activate` 必须显式产出每个对象的 lifecycle、readiness、classification、required actions 和 activation evidence。

### Contract 级规则

后续 API、runtime、docs 和 tests 都应遵守以下规则：

1. Stable ref 是业务接口；agent、API、runtime、artifact 都围绕 `*.ref` 工作，object id 只定位服务端实例。
2. Revision 是定义事实；同一 stable identity 的每次定义变化必须形成 revision，active revision 不允许原地修改。
3. Create 不承担修改职责；`POST /semantic/{family}` 只创建新 identity，同义修正不得通过 `*_v2`、同义 ref 或 deprecated 后重建绕过 revision。
4. Deprecate 不等于 delete；deprecate 只让 identity 退出未来引用，不释放 ref，不删除历史，不影响 artifact 回放。
5. Agent 不声明 compatibility 事实；agent 可提交 `expected_change_scope` 这类 guardrail，但 `classified_compatibility` 必须由 server canonical diff 产生。
6. Server 必须返回可执行 dependency plan；breaking 不能只返回自然语言说明，必须返回 required actions、completion criteria、action status 和 validation evidence。
7. Dependency 复用优先于重建；binding / profile 受影响时，server 应优先判断 `reuse_as_is`、`reuse_after_revalidate`、`derive_revision`、`add_binding_coverage`，只有确实不适用才要求重新建模。
8. Agent 不复制完整 grounding JSON；binding derive / rebind 应让 agent 提供受控 delta，例如 target revision、coverage additions、reuse sections，完整 payload 由 server 生成并校验。
9. Activate 是 fail-closed gate；base revision stale、required actions 未满足、readiness 未通过、guardrail mismatch 未确认时，都不能成为 latest active。
10. Read 面向决策，不只是取 payload；read surface 必须让 agent 看见 lifecycle、readiness、blocking requirements、dependents、explicit revision 和历史 snapshot。
11. Artifact / session 冻结 resolved snapshot；每次执行必须记录 `ref + object_id + revision`，同一 session 不因 latest active 漂移而静默改变语义。
12. 快捷能力必须等价可审计；batch、`create_validate_activate`、derive-and-activate 等 convenience endpoint 可以存在，但必须产生与分步流程等价的 classification、validation evidence 和 activation record。

### 第一阶段适用范围

第一阶段只把这些准则打透到 `metric.*`、`binding.*`、`compiler_profile.*` 三类对象，形成最小闭环：

- `metric.*`：server canonical diff、classification、revision activation。
- `binding.*`：derive revision、coverage additions、grounding 复用。
- `compiler_profile.*`：revision drift 检测、revalidate action、stale 诊断。

`entity.*`、`dimension.*`、`time.*`、`process.*`、`predicate.*`、`enum.*` 只继承 identity / revision / artifact freeze 原则；在各自 compatibility matrix 明确前，不开放完整同构 revision API，也不复用 metric 的字段规则做推断。

## Agent 工作流

### 创建新对象

当业务语义确实是新的 stable identity 时，agent 继续使用 create 流程：

1. 生成新的稳定 ref，例如 `metric.avg_watch_time`、`entity.user`、`binding.user_events_primary`。
2. 创建 draft semantic object。
3. validate draft。
4. 创建或关联必要的 binding / compiler profile。
5. activate object 及其必要依赖。
6. 后续 runtime 默认解析该 identity 的 revision 1 或 latest active revision。

新对象创建流程相比旧模型的关键变化是：create 不再承担“修改已有对象”的职责。若 ref 语义不变，即使 payload 需要变化，也不应创建 `metric.xxx_v2` 或同义的新 ref。

### 维护已有对象

当业务 identity 不变，只是定义演进时，agent 使用 revision 流程：

1. inspect latest active revision，读取当前 payload、readiness 和 dependents。
2. 基于 `base_revision` 创建 revision draft，并提交 `change_summary` 与 full replacement payload。
3. server 同步对 base revision 与新 revision 做 canonical semantic diff，返回 compatibility classification 和 dependency plan。
4. agent 执行 validate；validate 负责重新计算或确认分类结果、依赖计划和当前 readiness 仍一致。
5. 如果 `can_activate_now = true` 且 validate 通过，agent 执行 activate。
6. 如果 `can_activate_now = false`，agent 按 `required_actions` 补齐缺失覆盖、派生 binding revision 或触发 profile revalidate。
7. 所有 blocking required actions 通过再次 validate 证明已完成后，再 activate 新 revision。

agent 不再负责判断 `compatible` 还是 `breaking`。请求中最多允许提供非权威 guardrail 字段，例如 `expected_compatibility` 或 `expected_change_scope`。这些字段只用于防误操作：server 先基于请求 payload 做分类；如果分类结果超出 agent 预期，应返回结构化 `409 RevisionGuardrailMismatch`，响应体仍包含 server 分类和 dependency plan 预览，但不持久化 draft revision。agent 可以带 `accept_classified_compatibility=true` 重试创建。该确认只表示接受 server 分类，不会绕过 validate、required actions 或 activate gate。

本方案第一阶段不定义通用 patch 语义。revision create 使用 full replacement，避免 `null`、absent、字段删除和 base_revision 冲突语义不清。binding derive 可以使用受控 delta，例如 `coverage_additions`、`target_metric_revision`、`reuse_sections`，但这些 delta 由对应 endpoint 转换成完整 revision payload 并由 server 校验，不对外暴露任意 JSON patch。

revision 不引入新的状态机。持久化状态继续只使用现有 `draft`、`published`、`deprecated`：

- `draft`：尚未发布的 revision，可以 validate / activate / cleanup。
- `published`：已发布的 revision；其中 `is_latest_active = 1` 的一条是默认解析版本，`is_latest_active = 0` 的旧 revision 仅用于历史回放。
- `deprecated`：stable identity 已退出新引用，ref 不释放。

public API 不再暴露 storage `status`。agent 和外部调用方只消费 `lifecycle_status = draft | active | deprecated`、`readiness_status = not_ready | ready | stale`、`blocking_requirements` 和 revision 相关字段。服务端内部把 storage `published` 映射为 public `active`；`status` 只保留在 metadata DB 和服务端实现里，不作为 public contract 字段、过滤字段或 agent 决策依据。

其他概念都不应建模为状态：`classified_compatibility` 是 server diff 结果；`required_actions` 和 readiness blockers 决定能否 activate；`can_activate_now` 是即时派生布尔值。这样实现只需要维护三种存储状态、一个 latest selector 和一个 activate gate。

`can_activate_now` 不是跳过 validate 的许可。create 时返回的 `can_activate_now` 代表“按当前快照看是否可能直接进入 validate/activate”；validate 时必须重新计算；activate 时必须再次检查 revision 仍是 draft、base revision、required actions、readiness 和 guardrail 确认。

## Server 自动分类与依赖计划

revision create / validate 阶段，server 必须基于 base revision 和新 revision 做确定性分析，并返回结构化结果：

- `classified_compatibility`: `compatible` 或 `breaking`
- `diff_summary`: 变更字段、变更类型、分类原因
- `affected_dependents`: 受影响的 binding、profile、下游 semantic object 或 runtime surface
- `required_actions`: agent 或服务端后续必须完成的动作
- `can_activate_now`: 当前 revision 是否可以直接进入 validate / activate 链路

diff 必须是 family-specific canonical semantic diff，而不是原始 JSON diff。server 在分类前应完成 schema normalization、默认值展开、数组/集合顺序归一化、ref 规范化和 dependency extraction。不同 family 可以有不同 canonicalizer；实现上必须保证“等价 payload 不产生误报 breaking diff”，也必须保证“结构相似但语义变化的 payload 不被漏判”。

第一阶段先把 `metric.*` 的 canonical diff 做实，binding / compiler profile 暂时只实现服务端结构化比较、derive / revalidate plan 和 readiness drift 检测，不要求一次性具备完整 field-level compatibility classifier。metric canonical diff 的最低规格如下：

| Diff class | 字段范围 | Classification |
| --- | --- | --- |
| display metadata | `display_name`、`description`、`owner`、`tags`、示例、展示 label | `compatible` |
| unit display metadata | 仅改变展示 label、说明文本，且不改变单位语义和换算规则 | `compatible` |
| metric identity / family | `metric_ref`、`metric_family`、`value_semantics`、payload family discriminator | `breaking` 或拒绝 create revision |
| required input contract | required input slot 新增、删除、重命名、required/optional 变化 | `breaking` |
| observed entity / process context | `observed_entity_ref`、population subject、process anchor 改变 | `breaking` |
| grain / time axis | primary time、grain、calendar alignment、timezone / timestamp semantics 改变 | `breaking` |
| predicate / additivity contract | governed predicate、row filter、additivity constraints 改变 | `breaking`，除非 family matrix 明确证明等价 |
| equivalent normalization | 数组排序变化、默认值省略、ref 大小写/前缀规范化后等价 | 无实质 diff，不应产生 breaking |

canonicalizer 必须输出规范化后的 semantic payload，再进行 diff；`diff_summary.path` 应指向 canonical path，而不是请求原始 JSON path。

compatible 变更表示不影响 runtime 编译、binding coverage、查询结果语义或依赖对象契约。典型场景包括 display name、description、owner、tags、单位展示文案、示例说明等。compatible revision 激活后，现有 binding / profile readiness 可以复用，不应被统一标记为 stale。

breaking 变更表示不能无条件继承旧 revision 的依赖 ready 结论。典型场景包括 metric family、required input slot、observed entity、primary time、grain、time axis policy、entity identity key、dimension value domain、binding carrier、import 关系或 compiler capability 约束变化。

breaking 不等于全部重建。server 应逐个依赖给出复用策略：

| Action | 含义 | agent 行为 |
| --- | --- | --- |
| `reuse_as_is` | 依赖仍完全满足新 revision | 不需要处理 |
| `reuse_after_revalidate` | 结构可复用，但 ready 结论需要重验 | 触发 validate / profile revalidate |
| `derive_revision` | 复用旧对象大部分 payload，派生一个新 revision | 创建派生 revision，只修改必要字段 |
| `add_binding_coverage` | 新 revision 引入旧 binding 没有覆盖的 target | 在派生 binding revision 中补充 field/time/import coverage |
| `create_missing_dependency` | 新 revision 需要一个当前不存在的 public dependency | 创建缺失的 semantic object 或 profile |
| `incompatible` | 旧依赖语义不再适用 | 需要 agent 重新建模或人工决策 |

breaking revision 在 blocking `required_actions` 未完成前不得 activate。当前目标态选择 fail-closed：server 不提供普通 agent 的“显式确认后带 blocker 激活为 latest active”旁路。真实运营里的紧急修正可以在后续独立设计 operator override，但必须满足三条约束：只有 operator 角色可用；写入审计记录和 override reason；runtime/resolver 对未 ready 的 latest active 仍 fail-closed，不允许静默执行不完整语义。本方案不把 override 作为第一阶段实现范围。

每个 required action 必须是可验证状态，而不是纯提示文本。最小结构包括：

- `action_id`：在同一次 revision plan 内稳定。
- `action`：稳定动作码，例如 `derive_revision`、`add_binding_coverage`、`reuse_after_revalidate`。
- `target_ref` / `target_revision`：动作作用对象或目标 revision。
- `depends_on`：同一 plan 内必须先满足的 `action_id` 列表；为空表示可并行执行。
- `blocking`：是否阻塞当前 revision activate。
- `action_status`：`pending`、`satisfied`、`failed`；第一阶段不支持 `waived` 作为可激活状态。
- `completion_criteria`：机器可判定 predicate，而不是自然语言。
- `validation_evidence`：最近一次 validate 产生的证据摘要。

`completion_criteria` 的最低结构应包含 `kind`、`expected` 和 `observed`。例如 `kind=derived_binding_revision_validates`，`expected` 包含 source binding ref、metric ref、metric revision、coverage target，`observed` 由 validate 填入实际派生 revision 和 validator result。自然语言 `reason` 只用于解释，不参与 activate 判定。

required action status 不由 agent 手动改写。agent 通过执行对应 API（例如 derive binding revision、补 coverage、profile revalidate）改变底层对象；随后调用 validate，server 重新计算 action predicate 并把 `action_status` 标记为 `satisfied` 或 `failed`。`failed` 表示 server 能确定动作已尝试但未满足 criteria，例如派生 revision 存在但 coverage 仍缺失；agent 应按 `validation_evidence` 修正后再次 validate。

activate 必须重新读取 required actions 并确认所有 blocking action 已满足，不能只信任 create revision 时返回的计划。

示例 compatible response：

```json
{
  "ref": "metric.avg_blocked_time",
  "revision": 2,
  "base_revision": 1,
  "classified_compatibility": "compatible",
  "diff_summary": [
    {
      "path": "payload.unit.display_label",
      "change_type": "display_metadata",
      "compatibility": "compatible",
      "reason": "Display-only unit label change does not alter runtime compilation."
    }
  ],
  "affected_dependents": [],
  "required_actions": [],
  "can_activate_now": true
}
```

示例 breaking response：

```json
{
  "ref": "metric.avg_blocked_time",
  "revision": 3,
  "base_revision": 2,
  "classified_compatibility": "breaking",
  "diff_summary": [
    {
      "path": "payload.required_inputs",
      "change_type": "metric_input_contract_changed",
      "compatibility": "breaking",
      "reason": "A new required metric input must be covered by a binding."
    }
  ],
  "affected_dependents": [
    {
      "ref": "binding.avg_blocked_time_primary",
      "current_revision": 1,
      "recommended_action": "derive_revision"
    },
    {
      "ref": "compiler_profile.avg_blocked_time_duckdb",
      "current_revision": 1,
      "recommended_action": "reuse_after_revalidate"
    }
  ],
  "required_actions": [
    {
      "action_id": "act_binding_avg_blocked_time_primary",
      "action": "derive_revision",
      "target_ref": "binding.avg_blocked_time_primary",
      "target_revision": null,
      "depends_on": [],
      "blocking": true,
      "action_status": "pending",
      "completion_criteria": {
        "kind": "derived_binding_revision_validates",
        "expected": {
          "source_binding_ref": "binding.avg_blocked_time_primary",
          "metric_ref": "metric.avg_blocked_time",
          "metric_revision": 3
        },
        "observed": null
      },
      "reason": "Existing carrier/time/import grounding can be reused, but metric input coverage must be updated."
    },
    {
      "action_id": "act_metric_input_denominator",
      "action": "add_binding_coverage",
      "target_ref": "binding.avg_blocked_time_primary",
      "target_revision": null,
      "coverage_target": "metric_input.denominator",
      "depends_on": ["act_binding_avg_blocked_time_primary"],
      "blocking": true,
      "action_status": "pending",
      "completion_criteria": {
        "kind": "binding_revision_covers_metric_input",
        "expected": {
          "metric_ref": "metric.avg_blocked_time",
          "metric_revision": 3,
          "coverage_target": "metric_input.denominator",
          "binding_revision_source": "act_binding_avg_blocked_time_primary"
        },
        "observed": null
      },
      "reason": "New required metric input is not covered by the current binding revision."
    }
  ],
  "can_activate_now": false
}
```

binding rebind / derive revision 应默认复用 carrier、time、import 和已满足的 field coverage，只补缺失项或变化项。`metric_input.*` 是 metric contract 内部 target ref，不是 public object family；补齐它意味着在 binding revision 中增加 coverage，而不是创建一个独立 `metric_input` 对象。agent 不应把完整 grounding JSON 搬回客户端做无差别复制。

`affected_dependents` 的发现方式需要显式落在 server 侧。第一阶段可以采用 deterministic scan：按 family 查询 binding / compiler profile / readiness context 中引用了目标 `ref + revision` 或 stable ref 的对象，并生成一次性 dependency plan。随着规模增长，可以再引入反向依赖索引或 materialized dependency table，但这属于性能优化，不改变 public contract。API 返回应说明 dependency graph 的计算时间点，例如 `dependency_plan_created_at` 和 `dependency_plan_base_revision`。

## 并发与激活规则

`base_revision` 是 revision 维护的乐观并发边界。create revision 时，server 必须确认 `base_revision` 存在；activate 时，server 必须再次确认该 draft 的 `base_revision` 仍然是当前 latest active revision。若已有另一个 revision 在此期间激活，当前 draft 必须被拒绝为 stale draft，并要求 agent 基于新的 latest active revision 重新创建 revision。

本方案不支持基于历史 revision 的 silent fork，也不允许后激活的 stale draft 覆盖更新的 active revision。若未来需要支持 fork，必须作为独立能力设计，显式区分 forked identity、forked revision lineage 和默认 resolver 选择规则。

长期未激活的 draft revision 不参与默认解析，也不阻塞其他基于同一 latest active revision 的 draft 创建；并发正确性以 activate 时的 `base_revision` 复检为准。server 应提供 draft 清理能力，例如 `DELETE /semantic/{family}/{ref}/revisions/{revision}` 或后台 TTL cleanup，仅允许删除从未 published 的 draft revision。过期 draft 被清理后，相关 dependency plan 和 required action evidence 也应一并失效。

## 对象分层接入范围

### 第一优先级：必须打透

- `metric.*`：作为当前已落地 revision v1 的升级对象，移除 agent 必填 `compatibility`，改为 server classified result。
- `binding.*`：支持 revision / rebind / derive revision，解决 grounding JSON 复制和 metric revision 后依赖迁移成本。
- `compiler_profile.*`：记录 subject / metric / binding revision 绑定关系，支持 revision drift 后的 `reuse_after_revalidate` 和 stale 诊断。

这三类对象构成最小闭环：metric 定义演进、binding grounding 复用、compiler capability 重新认证。

### 第二优先级：只定义扩展边界

`entity.*`、`dimension.*`、`time.*`、`process.*` 可以采用同一 identity / revision / artifact freeze 模型，但本方案不在总体文档中展开逐字段 compatibility matrix。进入实施前，每个 family 必须单独定义：

- 参与 canonical diff 的字段集合。
- normalization 规则。
- compatible / breaking / revalidate 的判定表。
- 反向依赖发现方式和 required action 类型。

在这些 matrix 未定义前，第二优先级 family 不应开放完整 revision API，也不应复用 metric 的字段规则做推断。

### 第三优先级：只沉淀原则

- `predicate.*`：命名治理谓词可以版本化；request-level 临时 filter 不应变成治理对象 revision。
- `enum.*`：可以作为 dimension value domain 的从属 revision 或独立 revision，但需要先决定新增、删除、重命名枚举值的兼容性语义。
- system-managed policy / catalog：可以有内部版本和 artifact freeze，但不一定开放 public write revision API。

`key.*`、`grain.*`、`measure.*`、`metric_input.*` 仍是 contract 内部稳定 ref，不作为独立 public object family。agent 应在 typed payload 内生成和引用这些 ref，而不是调用 `/semantic/keys`、`/semantic/grains` 之类不存在的 public CRUD。

## Public API / Interface 方向

这是目标设计，不要求一次性实现所有接口。公共 API 的方向如下：

1. revision create 请求移除必填 `compatibility`。
2. 可选保留 `expected_compatibility` 或 `expected_change_scope`，仅用于 guardrail，不作为事实来源。
3. revision create / validate 返回 server classified result。
4. activate 根据 `can_activate_now` 和 `required_actions` 决定是否允许推进。
5. binding 增加 derive / rebind 能力，支持只提交受控变化点或缺失 target。
6. required action 不提供独立“手动改状态”接口；状态由 validate 基于底层对象和机器 criteria 重新计算。

第一阶段接口职责边界：

| API | 职责 | 不负责 |
| --- | --- | --- |
| `POST /semantic/metrics/{ref}/revisions` | 创建 draft revision，执行同步 classification，生成 dependency plan | 不激活，不跳过 guardrail |
| `POST /semantic/metrics/{ref}/revisions/{revision}/validate` | 重新计算 diff / dependency plan / readiness / action status，返回最新 `can_activate_now` | 不把 public `lifecycle_status` 推进到 `active` |
| `POST /semantic/metrics/{ref}/revisions/{revision}/activate` | 原子复检并激活 draft revision | 不重新解释 agent compatibility claim |
| `POST /semantic/bindings/{ref}/revisions/derive` | 基于 required action 派生 binding revision，复用旧 grounding 并应用受控 delta | 不接受任意 JSON patch |
| `POST /compiler/compatibility-profiles/{ref}/revalidate` | 针对指定 subject / metric / binding revision 重验 profile | 不创建新的语义事实 |

binding derive request 的目标形态是“指向 plan action + 提供受控补充”，而不是提交完整 grounding JSON：

```json
{
  "base_revision": 1,
  "source_action_id": "act_binding_avg_blocked_time_primary",
  "target_metric_ref": "metric.avg_blocked_time",
  "target_metric_revision": 3,
  "reuse_sections": ["carrier", "time", "imports", "satisfied_field_coverage"],
  "coverage_additions": [
    {
      "coverage_target": "metric_input.denominator",
      "field_ref": "field.blocked_time_denominator"
    }
  ]
}
```

server 根据 base binding revision 生成完整 derived binding payload，并在 validate 时证明该 derived revision 覆盖目标 metric revision。

推荐的 revision create payload：

```json
{
  "base_revision": 1,
  "change_summary": "Fix display label for blocked time unit.",
  "expected_change_scope": "display_metadata",
  "replacement": {
    "header": {},
    "payload": {}
  }
}
```

推荐的 create response：

```json
{
  "ref": "metric.avg_blocked_time",
  "revision": 2,
  "base_revision": 1,
  "classified_compatibility": "compatible",
  "diff_summary": [],
  "affected_dependents": [],
  "required_actions": [],
  "can_activate_now": true
}
```

推荐的 dependency action response：

```json
{
  "classified_compatibility": "breaking",
  "required_actions": [
    {
      "action_id": "act_binding_avg_blocked_time_primary",
      "action": "derive_revision",
      "target_ref": "binding.avg_blocked_time_primary",
      "depends_on": [],
      "blocking": true,
      "action_status": "pending",
      "completion_criteria": {
        "kind": "derived_binding_revision_validates",
        "expected": {
          "source_binding_ref": "binding.avg_blocked_time_primary",
          "metric_ref": "metric.avg_blocked_time",
          "metric_revision": 3
        },
        "observed": null
      },
      "reason": "Metric input coverage must be revalidated."
    },
    {
      "action_id": "act_metric_input_denominator",
      "action": "add_binding_coverage",
      "target_ref": "binding.avg_blocked_time_primary",
      "coverage_target": "metric_input.denominator",
      "depends_on": ["act_binding_avg_blocked_time_primary"],
      "blocking": true,
      "action_status": "pending",
      "completion_criteria": {
        "kind": "binding_revision_covers_metric_input",
        "expected": {
          "metric_ref": "metric.avg_blocked_time",
          "metric_revision": 3,
          "coverage_target": "metric_input.denominator",
          "binding_revision_source": "act_binding_avg_blocked_time_primary"
        },
        "observed": null
      },
      "reason": "New required metric input is not covered by the derived binding revision."
    }
  ],
  "can_activate_now": false
}
```

API 文档需要明确：`POST /semantic/{family}` 创建新 stable identity；`POST /semantic/{family}/{id_or_ref}/revisions` 维护已有 identity；`deprecate` 只用于 identity 退出。

本方案是目标态替代，不保留双 public contract。后续实施时，public revision create request 应删除必填 `compatibility`；如果当前代码或文档中已经存在 v1 `compatibility` 字段，应一次性改为 server 输出字段或内部存储字段。若实现需要短期接受旧字段，只能把它映射为非权威 `expected_compatibility` guardrail，并在同一实施计划中记录删除任务；不得让 agent claim 和 server classification 并存为两个事实来源。

classification 默认同步执行。第一阶段 dependency graph 规模有限，create / validate 应直接返回分类和 dependency plan，避免 agent 处理异步轮询状态。若未来依赖图变大，可新增异步 validate job，但 create response 仍必须明确 `classification_status=pending`，且 pending revision 不能 activate；这属于后续扩展，不改变当前同步目标。

## 影响范围

### API

`docs/api/semantic.md` 中 metric revision v1 的请求说明需要后续修正：`compatibility` 不再是 agent 必填输入，而是 server 输出的 `classified_compatibility`。binding / compiler profile 后续需要新增 revision、derive、rebind 或 revalidate 相关接口。

public semantic object response 应删除或停止文档化 storage `status`，只暴露 `lifecycle_status`。list/filter 也应以 `lifecycle_status` 为 canonical 字段；不再要求 agent 理解 storage `published` 和 public `active` 的映射。若为了短期兼容保留 `status`，必须标记为 deprecated internal compatibility field，并在同一实施计划中列出删除任务。

现有 create 语义需要更明确：同 ref create 应始终返回结构化冲突，不允许覆盖旧 identity，也不允许因为 deprecated 而释放 ref。

### Storage

已存在 storage `status` 和 metric revision 存储字段可以继续使用，但 storage `status` 不再作为 public API contract；服务端负责映射为 `lifecycle_status`。`revision_compatibility` 应被解释为 server classified result，而不是 agent claim。后续其他 family 接入 revision 时，应采用 stable ref + revision 唯一约束，并保留 latest active selector。

本方案不要求复杂在线迁移旧 metadata DB。当前目标仍以 fresh-init 和后续明确迁移计划为边界，但不能忽略历史 agent claim 的风险。若遇到已有 `revision_compatibility` 数据，应按以下策略处理：

- fresh-init：不存在历史 agent claim，直接使用 server classification。
- existing dev metadata：把旧字段标记为 `legacy_agent_claim`，首次 validate/read 时可重新计算并写入 server classified result。
- 无法重算或重算发现旧 `compatible` 实为 `breaking`：不得继续复用旧 ready 结论，应返回 stale / breaking blocker，并要求重新 validate 依赖。
- 不在本方案内承诺在线 backfill、双写或无停机迁移；这些必须进入单独迁移计划。

### Readiness

readiness 需要能表达 revision drift、历史 active revision、deprecated identity、breaking blocker 和 dependency action plan。compatible revision 不应导致下游 binding/profile generic stale；breaking revision 也不应简单地把全部依赖标记为不可用，而应给出逐项处理建议。

### Runtime / Resolver / Artifact

runtime 和 resolver 继续默认读取 latest active revision。历史 artifact、step metadata、typed semantic snapshot 必须冻结 resolved ref、resolved object id 和 resolved revision。新 revision 激活后，新 intent 默认使用新 latest active revision；旧 artifact 继续按记录的 ref + revision 回放，并可用 object id 追溯具体存储实例。

活跃分析 session 必须使用 session 内已经 resolved 的 semantic snapshot 继续追加 step，不能因为中途激活新 revision 而静默漂移。新 session / 新 intent 默认解析最新 active revision；同一 session 如果需要切到新 revision，必须显式 refresh semantic snapshot，并在 step metadata 记录 refresh 前后的 `ref + object_id + revision`。

### Docs / Skill

agent 指南需要从“判断 compatible/breaking 并填写字段”改为“提交 revision，消费 server classified dependency plan”。文档应明确优先 revision、避免 `*_v2`、deprecate 只用于 identity 退出。

Marivo 仍保持 HTTP-only。MCP 只能作为客户端 adapter，不作为 semantic revision 能力来源；文档不应要求 agent 通过本地 DB 或 MCP 私有能力维护 revision。

### Tests

后续实施需要覆盖以下场景：

- compatible 变更自动分类，并允许无依赖动作 activate。
- breaking 变更自动分类，并返回 blocker / required actions。
- breaking 变更复用已有 carrier、time、import 和已满足 field coverage，只补缺失 target。
- raw JSON 顺序变化、默认值省略等等价 payload 不产生误报 breaking diff。
- 过期 draft 在另一个 revision 激活后不能再 activate。
- required actions 未满足时 breaking revision 不能 activate。
- binding derive revision 不要求 agent 提交完整 grounding JSON。
- compiler profile 对 subject / metric / binding revision drift 返回 revalidate action。
- artifact 在 metric revision 激活前后分别冻结旧 resolved ref + revision + object id 与新 resolved ref + revision + object id。
- 同 ref create 对 `lifecycle_status = draft | active | deprecated` 的 identity 均返回结构化 conflict。

## 验收标准

本方案文档的验收标准如下：

1. 能清楚回答 semantic layer revision management 要解决什么问题，以及为什么不再要求 agent 判断 compatibility。
2. 能清楚区分 create new identity、create revision、activate revision、deprecate identity 的边界。
3. 能说明 compatible 与 breaking 的服务端分类方式，以及 breaking 下依赖可复用、不必全部重建的处理原则。
4. 能列出 metric、binding、compiler profile 的第一优先级闭环，以及 entity / dimension / time / process / predicate / enum 的扩展边界。
5. 能说明 storage status 只保留在服务端内部、public API 只暴露 lifecycle_status，以及 latest active selector、activate fail-closed gate、base revision 并发校验和 required action 完成判定。
6. 能说明 API、storage、readiness、runtime、docs/skill、tests 的影响范围。
7. 不把 MCP、本地 DB 操作或旧 metadata 在线迁移作为当前方案的能力前提。
