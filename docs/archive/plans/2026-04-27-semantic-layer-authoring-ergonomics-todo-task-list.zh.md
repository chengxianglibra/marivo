# Marivo Semantic Layer Authoring Ergonomics Todo Task List

## 背景

本计划针对 agent 在当前 Marivo 服务中为 Trino 同源表构建 semantic layer 对象时暴露的 authoring 摩擦。该工作流需要创建 `time`、`dimension`、`entity`、`metric`、`typed binding` 等多类对象，并在创建后逐个 publish / activate。一次建模包含约 34 次 create 调用和约 34 次 lifecycle 调用；其中 binding 建模又需要重复声明同源 carrier、time surface、time binding 和 metric input 映射。

这类问题不是单个 payload 写错，而是当前 semantic authoring contract 对 agent 不够友好：

- schema 只暴露全局字段枚举，route / scope 级约束主要藏在 service 校验中。
- `imports.required_ref_prefixes` 表达了依赖意图，但没有参与 required target 覆盖合成。
- readiness / capability 能说明对象不可用，但 create 阶段和 detail 阶段没有足够早地暴露缺失目标。
- 文档与 guidance 示例没有覆盖 time surface、time binding、metric input slot、average metric 等高频路径。
- 批量创建和批量 lifecycle 缺位，导致 agent 必须用大量独立调用试错。

本文产出一个可实施计划，使 Marivo 在当前 HTTP-only API 边界内支持更友好的 semantic layer 对象构建。本文只规划 Marivo 服务能力，不假设 MCP 层存在。

## 当前 Root Cause

### 1. Import 只校验依赖存在，不参与 target coverage

当前 `BindingImport.required_ref_prefixes` 会被保存到 `binding_imports.required_ref_prefixes_json`，读取时也会返回到 `interface_contract.imports`。但 create / activate 校验中，required target 覆盖只检查当前 binding 的 `field_bindings + time_bindings`，不会把 imported binding 中满足 prefix 的 target 传播为可用 target。

直接结果是：metric binding 即使 import 了 entity binding 的 `time.*`、`key.*`、`dimension.*`，也无法满足 metric 自身 `primary_time_ref` 的覆盖要求。agent 必须在每个 metric binding 中重复声明同一个 carrier、time surface 和 time binding。

### 2. Scope-specific target kind 约束只在 service 层后置表达

`BindingTarget.target_kind` 使用全局 `TargetKind` 枚举，包含 `identity_key`、`primary_time`、`stable_descriptor`、`population_subject`、`analysis_window_anchor`、`process_context`、`metric_input`。但 `TypedBindingCreateRequest` 没有按 `header.binding_scope` 限定可用 target kind。

service 层随后用 `_validate_binding_scope_compatibility(...)` 拒绝不合法组合，例如 entity binding 不能使用 `metric_input`。这能保护数据正确性，但对 agent 来说属于后置失败：schema、OpenAPI 和示例无法提前告诉调用方“当前 scope 可用哪些 target kind”。

### 3. `metric_input` 命名规则隐式且不一致

service 层要求：

- `target.target_kind = "metric_input"`
- `target.target_key` 使用 metric family slot 名，例如 `measure`、`count_target`、`numerator`、`denominator`
- `semantic_ref` 必须使用 `metric_input.*`

这条规则没有在 schema field description、文档示例和 guidance 中清晰表达。它还容易与 metric ref / target key 混淆：metric 对象是 `metric.*`，但 binding 的 input slot semantic ref 是 `metric_input.*`。

### 4. `TimeSurfaceSpec.physical_name` 是必填，但示例主要展示 field surface

`CarrierBinding.time_surfaces` 使用 `TimeSurfaceSpec`，其中 `physical_name` 是 required 字段。当前 binding 示例主要展示 `field_surfaces.physical_name`，没有展示完整 `time_surfaces` 与 `time_bindings` 配套写法。

同时当前 `time_bindings` 实际引用的是 `field_surfaces` 的 `surface_ref`，而不是 `time_surfaces` 的 `surface_ref`。这会进一步放大困惑：调用方以为 time surface 是 time binding 的目标，但 runtime 校验走的是 field surface。

