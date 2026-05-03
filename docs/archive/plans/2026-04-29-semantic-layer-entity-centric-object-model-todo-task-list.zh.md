# Marivo Semantic Layer Entity-Centric Object Model 实施 Todo Task List

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Marivo semantic layer 对象模型收敛为 `entity` 唯一 physical grounding、其他 semantic objects 只引用 `entity.field` 或 semantic refs 的目标态。

**Architecture:** 先冻结对象模型与迁移边界，再按 `entity/domain -> dimension/time/predicate -> metric/process -> relationship/profile -> compiler/readiness -> HTTP/docs/tooling` 顺序推进。实现上保留 Marivo HTTP-only 主边界，不把 `marivo-mcp` 或 skill 文档当作服务端契约来源。

**Tech Stack:** FastAPI route in `app/api/semantic.py`, Pydantic models in `app/api/models`, semantic services in `app/semantic_service`, runtime/compiler in `app/semantic_runtime` and `app/analysis_core`, readiness in `app/semantic_readiness`, docs under `spec/semantic`, `docs/api`, `agent-guide.md`, `marivo-skill`, and `marivo-mcp`.

---

## 概述

本文将 [`spec/semantic/entity-centric-object-model.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/entity-centric-object-model.zh.md) 拆解为一份可直接落地开发的实施清单。

目标对象模型一句话概括：

```text
entity-centric authoring
entity-only physical grounding
thin entity fields
object-owned semantic roles
relationship/profile-based composition
compiler-enforced compatibility
```

本轮是 semantic layer **对象模型重构**，不是使用范围治理重构。计划不引入 session / workspace / official 分层，不实现 promotion / approval 流程，也不重构企业权限体系。

## 文档依据

- [`spec/semantic/entity-centric-object-model.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/entity-centric-object-model.zh.md)
- [`spec/semantic/entity-schema-contract.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/entity-schema-contract.zh.md)
- [`spec/semantic/typed-binding-contract.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/typed-binding-contract.zh.md)
- [`spec/semantic/dimension-schema-contract.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/dimension-schema-contract.zh.md)
- [`spec/semantic/time-schema-contract.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/time-schema-contract.zh.md)
- [`spec/semantic/predicate-schema-contract.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/predicate-schema-contract.zh.md)
- [`spec/semantic/metric-v2-schema.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/metric-v2-schema.zh.md)
- [`spec/semantic/process-object-schema.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/process-object-schema.zh.md)
- [`spec/semantic/compiler-spec.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/compiler-spec.zh.md)
- [`docs/api/semantic.md`](/Users/lichengxiang/source/oss/marivo/docs/api/semantic.md)
- [`agent-guide.md`](/Users/lichengxiang/source/oss/marivo/agent-guide.md)
- [`marivo-skill/marivo/SKILL.md`](/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo/SKILL.md)
- [`marivo-mcp/README.md`](/Users/lichengxiang/source/oss/marivo/marivo-mcp/README.md)

## 当前实现对照

当前仓库中已经存在以下实现基础：

- `app/api/models/entity.py` 定义 entity contract，但 entity 仍主要是业务对象 contract，不是唯一 physical grounding 单元。
- `app/api/models/binding.py` 定义 typed binding，当前 binding 可按 `entity`、`metric`、`process_object` 等 scope 绑定 physical carrier。
- `app/api/models/dimension.py`、`time.py`、`predicate.py`、`metric.py`、`process_object.py` 仍存在各自的 schema contract，其中部分对象和 binding/readiness 之间存在独立落地关系。
- `app/semantic_service/binding.py`、`typed_objects.py`、`common.py` 和 `app/semantic_readiness` 仍围绕 typed binding coverage、target kind、metric input 等逻辑做校验。
- `app/semantic_runtime` 与 `app/analysis_core/compiler.py` 已有 semantic resolution / compile 路径，但目标态需要从 semantic refs 收集 `entity.field`，再通过 entity binding 解析 physical columns。
- `docs/api/semantic.md`、`marivo-skill`、`marivo-mcp` 仍需要从“多 semantic object binding”叙述调整到“entity-first authoring”。