### 5. `covers_required_targets=false` 缺少 create/read detail 阶段的缺失明细

binding detail 会返回 `capabilities.covers_required_targets`，但该布尔值不携带 `missing_required_targets` 明细。只有当对象进入 active readiness 评估或 metric observe/activate 路径时，调用方才看到类似 `METRIC_BINDING_MISSING`、`METRIC_INPUT_COVERAGE_MISSING` 的 blocking requirements。

这使 create 成功不等于“可继续批量构建”。agent 在下一阶段才知道缺哪些 target，浪费整轮调用。

### 6. Average metric 的双 input slot 模式缺少规范示例

`average_metric` 和 `rate_metric` 都需要 `numerator`、`denominator` 两个 metric input slot。代码层面的 slot 来源是 `app/metric_inputs.py`，但用户文档和 API guidance 没有给出一个 metric binding 中如何声明两个不同 `target_key`、两个 `semantic_ref`、两个物理字段的完整例子。

直接结果是 agent 难以一次性构造出 `metric.avg_elapsed_time` 这类 average metric 的可用 binding。

### 7. service-level ValueError 没有统一 guidance

Pydantic request validation error 会走 guided 422，返回 `schema_url`、`contract_url`、`docs_url` 和 examples。service 层 `_validation_error(...)` 抛出的业务校验错误在 API route 中通常被包装成 plain `HTTPException(detail=str(error))`，或者在 structured lifecycle action 中只返回 `message/code/category`。

因此类似 `metric_input semantic_ref must use 'metric_input.' prefix`、`Entity binding cannot use target kinds` 的错误没有 guidance，和 schema 缺字段类 422 的体验不一致。

### 8. 缺少 batch authoring endpoint / 模板化 authoring 能力

当前对象创建、验证、激活均按单对象 endpoint 执行。对于同源表批量建模，agent 需要重复提交 carrier/time/context 结构，且无法在一个请求中获得跨对象依赖排序、局部失败定位、dry-run 诊断和批量 lifecycle 结果。

这不是性能问题为主，而是 authoring contract 不够批量友好：调用方不得不把 Marivo 内部依赖图和重复模板逻辑搬到 agent 侧。

## 目标

1. 降低同源表多 metric 建模的重复声明成本。
2. 让 schema、OpenAPI、docs、guidance 在 create 前暴露 scope-specific 规则。
3. 让 create / validate 阶段尽早返回缺失 target 明细，而不是等 activate 或 observe 才暴露。
4. 明确 metric input slot 命名和 average metric 双 input 绑定模式。
5. 提供 batch authoring / dry-run 能力，让 agent 能一次提交 semantic object graph，并获得结构化逐项结果。
6. 保持 Marivo HTTP-only；不引入 MCP 假设，不暴露 raw SQL 作为外部契约。

## 非目标

- 不改变 source / engine / mapping 的现有 HTTP 管理边界。
- 不引入 UI 旧实现或 raw SQL workbench。
- 不把 binding contract 变成 SQL DSL。
- 不要求一次实现复杂模板语言；v1 先支持同源表重复结构的最小模板化。
- 不做历史 metadata 兼容迁移方案；如涉及 schema 变更，另行拆分。

## 设计原则

- **Schema 负责前置可发现性**：OpenAPI 能告诉 agent 当前 binding scope 下哪些 target kind 合法，常见错误不应只能靠 service 报错学习。
- **Service 负责权威校验**：即使 schema 提供 discriminated shape，service 仍保留最终 scope compatibility 与 dependency validation。
- **Import 语义要可解释**：要么明确 import 不传播 required target，要么实现有限传播；不能继续让 `required_ref_prefixes` 看起来能满足 `primary_time_ref` 但实际不生效。
- **Readiness 信息前移**：create 成功后应能看到 “下一步为什么还不能 ready / active” 的结构化明细。
- **Batch 是 authoring API，不是事务型 SQL 执行器**：batch endpoint 面向 semantic object graph 创建、validate 和 lifecycle，不承诺任意 SQL 执行。

## Todo Task List

### 1. 固化现状回归用例

- [ ] 增加 entity binding 中误用 `metric_input` 的 API 级测试，断言返回结构化错误，并包含合法 target kind 提示。
- [ ] 增加 `metric_input` 使用 `metric.*` semantic_ref 的 API 级测试，断言返回 guidance。
- [ ] 增加 metric binding import entity binding 的测试，覆盖当前 `required_ref_prefixes=["time."]` 不满足 `primary_time_ref` 的行为，作为改造前基线。
- [ ] 增加 `TimeSurfaceSpec.physical_name` 缺失测试，断言 guided example 覆盖 `time_surfaces` 完整写法。
- [ ] 增加 `average_metric` 缺少 `numerator` / `denominator` 任一 slot 的 readiness / capability 测试。
- [ ] 增加 batch authoring 暂缺能力的 contract TODO 测试或文档验收项，避免后续只修单点错误。

验收：

- `make test` 中 semantic binding 相关测试能稳定复现上述 8 类问题。
- 每个测试名称能对应一个用户侧 authoring 症状。

### 2. 明确 import propagation contract

需要先做一个产品决策：`imports.required_ref_prefixes` 是否应满足 required target coverage。

推荐方案：支持有限传播，但只用于同源、同 subject、显式 prefix 的 target coverage。

- [ ] 在 `docs/semantic/typed-binding-contract.zh.md` 增加 “imported target coverage” 章节。
- [ ] 定义 import 可传播范围：
  - `identity_key`、`primary_time`、`stable_descriptor` 可从 entity binding 传播到 metric binding。
  - `population_subject` 可从 entity / process binding 传播到 metric binding，但必须语义 ref 匹配。
  - `metric_input` 不允许从其他 binding 传播，必须由 metric binding 本身声明。
- [ ] 定义传播条件：
  - imported binding 必须 active / published。
  - imported binding 的 carrier 必须 resolve 到同一个 source object，或通过 explicit relation 声明可 join。
  - `required_ref_prefixes` 必须精确匹配传播 ref 前缀，例如 `time.`、`key.`、`dimension.`。
  - 若多个 import 提供同一 required target，必须返回 ambiguity blocker，不能静默选择。
- [ ] 在 readiness evaluator 和 binding contract validation 中引入 `effective_target_coverage`：
  - `local_targets`
  - `imported_targets`
  - `missing_required_targets`
  - `ambiguous_imported_targets`
- [ ] 对 metric binding 的 `primary_time_ref` 覆盖检查改为使用 effective target coverage。

验收：

- metric binding 可以 import entity binding 的 `time.*` 来满足 `primary_time_ref`，无需重复声明同一 time binding。
- metric binding 仍必须本地声明 `metric_input` slots。
- ambiguous import 和 missing import 都有结构化 blocker。

### 3. 增强 binding schema 的 scope-specific 可发现性

- [ ] 在 OpenAPI schema 中为 `TypedBindingCreateRequest` 增加按 `header.binding_scope` 的 target kind 说明。
- [ ] 评估 Pydantic discriminated union 是否值得引入：
  - `EntityBindingInterfaceContract`
  - `MetricBindingInterfaceContract`
  - `ProcessBindingInterfaceContract`
- [ ] 如果不引入 discriminated union，至少在 `BindingTarget.target_kind`、`TypedBindingCreateRequest` examples 和 API docs 中显式列出合法矩阵。
- [ ] service 层保留 `_validate_binding_scope_compatibility(...)`，但错误 payload 增加：
  - `binding_scope`
  - `allowed_target_kinds`
  - `invalid_target_kinds`
  - `field_path`
- [ ] 为非法 target kind 生成可机器读取错误码，例如 `binding_target_kind_not_allowed_for_scope`。

合法矩阵：

| binding_scope | allowed target_kind |
| --- | --- |
| `entity` | `identity_key`, `primary_time`, `stable_descriptor` |
| `metric` | `population_subject`, `primary_time`, `metric_input` |
| `process_object` | `population_subject`, `primary_time`, `analysis_window_anchor`, `process_context` |

验收：