因此，本轮重点不是增量修补 authoring 示例，而是把 physical grounding 权威收敛到 `entity`，并让其他对象模型、compiler、readiness、API 文档和 agent guidance 与此一致。

## 实施范围

### 本次必须覆盖

- 引入统一 `catalog_metadata.domain_ref` 与 domain discovery 最小 contract。
- 将 entity 扩展为唯一 physical binding owner：entity 可绑定物理表/view 的字段子集，多个 entity 可绑定同一物理表/view。
- 将 `entity.field` 收敛为 thin field surface，只表达字段本体、基础类型、治理标签和 physical locator。
- 从 `dimension`、`time`、`predicate`、`metric`、`process_object` 的 public contract 中移除对象自有 physical binding 入口。
- 让上述 semantic objects 通过 `entity.field`、`time.*`、`predicate.*`、`dimension.*`、`process.*` 等 refs 表达语义角色。
- 引入 entity relationship / compatibility profile 的最小对象模型，用于 key、time、grain、cardinality、additivity 等编译前兼容性判断。
- 改造 compiler resolution flow，使执行前确定性完成 ref resolution、field type、relationship/profile、time/grain/additivity/governance 校验。
- 更新 readiness / blocker，使缺失 entity binding、字段类型不匹配、缺 relationship、grain 不兼容等问题以 semantic blocker 暴露。
- 更新 HTTP API、spec 文档、agent-guide、marivo-skill、marivo-mcp 相关说明，避免继续指导 agent 为 metric/time/process 单独建 physical binding。
- 补齐 API model、service、compiler、readiness、end-to-end 和 docs contract 测试。

### 本次明确不做

- 不引入 session / workspace / official 对象使用范围。
- 不实现 semantic object promotion、审批、发布治理流。
- 不重构权限体系；只为后续 governance 校验保留结构化上下文。
- 不把 relationship/profile 做成任意 SQL DSL、join graph DSL 或通用规则引擎。
- 不复制 SQL 全部表达力；复杂清洗、拼宽、预计算逻辑应进入上游 view/model，再由 entity binding 暴露。
- 不把 `marivo-mcp` 作为 Marivo 服务端能力前提；Marivo core 仍保持 HTTP-only。
- 不做在线兼容迁移、双写、灰度或 legacy binding 回读；若当前 metadata 模板需要重置，按 fresh-init / reset 方案处理。

## 交付原则

- 粒度以“单个 owner 可独立完成并验收”为准，避免泛泛的“重构 semantic layer”。
- Contract 优先：先冻结 public shape、错误码和迁移边界，再动 service/compiler。
- Entity 不等于物理表：entity 可只映射物理表字段子集，也允许多个 entity 绑定同一物理表/view。
- Field role 归消费对象：`field_kind`、`semantic_role`、`allowed_usages` 不放在 field 层作为编译真相。
- Domain 只服务 catalog discovery，不作为权限来源、compiler compatibility 真相或 stable ref 组成部分。
- Typed analysis step 仍是外部分析主契约，不把 raw SQL 变成 agent 面的主要接口。

## 建议实施顺序

1. T1 冻结 scope、cutover 策略和 contract 差异清单。
2. T2 落 catalog metadata 与 domain discovery 基础。
3. T3 重构 entity / entity field / entity binding。
4. T4 重构 dimension / time / predicate 为 field-referencing objects。
5. T5 重构 metric / process object 为 no-physical-binding objects。
6. T6 落 relationship / compatibility profile 最小模型。
7. T7 改造 service、compiler、readiness 和 semantic blocker。
8. T8 收敛 HTTP API、storage、reset、OpenAPI 和错误面。
9. T9 补测试矩阵和 golden cases。
10. T10 更新 spec、docs、agent-guide、marivo-skill、marivo-mcp。

说明：

- T2 与 T3 可以并行做设计细化，但实现应先让 domain/catalog metadata 不污染 semantic core contract。
- T4/T5 依赖 T3 的 `entity.field` ref shape。
- T6/T7 是跨 entity metric/process 可用性的关键路径，不能只靠文档说明替代实现校验。
- T10 中 `marivo-skill` 和 `marivo-mcp` 是 adapter / guidance 更新，不改变 Marivo core 的 HTTP-only 边界。

## Todo Task List