- agent 只看 schema / docs / guidance 就能知道 entity binding 不能放 `metric_input`。
- API 错误不再只返回字符串。

### 4. 明确 metric input slot contract

- [ ] 在 `docs/semantic/metric-v2-schema.zh.md` 和 `docs/semantic/typed-binding-contract.zh.md` 中增加 metric family 到 required input slots 的表格。
- [ ] 在 `docs/api/semantic.md` 增加 metric binding 示例：
  - `count_metric`: `count_target`
  - `sum_metric`: `measure`
  - `average_metric`: `numerator` + `denominator`
  - `rate_metric`: `numerator` + `denominator`
- [ ] 在 `BindingTarget.target_key` description 中明确：当 `target_kind=metric_input` 时，`target_key` 是 slot name，不是 `metric.*` / `metric_input.*` ref。
- [ ] 在 `FieldBinding.semantic_ref` description 中明确：当 `target_kind=metric_input` 时必须使用 `metric_input.<slot_or_name>`。
- [ ] service 错误增加 remediation hint：
  - `target_key`: `numerator`
  - `semantic_ref`: `metric_input.numerator`
  - `target_kind`: `metric_input`

验收：

- average metric 的 binding 示例能直接用于两列物理字段映射。
- `metric_input` 命名错误返回 guidance，且包含正确 payload 片段。

### 5. 修正 time surface / time binding 文档与 guidance

- [ ] 梳理当前 `time_surfaces` 与 `time_bindings` 的真实关系：
  - 如果 runtime 继续让 `time_bindings` 引用 `field_surfaces`，文档要明确 time surfaces 只是 carrier time metadata。
  - 如果目标是让 `time_bindings` 引用 `time_surfaces`，需要单独设计兼容或 breaking schema 变更。
- [ ] 更新 `TypedBindingCreateRequest` 的 examples，加入完整 `time_surfaces.physical_name`。
- [ ] 更新 guided 422 example，展示：
  - `field_surfaces`
  - `time_surfaces`
  - `time_bindings`
  - `resolution_kind=date_column`
  - `date_surface_ref`
- [ ] 增加 API docs 的 “date column primary time binding” 最小示例。

推荐 v1：不改变 runtime 引用关系，只补齐文档与 guidance；后续如要让 `time_bindings` 消费 `time_surface_ref`，另开设计。

验收：

- `TimeSurfaceSpec.physical_name` 缺失错误的 guidance 能展示完整可修复 payload。
- 文档不再让调用方误以为 time binding 必须引用 `time_surface.*`。

### 6. 在 create / detail 阶段暴露 missing required targets

- [ ] 为 binding readiness capabilities 增加结构化字段：
  - `required_targets`
  - `covered_targets`
  - `missing_required_targets`
  - `imported_covered_targets`
  - `covers_required_targets`
- [ ] 在 draft binding detail 中也计算 coverage preview，不只在 active readiness 中暴露。
- [ ] create/update response 返回同样的 coverage preview。
- [ ] 对 metric readiness 增加 binding candidate coverage summary，说明哪些 active binding 缺 `metric_input`、`primary_time`、`population_subject`。
- [ ] 保持 list endpoint 轻量，只在 detail 或 `detail=true` 时返回完整 coverage。

验收：

- 创建 binding 后，不需要 activate 就能看到缺少 `numerator` / `denominator` / `primary_time`。
- `covers_required_targets=false` 必须伴随 `missing_required_targets` 非空，除非 bound object 本身不可解析。

### 7. 统一 service-level validation guidance

- [ ] 为 semantic service validation error 引入结构化错误类型，至少包含：
  - `code`
  - `message`
  - `category`
  - `field_path`
  - `docs_url`
  - `schema_url`
  - `contract_url`
  - `examples`
  - `remediation`
- [ ] API route 对 semantic create/update/activate/validate 统一使用 structured guidance envelope。
- [ ] 将当前 plain ValueError 文案映射为稳定错误码：
  - `binding_target_kind_not_allowed_for_scope`
  - `metric_input_semantic_ref_prefix_invalid`
  - `metric_input_target_key_invalid`
  - `binding_primary_time_missing`
  - `binding_required_metric_input_missing`
- [ ] 保留原 message，但新增机器可读字段，避免破坏人工排查体验。
- [ ] 更新 `docs/api/errors.md` 与 `docs/api/semantic.md` 的错误响应说明。

验收：

- schema validation 和 service validation 的 422 都能提供 guidance。
- agent 可以根据 `code` 和 `remediation.example_patch` 自动修复常见 binding payload。

### 8. 增加 batch semantic authoring v1

设计一个 HTTP-only batch endpoint：

```http
POST /semantic/batch
```

建议请求结构：

```json
{
  "mode": "dry_run | apply",
  "lifecycle": "create_only | create_and_validate | create_validate_activate",
  "continue_on_error": true,
  "items": [
    {
      "op_key": "time.elapsed",
      "kind": "time",
      "action": "create",
      "payload": {}
    },
    {
      "op_key": "metric.avg_elapsed_time",
      "kind": "metric",
      "action": "create",
      "payload": {}
    }
  ]
}
```

建议响应结构：

```json
{
  "ok": false,
  "mode": "dry_run",
  "summary": {
    "total": 34,
    "succeeded": 31,
    "failed": 3,
    "skipped": 0
  },
  "items": [
    {
      "op_key": "binding.avg_elapsed_time",
      "kind": "binding",
      "action": "create",
      "status": "failed",
      "error": {
        "code": "binding_required_metric_input_missing",
        "message": "...",
        "guidance": {}
      },
      "coverage": {
        "missing_required_targets": []
      }
    }
  ]
}
```

任务：

- [ ] 定义 `SemanticBatchRequest` / `SemanticBatchResponse` API models。
- [ ] v1 支持对象类型：
  - `time`
  - `dimension`
  - `entity`
  - `metric`
  - `binding`
- [ ] v1 支持 action：
  - `create`
  - `validate`
  - `activate`
  - `publish`，作为 `activate` alias 时需在文档中明确。
- [ ] 支持 `dry_run`：
  - 执行 schema + service validation。
  - 不写入 metadata。
  - 返回 would_create / would_activate 结果。
- [ ] 支持 `op_key` 局部引用，允许后续 item 引用前面 item 的 semantic ref，而不是内部 id。
- [ ] 支持依赖排序：
  - 默认按提交顺序执行。
  - 后续可增加 `depends_on`，但 v1 不做复杂 DAG planner。
- [ ] 每个 item 返回独立 result、error、guidance、coverage preview。
- [ ] batch 顶层不因为单个 item 失败丢失其他 item 的诊断。

验收：

- 同源表 1 time + 12 dimension + 1 entity + 10 metric + 10 binding 可用一个 batch dry-run 发现所有结构问题。
- `apply + create_validate_activate` 可以返回逐对象 lifecycle 结果。
- 单个 metric binding 出错不会吞掉其他对象的成功结果。

### 9. 支持轻量 binding template / shared carrier defaults

batch v1 可先不引入完整模板语言，但应支持减少重复声明的最小机制。

建议扩展：

```json
{
  "defaults": {
    "carrier_bindings": {
      "primary_trino_table": {
        "binding_key": "primary",
        "carrier_kind": "table",
        "carrier_locator": {
          "catalog": "hive",
          "schema": "dwd",
          "table": "query_history"
        },
        "binding_role": "primary",
        "field_surfaces": [],
        "time_surfaces": []
      }
    },
    "time_bindings": {
      "event_date": {
        "carrier_binding_key": "primary",
        "target": {
          "target_kind": "primary_time",
          "target_key": "time.event_date"
        },
        "semantic_ref": "time.event_date",
        "resolution_kind": "date_column",
        "date_surface_ref": "field.event_date"
      }
    }
  }
}
```

任务：

- [ ] 先在 docs 中定义 `defaults` 语义，不急于支持任意变量替换。
- [ ] v1 只支持按 key 引用整块 carrier / time binding default。
- [ ] 禁止 default 静默覆盖 item 本地字段；冲突必须报错。
- [ ] 对展开后的 payload 执行同一套 schema + service validation。

验收：