## 一、Scope 与 Contract 冻结

- [x] 任务 1.1：冻结对象模型重构边界
  - 交付物：更新 [`spec/semantic/entity-centric-object-model.zh.md`](/Users/lichengxiang/source/oss/marivo/spec/semantic/entity-centric-object-model.zh.md) 的 scope note，明确本轮只做对象模型重构。
  - 关键内容：保留“非目标”中的 session/workspace/official、promotion/approval、权限体系重构排除项。
  - 验收标准：计划、spec、API 文档中不出现本轮必须实现审批流或使用范围分层的表述。

- [x] 任务 1.2：冻结 physical grounding 唯一权威
  - 交付物：contract decision record。
  - 关键内容：只有 `entity` 可以拥有 physical binding；`dimension/time/predicate/metric/process` 不直接绑定 physical table/view/column。
  - 验收标准：后续 schema 和 service 任务都能指向同一条规则，且没有第二套 binding authority。

- [x] 任务 1.3：冻结 `entity.field` 最小字段集
  - 交付物：字段 contract matrix。
  - 最小字段：`field_ref`、`display_name`、`description`、`value_type`、`nullable`、`unit`、`enum_hint`、`sample_values/profile_summary`、`sensitivity_tags`、`physical_column/physical_expression_locator`。
  - 明确删除：`field_kind`、`semantic_role`、`allowed_usages`。
  - 验收标准：field 层不提前声明 numerator、dimension、time anchor、process step 等消费角色。

- [x] 任务 1.4：冻结 stable ref 与 domain 关系
  - 交付物：catalog metadata decision record。
  - 关键内容：`domain_ref` 放在 `catalog_metadata` 中，不编入 `metric.*`、`entity.*` 等 stable ref。
  - 验收标准：业务域调整不会强制 semantic object core contract revision。

- [x] 任务 1.5：冻结 legacy typed binding cutover 策略
  - 交付物：cutover note。
  - 关键内容：metric/process/dimension/time/predicate binding path 一次性切到 entity-only physical grounding；不做双写、双读或 legacy binding 自动回投影。
  - 验收标准：实现任务中没有保留“长期兼容旧 binding authority”的分支。

## 二、Catalog Metadata 与 Domain Discovery

- [x] 任务 2.1：定义统一 `catalog_metadata` 模型
  - 交付物：`app/api/models/base.py` 或独立 model 文件中的 metadata envelope。
  - 字段：`domain_ref`、`related_domain_refs`、`aliases`。
  - 规则：顶层 semantic objects 推荐必填 `domain_ref`；`entity.field` 默认继承 entity domain。
  - 验收标准：entity、dimension、time、predicate、metric、process、relationship、compatibility profile 都能携带同一 metadata shape。

- [x] 任务 2.2：定义 Domain Catalog API model
  - 交付物：domain Pydantic models。
  - 字段：`domain_ref`、`display_name`、`description`、`status`、`aliases`。
  - `status` 最小取值：`active`、`deprecated`。
  - 验收标准：不引入 `owner_ref`、`parent_domain_ref` 等当前没有明确需求的字段。

- [x] 任务 2.3：实现 domain 持久化与 registry
  - 交付物：metadata schema、registry/service 读写逻辑。
  - 最小能力：create/read/list/update/deprecate domain catalog entry。
  - 验收标准：domain 是 catalog entry，不被 compiler 当作 semantic compatibility 真相。

- [x] 任务 2.4：实现 domain discovery HTTP surface
  - 交付物：`GET /semantic/domains`、`GET /semantic/domains/{domain_ref}`、按 domain list/search semantic objects 的 HTTP route。
  - 最小查询：`domain_ref`、object type、status、text query。
  - 验收标准：agent 可先 list domains，再在选定 domain 内搜索 semantic objects。

- [x] 任务 2.5：补 domain search/readiness 不越权说明
  - 交付物：API docs 与 service 注释。
  - 关键内容：domain 不是权限来源；权限仍由 governance policy、数据访问授权和底层执行引擎 ACL 判断。
  - 验收标准：错误码和文档不把 `domain_ref` 描述成授权判断依据。

## 三、Entity / Field / Binding 重构