- 10 个 metric binding 可以共享同一个 carrier/time default，只本地声明不同 metric input。
- 展开后的 response 能显示 effective payload 或至少显示 defaults provenance。

### 10. 更新文档、agent guide 与 OpenAPI 示例

- [ ] 更新 `docs/api/semantic.md`：
  - binding scope target kind matrix
  - metric input slot table
  - average metric binding example
  - time surface + time binding example
  - create 后 coverage preview 说明
  - batch authoring endpoint
- [ ] 更新 `docs/semantic/typed-binding-contract.zh.md`：
  - import propagation contract
  - effective target coverage
  - metric input 命名规则
  - batch/template 非核心 contract 边界
- [ ] 更新 `docs/semantic/metric-v2-schema.zh.md`：
  - family input slots 与 binding target_key 对齐。
- [ ] 更新 Marivo skill 文档 （～/source/oss/marivo-skill/marivo）：
  - agent 构建 semantic layer 时优先用 batch dry-run。
  - 对同源 metric binding 使用 shared carrier/time defaults 或 import propagation。
  - 遇到 `covers_required_targets=false` 先看 `missing_required_targets`。

验收：

- 文档示例能覆盖本次 8 个用户侧失败点。
- OpenAPI examples 与 docs 示例一致。

## 建议实施顺序

1. 先做任务 1、4、5、7：用测试和 guidance 修复最直接的 agent 试错成本。
2. 再做任务 6：让 create/detail 阶段能暴露 coverage preview。
3. 再做任务 2：实现 import target coverage propagation，减少重复声明。
4. 最后做任务 8、9、10：提供 batch authoring 与 shared defaults，并统一文档。

这个顺序可以避免一开始就把 batch endpoint 做成复杂编排器。即使 batch 尚未实现，schema / guidance / coverage preview 也能立即改善单对象 authoring。

## 验证方案

### 单元测试

- `tests/test_api_models_binding.py`
  - scope-specific target kind schema / validation。
  - metric_input semantic_ref / target_key 命名规则。
  - time_surfaces required 字段。
- `tests/test_semantic_readiness.py`
  - binding coverage preview。
  - imported target coverage。
  - missing_required_targets 明细。
- `tests/test_typed_bindings.py`
  - average metric numerator / denominator binding。
  - rate metric 双 slot 仍保持严格。

### API 测试

- `tests/test_semantic_typed_api.py`
  - create binding 返回 coverage preview。
  - 业务校验 422 返回 guidance。
  - batch dry-run 返回逐项结果。
  - batch apply + validate + activate 返回 lifecycle summary。

### 文档校验

- `make lint`
- `make typecheck`
- `make test`
- 人工按 `docs/api/semantic.md` 中的 average metric 示例构造 payload，确认能通过 dry-run 或 create。

## 验收标准

- 对同一 Trino 表构建 10 个 metric binding 时，不再需要重复声明 10 次相同 carrier/time binding；可通过 import propagation 或 shared defaults 复用。
- entity binding 中误用 `metric_input` 时，错误包含 allowed target kind matrix 和 remediation。
- `metric_input` semantic_ref 命名错误时，错误明确要求 `metric_input.*` 并给出可复制示例。
- `TimeSurfaceSpec.physical_name` 的 required 规则在 schema example、docs 和 guidance 中都可见。
- `covers_required_targets=false` 时，detail/create response 同步返回 `missing_required_targets`。
- average metric 的 `numerator` / `denominator` binding 有完整示例和测试覆盖。
- service-level 422 与 Pydantic 422 都返回 guidance。
- batch dry-run 可以一次性返回 semantic object graph 的所有 create/validate 问题。

## 风险与边界

- Import propagation 若过宽，会隐藏真实 join / carrier 差异。v1 必须限制在同源或显式 relation 可解释的 target coverage。
- Batch endpoint 不应绕开对象级 service validation；它只是组合调用与诊断聚合。
- Shared defaults 不应成为第二套 binding schema；展开后仍必须是标准 typed binding payload。
- 若后续决定让 `time_bindings` 直接引用 `time_surfaces`，需要单独设计 schema 迁移或 breaking cutover，不应混入本计划 v1。