- [x] 任务 3.1：重构 entity schema 为唯一 grounding owner
  - 交付物：`app/api/models/entity.py` 的 entity create/read/list contract。
  - 关键内容：entity contract 同时包含业务实体定义、thin fields、entity binding 引用或内联 binding block。
  - 验收标准：entity 可绑定单个物理 table/view 的字段子集，且多个 entity 可绑定同一 source object。

- [x] 任务 3.2：定义 `entity_kind` 轻量分类
  - 交付物：枚举与文档。
  - 取值：`business_entity`、`event_entity`、`fact_entity`、`snapshot_entity`、`derived_entity`。
  - 验收标准：`entity_kind` 只用于 agent/catalog/readiness hint，不参与 SQL lowering、权限判断或字段用途判断。

- [x] 任务 3.3：实现 entity field physical locator
  - 交付物：field model 与 service validation。
  - 支持：physical column locator、受控 physical expression locator。
  - 验收标准：field locator 足以从 source object 映射到执行侧 column，但不承载 metric/process 语义角色。

- [x] 任务 3.4：收敛 typed binding storage 到 entity binding
  - 交付物：metadata schema 与 service cutover。
  - 关键内容：旧 `binding_scope=metric/process_object` 不再作为 physical grounding 权威；entity binding 成为唯一 active grounding path。
  - 验收标准：新建 metric/process 不需要也不能提交 physical carrier binding。

- [ ] 任务 3.5：实现 entity binding validate/readiness
  - 交付物：readiness evaluator。
  - 校验：source object 存在、locator 可解析、字段 physical column 存在、value_type/profile 与 source metadata 兼容、sensitivity_tags 可读。
  - 验收标准：缺字段或类型不匹配返回结构化 blocker，而不是等 compiler 生成 SQL 后失败。

- [ ] 任务 3.6：实现 field reverse dependency graph
  - 交付物：service 查询与 read response。
  - 内容：列出消费某个 `entity.field` 的 dimension/time/predicate/metric/process/profile。
  - 验收标准：字段变更前可判断影响面，不需要扫描自由文本或 SQL。

## 四、Dimension / Time / Predicate 重构

- [ ] 任务 4.1：重构 dimension 为 `entity.field` referencing object
  - 交付物：`app/api/models/dimension.py` 与 service validation。
  - 关键字段：`source_field_ref`、value domain、hierarchy/grouping policy、governance metadata。
  - 验收标准：dimension 不再拥有 physical column、carrier、binding target。

- [ ] 任务 4.2：重构 time 为 `entity.field` referencing object
  - 交付物：`app/api/models/time.py` 与 time service validation。
  - 关键字段：`source_field_ref`、time role、calendar/alignment policy、timezone/format 语义。
  - 验收标准：time object 可复用同一个 field，但 time anchor 角色由 time object 自己声明。

- [ ] 任务 4.3：重构 predicate 为 field atom / semantic ref filter
  - 交付物：`app/api/models/predicate.py` 与 predicate validator。
  - 支持：atom target 引用 `entity.field`、组合引用其他 predicate、allowed usage / lineage 仍在 predicate object 中声明。
  - 验收标准：predicate 不暴露 physical column，不把 filter SQL 作为 public contract。

- [ ] 任务 4.4：删除 dimension/time/predicate 的物理绑定入口
  - 交付物：API model、service、docs、tests 中的旧 binding 字段清理。
  - 验收标准：任何 create/update 请求携带 object-level physical binding 时稳定返回 contract error。

- [ ] 任务 4.5：补 field type usage validation
  - 交付物：common semantic validator。
  - 示例：time 必须引用 timestamp/date-compatible field；dimension 可引用 category/string/enum/numeric bucket source；predicate op 必须匹配 field value_type。
  - 验收标准：字段类型错误在 validate 阶段返回 `invalid_field_type_for_semantic_object` 类 blocker。

## 五、Metric / Process Object 重构

- [ ] 任务 5.1：重构 metric component input
  - 交付物：`app/api/models/metric.py`。
  - 关键内容：component input 直接引用 `entity.field`，保留 measurement semantics、component role、aggregation、sample basis、observed entity/grain、primary time、additivity/comparability。
  - 验收标准：metric 无 physical binding 字段，sum/count/average/rate 等 family 能表达 required inputs。

- [ ] 任务 5.2：支持跨 entity metric component
  - 交付物：metric model 与 service validation。
  - 示例：conversion rate 同时引用 `entity.conversion_event.field.converted_users` 与 `entity.exposure_event.field.exposed_users`。
  - 验收标准：metric create 可以表达跨 entity component；缺 relationship/profile 时 validate 返回 blocker，而不是拒绝模型表达。

- [ ] 任务 5.3：重构 metric default predicate / primary time 引用
  - 交付物：metric service validation。
  - 规则：`primary_time_ref` 引用 `time.*`，predicate refs 引用 `predicate.*`，二者最终必须可追溯到 compatible `entity.field`。
  - 验收标准：metric 不重复声明 time/predicate 的 physical mapping。

- [ ] 任务 5.4：重构 process object field refs
  - 交付物：`app/api/models/process_object.py`。
  - 关键内容：cohort/funnel/session/experiment/path/lifecycle contract 中涉及字段时引用 `entity.field`、`time.*`、`predicate.*`、`dimension.*`。
  - 验收标准：process object 不拥有 physical table/view/column binding。

- [ ] 任务 5.5：实现 process sequence/window semantic validation
  - 交付物：process validator。
  - 校验：step field value_type、subject entity、time anchors、matching window、state transition 引用合法。
  - 验收标准：process 语义错误以 typed blocker 暴露，不泄漏 engine-specific matcher 细节。

- [ ] 任务 5.6：清理 metric/process binding authoring API
  - 交付物：HTTP route、OpenAPI、docs、tests。
  - 关键内容：停止指导 agent 创建 metric binding/process binding；如旧 endpoint 仍存在，应返回 deprecation/contract error 或被直接删除。
  - 验收标准：agent-first 建模流程变为 entity -> fields -> time/dimension/predicate -> metric/process。

## 六、Relationship / Compatibility Profile 最小模型

- [ ] 任务 6.1：定义 entity relationship model
  - 交付物：Pydantic model、schema docs、storage。
  - 字段：`relationship_ref`、left/right entity refs、key alignment、time alignment、cardinality、grain compatibility、snapshot effective window alignment。
  - 验收标准：relationship 不包含 physical join SQL、optimizer hint、CTE shape 或任意 boolean expression DSL。

- [ ] 任务 6.2：实现 relationship validation
  - 交付物：service validator。
  - 校验：左右 entity 存在、field refs 存在、key value_type compatible、time refs 可解析、cardinality/grain 合法。
  - 验收标准：非法 relationship 在写入或 validate 阶段失败，错误可被 agent 修复。

- [ ] 任务 6.3：定义 compatibility profile model
  - 交付物：Pydantic model、schema docs、storage。
  - v1 范围：required relationships、key/grain/time compatibility、additivity/aggregation compatibility、field profile requirements、governance preflight requirements。
  - 验收标准：profile 不替代 metric/process contract，也不拥有 physical binding。

- [ ] 任务 6.4：实现 profile validation 与 discovery
  - 交付物：profile service 与 list/search API。
  - 能力：按 metric/process 或 entity pair 查询候选 profile。
  - 验收标准：跨 entity metric/process validate 能找到候选 profile 或返回 `missing_entity_relationship` / `missing_compatibility_profile`。

- [ ] 任务 6.5：限制 relationship/profile 表达力
  - 交付物：contract tests。
  - 拒绝：raw SQL、arbitrary join graph、generic rule engine fields。
  - 验收标准：测试覆盖不支持字段，避免后续把 profile 扩成 SQL DSL。

## 七、Compiler / Runtime / Readiness 主链路

- [ ] 任务 7.1：实现 entity-centric ref resolution
  - 交付物：`app/semantic_runtime/resolution.py` 或等价 resolver。
  - 流程：resolve metric/dimension/time/predicate/process refs -> collect `entity.field` refs -> resolve entity binding revisions -> resolve physical columns。
  - 验收标准：执行计划不再通过 metric/process binding 找 physical grounding。

- [ ] 任务 7.2：实现 field usage compatibility validator
  - 交付物：compiler preflight validator。
  - 校验：field value_type vs object usage、nullable policy、unit、enum/profile summary、sensitivity_tags。
  - 验收标准：返回 `invalid_metric_input_type`、`invalid_time_field_type`、`invalid_predicate_operand_type` 等结构化 blocker。

- [ ] 任务 7.3：实现 cross-entity composition validator
  - 交付物：relationship/profile resolver。
  - 校验：required relationship、key alignment、time alignment、grain compatibility、cardinality、snapshot effective window。
  - 验收标准：跨 entity metric 缺关系时返回 `missing_entity_relationship`，grain 不兼容时返回 `incompatible_grain`。

- [ ] 任务 7.4：改造 compiler lowering
  - 交付物：`app/analysis_core/compiler.py` 或 planner 相关模块。
  - 内容：从 entity binding 生成 physical plan，保留 typed analysis step 作为外部契约。
  - 验收标准：lowering 不要求外部调用方提交 raw SQL，也不让 semantic object 携带 SQL join 片段。

- [ ] 任务 7.5：冻结 resolved refs + revisions
  - 交付物：artifact / step metadata snapshot。
  - 内容：记录 metric/process/time/dimension/predicate/entity/profile refs 与对应 revisions，以及最终 entity field -> physical locator 解析结果。
  - 验收标准：分析结果可回放、可审计，字段或 entity binding 后续变化不影响已生成 artifact 的解释。

- [ ] 任务 7.6：改造 readiness evaluator
  - 交付物：`app/semantic_readiness`。
  - 内容：readiness 从旧 binding coverage 转向 entity binding、field refs、relationship/profile、governance preflight。
  - 验收标准：not_ready 原因能指向缺 entity field、缺 entity binding、缺 relationship/profile、字段类型错误或 governance blocker。

- [ ] 任务 7.7：统一 semantic blocker taxonomy
  - 交付物：错误码枚举与 docs。
  - 最小错误码：`missing_entity_binding`、`missing_entity_field`、`ambiguous_field_ref`、`missing_time_object`、`invalid_metric_input_type`、`missing_entity_relationship`、`missing_compatibility_profile`、`incompatible_grain`、`permission_denied`。
  - 验收标准：API、compiler、readiness 返回同一组 code，不依赖自由文本判断。

## 八、HTTP API / Storage / OpenAPI Cutover

- [ ] 任务 8.1：重构 semantic create/read/list API
  - 交付物：`app/api/semantic.py` 和 API models。
  - 内容：entity-first create/read/list；top-level objects 支持 `catalog_metadata.domain_ref`；metric/process 不接收 physical binding。
  - 验收标准：OpenAPI 反映目标对象模型，不出现 metric/process physical binding 示例。

- [ ] 任务 8.2：实现 semantic object search by domain
  - 交付物：search/list service。
  - 支持：按 domain_ref、object_type、lifecycle/readiness、text query、related_domain_refs 搜索。
  - 验收标准：agent 能先锁定 business domain，再发现可用 entity/metric/dimension/time/predicate/process。

- [ ] 任务 8.3：更新 metadata schema / reset path
  - 交付物：`app/storage/schema.py`、metadata template、reset/bootstrap 脚本。
  - 策略：fresh-init / reset 为主，不做在线迁移兼容。
  - 验收标准：全新 metadata store 直接创建目标态表结构；旧 binding authority 不再被新代码依赖。

- [ ] 任务 8.4：处理 legacy payload failure surface
  - 交付物：request validation 与错误文档。
  - 内容：旧请求若携带 metric/process binding、dimension/time physical binding、field role 等目标态禁止字段，应返回结构化 contract error。
  - 验收标准：agent 能从错误中知道应改为 entity-first authoring。

- [ ] 任务 8.5：同步 API 示例
  - 交付物：`docs/api/semantic.md` 示例 payload。
  - 示例：创建 domain、entity+fields+binding、dimension/time/predicate、single-entity metric、cross-entity ratio、process object、relationship/profile、validate/compile。
  - 验收标准：示例不需要 raw SQL，也不需要 metric/process physical binding。

## 九、测试矩阵与 Golden Cases

- [ ] 任务 9.1：补 API model contract tests
  - 交付物：`tests/test_api_models_entity.py`、`tests/test_api_models_dimension.py`、`tests/test_api_models_time.py`、`tests/test_api_models_predicate.py`、`tests/test_api_models_metric.py`、`tests/test_api_models_process_object.py`。
  - 覆盖：允许的 entity field shape、禁止 field role、禁止非 entity 对象 physical binding、domain metadata。
  - 验收标准：schema contract 能在单元测试层防止模型回退。

- [ ] 任务 9.2：补 domain catalog API tests
  - 交付物：domain list/get/search tests。
  - 覆盖：active/deprecated、aliases、按 domain_ref list semantic objects、related domain 搜索扩展。
  - 验收标准：agent discovery 入口可重复验证。

- [ ] 任务 9.3：补 entity binding readiness tests
  - 交付物：`tests/test_semantic_readiness.py` 或 focused test file。
  - 覆盖：missing source object、missing physical column、type mismatch、sensitivity tag governance blocker。
  - 验收标准：entity 作为 grounding owner 的 blocker 完整可见。

- [ ] 任务 9.4：补 single-entity metric golden case
  - 交付物：semantic end-to-end test。
  - 场景：`entity.order.field.pay_amount` -> `time.order_paid_at` -> `predicate.successful_order` -> `metric.gmv`。
  - 验收标准：compiler 从 entity binding 解析 physical column，并生成可执行 typed plan。

- [ ] 任务 9.5：补 cross-entity ratio golden case
  - 交付物：semantic compiler test。
  - 场景：conversion rate 同时引用 exposure 与 conversion entity fields。
  - 覆盖：缺 relationship 失败、补 relationship 后通过、grain 不兼容失败。
  - 验收标准：跨 entity 表达力不因 metric 无 physical binding 而丢失。

- [ ] 任务 9.6：补 process object golden case
  - 交付物：process contract + compiler/readiness test。
  - 场景：checkout funnel 引用多个 event entity fields 与 matching window。
  - 验收标准：process 不绑定 physical carrier，也能通过 entity fields 完成 semantic preflight。

- [ ] 任务 9.7：补 snapshot alignment golden case
  - 交付物：relationship/profile test。
  - 场景：event entity 与 snapshot entity 的 effective window alignment。
  - 验收标准：`entity_kind=snapshot_entity` 只作为 hint，真正判断来自 relationship/profile。

- [ ] 任务 9.8：补 docs contract validation
  - 交付物：文档示例 smoke 或 schema validation。
  - 覆盖：`docs/api/semantic.md`、`marivo-skill` payload snippets、`marivo-mcp` tool examples。
  - 验收标准：文档示例能通过目标态 schema 校验。

## 十、文档 / Agent Guidance / MCP Adapter 收尾

- [ ] 任务 10.1：更新 semantic spec 文档族
  - 交付物：`spec/semantic/*.zh.md`。
  - 文件：entity、typed-binding、dimension、time、predicate、metric-v2、process-object、compiler、overview。
  - 验收标准：spec 文档统一指向 entity-only physical grounding，不残留多对象 physical binding 目标态描述。

- [ ] 任务 10.2：更新 `docs/api/semantic.md`
  - 交付物：HTTP API contract 文档。
  - 内容：domain catalog、entity-first authoring、relationship/profile、validate/compile blockers、legacy payload failure。
  - 验收标准：外部调用方只读 API 文档即可按目标态创建 semantic objects。

- [ ] 任务 10.3：更新 `agent-guide.md`
  - 交付物：仓库级 agent guidance。
  - 内容：只保留 repo-wide coding/testing 规则与最短 semantic modeling guardrail；详细 Marivo 使用说明仍放 `marivo-skill`。
  - 验收标准：`agent-guide.md` 不膨胀成产品使用手册。

- [ ] 任务 10.4：更新 `marivo-skill`
  - 交付物：`marivo-skill/marivo/SKILL.md` 与 `references/semantic-layer.md`、`payload-cheatsheet.md`、`steps.md`、`semantic-readiness.md`。
  - 内容：entity-first authoring loop、domain discovery、field refs、relationship/profile blocker 修复路径。
  - 验收标准：skill 不再建议 agent 为 metric/time/process 创建独立 physical binding。

- [ ] 任务 10.5：更新 `marivo-mcp` 文档与 tool inventory 描述
  - 交付物：`marivo-mcp/README.md`、`marivo-mcp/docs/release-checklist.md`、tool inventory / schema docs。
  - 内容：MCP 作为 HTTP adapter，工具说明映射到目标态 HTTP payload。
  - 验收标准：文档明确 Marivo core 是 HTTP-only，MCP 不引入第二套 contract。

- [ ] 任务 10.6：更新 release / migration note
  - 交付物：release note 或 plan 附录。
  - 内容：fresh-init / reset 边界、legacy binding authority 删除、文档示例切换、测试命令。
  - 验收标准：开发者知道本轮是破坏性对象模型重构，不会期待旧 metadata 在线平滑迁移。

## 验证方案

### 静态文档验证

```bash
test -f plan/2026-04-29-semantic-layer-entity-centric-object-model-todo-task-list.zh.md
rg -n "entity-only physical grounding|entity-centric authoring|domain_ref|relationship|compatibility profile|session / workspace / official|HTTP-only" plan/2026-04-29-semantic-layer-entity-centric-object-model-todo-task-list.zh.md
rg -n "field_kind|semantic_role|allowed_usages|owner_ref|parent_domain_ref" spec/semantic/entity-centric-object-model.zh.md plan/2026-04-29-semantic-layer-entity-centric-object-model-todo-task-list.zh.md
```

预期：

- 前两个命令能找到目标态关键概念。
- 第三个命令只允许在“明确删除/不引入”语境中出现，不应作为目标字段出现。

### 单元与契约测试

```bash
make test
make typecheck
make lint
```

预期：

- semantic API model tests 覆盖目标态 request/response shape。
- readiness tests 覆盖 entity binding、relationship/profile、field type blocker。
- compiler/runtime tests 覆盖 single-entity metric、cross-entity ratio、process、snapshot alignment。
- typecheck/lint 通过，不使用 bare `pytest`、`python`、`mypy`、`ruff`。

### 手工验收流

1. 创建 `domain.commerce`。
2. 创建 `entity.order`，只绑定订单宽表的字段子集。
3. 基于 `entity.order.field.pay_time` 创建 `time.order_paid_at`。
4. 基于 `entity.order.field.order_status` 创建 `dimension.order_status` 和 `predicate.successful_order`。
5. 基于 `entity.order.field.pay_amount` 创建 `metric.gmv`。
6. validate/compile `metric.gmv`，确认 compiler 从 entity binding 解析 physical column。
7. 创建 exposure/conversion 两个 entity 和 `metric.conversion_rate`。
8. 不创建 relationship 时 validate 返回 `missing_entity_relationship`。
9. 创建 relationship/profile 后 validate 通过。
10. 检查 artifact snapshot 中包含 semantic refs/revisions 与 entity field physical locator 解析结果。

## 验收标准

- 所有新建 semantic object contract 都符合 entity-first 模型。
- 只有 entity 持有 physical binding；其他 objects 只引用 `entity.field` 或 semantic refs。
- `entity.field` 不保存消费角色；角色由 dimension/time/predicate/metric/process 自己声明。
- Domain catalog 支持 list/get/search，但 domain 不参与权限判断和 compiler compatibility 判断。
- 跨 entity metric/process 通过 relationship/profile 做确定性 preflight。
- Compiler 在 SQL lowering 前完成 semantic blocker 校验。
- Agent guidance、API docs、marivo-skill、marivo-mcp 文档都不再指导多对象 physical binding。
- 测试覆盖 single entity metric、cross entity ratio、process funnel、snapshot alignment、legacy payload rejection。
- 本轮没有引入 session/workspace/official、promotion/approval、权限体系重构等非目标能力。

## 推荐 PR 切分

1. PR1：contract/spec/docs 冻结与 domain catalog model。
2. PR2：entity/field/entity binding schema 与 storage cutover。
3. PR3：dimension/time/predicate no-physical-binding refactor。
4. PR4：metric/process no-physical-binding refactor。
5. PR5：relationship/profile 与 compiler/readiness 主链路。
6. PR6：API docs、agent-guide、marivo-skill、marivo-mcp、golden cases 收尾。
