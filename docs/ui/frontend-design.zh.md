# Marivo UI 前端设计方案

状态：draft design。

本文定义 Marivo UI 前端的产品定位、用户角色、核心工作流、页面信息架构与 v1 交付范围。本文不是运行时代码设计，不定义新的 HTTP API 契约，也不恢复历史内置 `/ui` 或 `/admin` 页面。

Marivo 的主要调用者仍然是 agent。UI 的职责是为人提供更直观的控制台，让管理员、业务专家和分析人员可以看清系统配置、语义建模状态与分析证据链，而不是把 Marivo 改造成 BI 工具、SQL 工作台或人类优先的分析产品。

## 设计定位

Marivo UI 是 agent-first 分析运行时的人类控制台。

它服务三类人类用户：

- Marivo 管理员：让数据源、执行引擎、映射、治理策略和运行状态稳定可用。
- 业务专家 / 语义建模者：把业务域沉淀为可发现、可执行、可复核的 semantic layer。
- 分析人员：查看自己或 agent 发起的分析 session、证据、判断、缺口和运行状态。

UI 不承担以下职责：

- 不是 BI 产品，不以自由拖拽报表、仪表盘搭建或可视化探索为中心。
- 不提供自由 raw SQL workbench，不把 SQL 作为外部主契约。
- 不绕过 typed intent、semantic layer、evidence surface 直接暴露底层执行能力。
- 不恢复旧的 FastAPI 内置 `/ui` 或 `/admin`。
- 不假设任何 MCP 层存在；Marivo UI 通过 HTTP API 访问 Marivo。
- 不把 `marivo.yaml` 作为 source、engine、mapping 的管理入口；这些对象只通过 HTTP API 管理。

当前 API 未提供真实认证或 RBAC。因此 v1 的角色划分只用于导航结构、信息优先级和操作边界设计，不是安全边界。真实权限控制必须等待认证 / RBAC 契约落地后再接入。

## 设计原则

### 1. HTTP-only

UI 只依赖 Marivo HTTP API，不引入平行管理通道。页面文案和交互不得暗示存在非 HTTP 的管理层。

### 2. 任务闭环优先

UI 不按 API 资源机械平铺，而按用户任务组织页面。用户进入页面后应该先看到“当前哪里不可用、下一步该修什么、证据是否足够”，而不是先看到一组未解释的 JSON 资源。

### 3. Typed intent 优先

分析写入应围绕 typed intent。UI 可以辅助构造 typed request，但不把自由 SQL 作为主要入口，也不把 SQL 结果当作 evidence surface 的替代品。

### 4. Readiness 优先

管理员和业务专家最需要知道的是对象是否可用。页面默认突出 `readiness_status`、`failure_code`、`blocking_requirements`、依赖关系和修复路径。

### 5. 证据闭包优先

分析人员不应在 artifact、finding、proposition、assessment 之间来回猜测。UI 应围绕 session state 和 proposition context 展示一个可读的证据闭包。

### 6. 操作诊断与证据结论分离

`runtime-status`、`Jobs` 和 observability 页面用于解释执行进度、队列、失败与排障。它们不是证据结论面，不能替代 session state 或 proposition context。

## 用户角色

| 角色 | 核心问题 | 主要对象 | 默认判断标准 |
| --- | --- | --- | --- |
| Marivo 管理员 | 系统是否能稳定执行？ | sources、engines、mappings、policies、quality rules、jobs、runtime status | source / engine / mapping ready，routing 可解释，治理规则生效 |
| 业务专家 / 语义建模者 | 业务语义是否可被 agent 稳定使用？ | semantic models、datasets、fields、metrics、relationships、readiness | semantic model ready，dataset / field grounding 完整，能力符合分析场景 |
| 分析人员 | 这次分析查了什么、证据是什么、还缺什么？ | sessions、state view、proposition context、findings、assessments、gaps、artifacts | 证据链可解释，blocking gaps 清晰，运行状态可诊断 |

## 管理员工作流与页面

管理员的目标是让 Marivo 具备稳定的数据面、执行面、治理面和排障面。

### 日常工作流

1. 注册或检查 datasource。
2. 浏览 live catalog，选择用于 `dataset.source` 的 schema / table。
3. 浏览 columns / preview，确认用于 `field.expression` 的字段和表达式。
4. 注册或检查 engine。
5. 建立 source 到 engine 的 mapping。
6. 使用 Routing Debugger 验证 table names 能路由到正确 engine。
7. 配置 policies 和 quality rules。
8. 查看 health、metrics、runtime status 和 Jobs，定位失败原因。

### 运行总览

运行总览是管理员默认首页，优先展示系统可用性，而不是资源数量。

页面内容：

- `/health` 当前状态。
- `/metrics` 中的 active sessions、pending jobs、steps failed、datasource browse failures 等摘要。
- source、engine、mapping 的 ready / not_ready 分布。
- 最近失败的 jobs、datasource browse failures、routing failures。
- 主要 blocker 列表，按影响范围排序。

展示方式：

- 顶部状态条展示服务健康状态和最近更新时间。
- 中部用紧凑统计卡展示 readiness 分布和失败计数。
- 下方用 blocker table 展示对象、`readiness_status`、`failure_code`、影响范围和建议进入的修复页面。

### Sources

Sources 页面用于管理 metadata authority。

列表默认字段：

- `source_id`
- `display_name`
- `source_type`
- `status`
- `readiness_status`
- `failure_code`
- live browse availability
- dataset grounding count
- related mappings count
- `updated_at`

详情页分区：

- 基本信息：类型、名称、状态、创建 / 更新时间。
- Authority：catalog system、connection 摘要、synthetic catalog。
- Catalog Browser：live schemas / tables / columns 浏览。
- Preview：受限行数的数据预览，用于确认 `dataset.source` 和 `field.expression`。
- Semantic Grounding：引用该 datasource 的 datasets / fields 摘要。
- Mappings：治理该 source 的 mapping 摘要。

用户友好性要求：

- `readiness_status != ready` 时，详情页顶部必须显示失败原因和修复入口。
- catalog browse 使用表格和列详情表达，不要求用户手写 JSON。
- authority connection 可以折叠展示敏感或冗长字段。

### Engines

Engines 页面用于管理 execution authority。

列表默认字段：

- `engine_id`
- `display_name`
- `engine_type`
- `status`
- `readiness_status`
- `failure_code`
- auth mode
- performance class
- related mappings count
- `updated_at`

详情页分区：

- 基本信息：类型、名称、生命周期状态。
- Connection：执行连接摘要。
- Auth：`auth.mode`、username source、fallback username。
- Capabilities：intrinsic capabilities 与 deployment capabilities。
- Policy：allowed step types、required policy support。
- Mappings：指向该 engine 的 mappings。

用户友好性要求：

- auth 区域要明确 DuckDB 忽略 session execution identity，Trino 可使用 username_only。
- engine readiness 是配置校验，不要暗示已经执行在线 `SELECT 1` 探测。

### Mappings

Mappings 是管理员最关键的页面，因为没有 ready mapping 就没有稳定 routing。

列表默认字段：

- `mapping_id`
- source display name / `source_id`
- engine display name / `engine_id`
- priority
- `status`
- `readiness_status`
- `failure_code`
- catalog coverage summary
- `updated_at`

详情页分区：

- Source 与 Engine 摘要。
- Catalog Mappings 表格：authority catalog、execution catalog、default schema。
- Readiness 检查结果：source readiness、engine readiness、type combo、catalog coverage。
- Routing Impact：当前 mapping 影响的 datasource datasets 和可路由表范围。

用户友好性要求：

- active mapping 的 catalog coverage 缺失必须以差异视图展示，例如“datasource 可浏览 catalog A/B，但 mapping 只覆盖 A”。
- `mapping_incomplete`、`mapping_invalid_type_combo`、`mapping_inactive_dependency` 等失败码要显示成可操作解释。
- 创建和编辑 mapping 使用表格行编辑，不要求用户手写 `catalog_mappings` JSON。

### Routing Debugger

Routing Debugger 用于解释 table names 到 engine 的解析路径。

输入：

- table names，多行输入或 tag input。
- 可选 routing intent 摘要，例如 step type、metrics、dimensions、policy hints。

输出：

- resolved / unresolved 状态。
- resolved engine。
- qualified names。
- selection reason。
- `failure_code`。
- routing detail，包括 candidates、selected mapping、readiness blockers。
- capability profile。

用户友好性要求：

- 成功时优先展示“为什么选中这个 engine”。
- 失败时优先展示 blocker，而不是让用户阅读完整 JSON。
- 支持从 Sources、Mappings、Semantic object detail 跳转并预填 table names。

### Governance

Governance 页面分为 Policies 和 Quality Rules。

Policies 默认字段：

- policy name
- policy type
- enabled
- scope 摘要
- updated_at

Quality Rules 默认字段：

- rule name
- rule type
- table name
- severity
- threshold 摘要

用户友好性要求：

- policy 按 aggregate_only、field_mask、row_filter、max_rows 分组或过滤。
- quality rule 按 table name 分组，便于管理员从数据对象角度排查质量约束。
- governance check 可作为调试抽屉，不作为分析结论页面。

### Jobs / Runtime

Jobs 和 runtime status 是只读排障面。

Jobs 页面：

- 支持按 session_id、status 过滤。
- 展示 job_id、session_id、job_type、status、submitted_at、started_at、completed_at、error_message。
- 不设计 submit、cancel、retry 操作。

Runtime 页面：

- session runtime status 展示 overall status、last successful stage、blocked reason、backlog summary。
- artifact runtime status 展示 artifact stage、extractor key、attempt lineage、last failure。
- proposition runtime status 展示 current stage、latest successful stage、publish readiness。

用户友好性要求：

- 明确 runtime status 解释“为什么还没看到结果”，不解释“结论是否成立”。
- 从 session、artifact、proposition 页面进入对应 runtime status，不要求用户手写 ID。

## 业务专家工作流与页面

业务专家的目标是把业务域建成 active + ready 的 semantic layer，让 agent 能围绕稳定语义对象工作。

### 日常工作流

1. 选择业务域或 datasource 范围。
2. 浏览 live schemas / tables / columns，理解事实表、维表和关键字段。
3. 创建或导入 OSI semantic model，先定义 datasets 和 fields。
4. 创建 metrics 和 relationships，让它们引用 datasets / fields。
5. 查看 model readiness blockers。
6. 修复 datasource、relation、field expression 或关系依赖，直到 model ready。
7. 通过一个代表性 typed intent 或 preview-backed workflow 验证对象可被 agent 使用。

### 语义对象总览

语义对象总览按 family 分 tab：

- Semantic Models
- Datasets
- Fields
- Metrics
- Relationships
- Readiness

列表默认字段：

- semantic ref 或 internal id。
- display name / header 摘要。
- `status`
- `lifecycle_status`
- `readiness_status`
- blocker count
- capabilities summary
- dependency count
- updated_at

用户友好性要求：

- 默认过滤出 active + not_ready / stale 的对象，帮助专家优先修复不可用语义。
- 支持按 lifecycle、readiness、name prefix、datasource、dataset、dependency ref 过滤。
- detail=false 的轻量列表作为默认视图；需要合约详情时再进入 detail 页面。

### Readiness Queue

Readiness Queue 是业务专家的默认工作台。

页面内容：

- not_ready / stale 对象队列。
- blocking requirements 聚合。
- dependency refs 与 dependent refs。
- capability 缺口。
- 建议修复入口，例如选择 datasource、更新 `dataset.source`、更新 `field.expression`、activate dependency。

展示方式：

- 左侧按 blocker 类型分组。
- 中间展示受影响语义对象。
- 右侧展示选中对象的 blocker 详情和依赖路径。

用户友好性要求：

- 不只显示“not_ready”，必须显示“为什么 not_ready”。
- 对 stale 对象要说明当前 readiness blocker 同时就是 stale reason。
- 对依赖缺失场景，要提供跳转到依赖对象、dataset 或 datasource browse 的入口。

### 对象详情页

语义对象详情页是建模者审查和修复对象的主页面。

顶部摘要：

- semantic ref / internal id。
- object family。
- lifecycle status。
- readiness status。
- blocker count。
- capabilities summary。

主体分区：

- Header / Identity：名称、描述、版本、所有权信息。
- Typed Contract：对象家族对应的结构化 contract。
- Dependencies：dependency refs。
- Dependents：dependent refs。
- Readiness：blocking requirements、capabilities、readiness explanation。
- Lifecycle Actions：validate、activate、deprecate。

用户友好性要求：

- draft 对象允许编辑；active 对象明确提示 public contract frozen。
- validate 是 check-only，不应显示为持久状态切换。
- activate 不等于 ready；activate 后仍必须展示 readiness_status。
- JSON 高级编辑可以存在，但默认展示结构化表单和可读摘要。

### 建模向导

建模向导用于高复杂对象，不替代完整对象详情页。

优先支持：

- Dataset Wizard：datasource、relation FQN、primary key、fields、preview。
- Metric Wizard：observed dataset、expression、primary time field、grain、additivity。
- Relationship Wizard：from / to dataset、field alignment、cardinality。

用户友好性要求：

- 向导按依赖顺序组织，不让用户先填无法验证的下游字段。
- 每一步都能显示将产生的 typed contract 摘要。
- 完成后进入对象详情页执行 validate / activate。

### Dataset Grounding Browser

Dataset Grounding Browser 服务语义建模，不是管理员 catalog 管理页的重复。

页面内容：

- live schemas / tables / columns。
- datasource-local relation FQN。
- dataset / field grounding 摘要。
- preview 样例。
- readiness 与 routing 摘要。

用户友好性要求：

- 支持从 live table 反向创建 dataset。
- 支持查看该 datasource / relation 关联了哪些 datasets 和 fields。
- routing 或 mapping 不可用时，要提示建模对象即使 active 也可能无法 ready。

## 分析人员工作流与页面

分析人员的目标是理解一次分析过程：agent 做了什么、证据是什么、判断为什么成立、还有哪些缺口。

### 日常工作流

1. 从 Session Inbox 找到自己的分析任务。
2. 查看 session goal、execution_identity、governance boundary 和生命周期。
3. 查看 session state，理解当前 active propositions、latest assessments 和 blocking gaps。
4. 点开 proposition context，查看 seed findings、relevant findings、support / oppose、inference records 和 artifact refs。
5. 查看 runtime status，判断空结果或延迟是未触发、运行中、等待 publish 还是失败。
6. 查看 Evidence Timeline、Artifacts 和 Jobs。
7. 必要时发起受约束的 typed follow-up intent，而不是写 raw SQL。

### Session Inbox

Session Inbox 是分析人员默认入口。

列表默认字段：

- `session_id`
- goal question
- lifecycle status
- `execution_identity.session_user`
- `execution_identity.actor_ref`
- policy / budget 摘要
- active proposition count
- blocking gap count
- runtime overall status
- created_at / updated_at

用户友好性要求：

- 支持按 status、session_id prefix、session_user、时间范围过滤。
- open / closed / aborted 状态要明显区分。
- runtime blocked 或有 blocking gaps 的 session 应优先提示。

### Session Detail

Session Detail 是分析上下文主页。

页面分区：

- Session Root：goal、execution identity、governance、lifecycle。
- Session State：active propositions、latest assessments、blocking gaps、artifact refs。
- Runtime Summary：session runtime status。
- Evidence Timeline：按 step / artifact / finding / proposition 的时间或 lineage 展示进展。
- Related Jobs：该 session 下的 jobs。

用户友好性要求：

- session state 是主读面，runtime status 只作为右侧诊断信息。
- `latest_assessment = null` 时，不直接解释为没有结论；应引导查看 runtime status。
- blocking gaps 应作为任务清单展示，便于判断下一步需要验证什么。

### Proposition Detail

Proposition Detail 是分析人员最重要页面。

页面内容：

- proposition 摘要。
- seed entries。
- relevant findings。
- latest assessment。
- blocking gaps。
- non-blocking gaps。
- applied inference records。
- assessment dependencies。
- artifact refs。
- proposition runtime status。

展示方式：

- 顶部展示判断状态和关键 gap。
- 中部用 evidence sections 区分支持证据、反对证据、缺口和推理记录。
- 右侧展示 provenance、artifact refs 和 runtime status。

用户友好性要求：

- proposition context 必须作为一个证据闭包展示，不要求用户跨多个页面拼接。
- seed entries 和 relevant findings 要分开展示，避免混淆创建时输入和当前评估依据。
- runtime status 只解释进度和失败，不覆盖 latest assessment 的判断。

### Evidence Timeline

Evidence Timeline 用于让人理解 agent 的分析路径。

展示内容：

- session 创建。
- typed intent 提交。
- artifact materialized。
- finding extracted。
- proposition seeded。
- assessment committed。

用户友好性要求：

- 默认按分析链路聚合，而不是逐条日志展示。
- 支持从 timeline 节点跳转到 artifact、finding 或 proposition detail。
- SQL 如果出现在执行审计中，只能作为折叠的 provenance 细节。

### Evidence Inspector

Evidence Inspector 用于查看 artifact 和 finding 细节。

页面内容：

- artifact identity。
- step lineage。
- artifact schema version。
- source lineage。
- projection metadata。
- runtime status。
- finding extraction result。

用户友好性要求：

- artifact 是权威输入，finding 是确定性抽取的事实单元，二者必须区分展示。
- 展示 provenance，不把 provenance 当结论。

### Gap View

Gap View 聚合一个 session 中的 blocking 和 non-blocking gaps。

列表默认字段：

- gap id。
- gap type。
- severity / blocking。
- related proposition。
- requirement summary。
- satisfiable by。
- status。

用户友好性要求：

- blocking gaps 优先排序。
- 支持从 gap 跳转到 proposition detail。
- gap 文案应回答”缺什么证据或前置条件”，而不是只显示内部 ID。

## 导航结构

左侧导航按工作区组织：

```text
Overview

Operations
  Health & Metrics
  Sources
  Engines
  Mappings
  Routing Debugger
  Governance
  Jobs

Semantic Layer
  Readiness Queue
  Entities
  Metrics
  Processes
  Dimensions
  Time
  Enum Sets
  Predicates
  Bindings
  Compiler Profiles

Analysis
  Sessions
  Gaps
  Evidence Search

API Contract
  OpenAPI Index
  Schema Explorer
```

导航原则：

- Overview 只放全局概览，不承载复杂编辑。
- Operations 面向管理员的系统可用性。
- Semantic Layer 面向业务专家的建模和 readiness。
- Analysis 面向分析人员的 session 与 evidence。
- API Contract 面向开发者和高级用户，不能取代主要业务页面。

## v1 范围

### v1 优先交付

管理员：

- 运行总览。
- Sources 列表 / 详情 / live browse / preview / semantic grounding 摘要。
- Engines 列表 / 详情。
- Mappings 列表 / 详情 / catalog mapping 编辑。
- Routing Debugger。
- Governance policies / quality rules。
- Jobs 只读列表 / 详情。

业务专家：

- Semantic object inventory。
- Readiness Queue。
- Semantic object detail。
- Validate / activate / deprecate 操作。
- Dataset Grounding Browser。
- Dataset / Metric / Relationship Wizard。

分析人员：

- Session Inbox。
- Session Detail。
- Session State。
- Proposition Detail。
- Evidence Timeline。
- Evidence Inspector。
- Gap View。

### v1 暂缓

- 真实认证 / RBAC。
- 自由 raw SQL workbench。
- 复杂拖拽式建模画布。
- 类 BI 的报表和仪表盘搭建。
- 任意 DAG 手工编排。
- 在 UI 中直接编辑 `marivo.yaml` 的 source / engine / mapping inventory。
- 恢复旧内置 `/ui` 或 `/admin`。
- 把 Jobs 暴露为 submit / cancel / retry 控制面。

## 前端技术基线

v1 前端建议采用：

- React
- TypeScript
- Vite
- TanStack Query
- Ant Design

工程形态：

- 独立前端应用，通过 Marivo HTTP API 访问后端。
- 不挂回 FastAPI 内置 `/ui` 或 `/admin`。
- API client 应从 OpenAPI 契约生成或受 OpenAPI 契约约束，避免手写漂移。
- 页面数据获取使用 TanStack Query 统一处理 loading、error、cache 和 refetch。
- 复杂表单使用 Ant Design Form，并保留 JSON preview 或 advanced editor。

设计理由：

- Marivo UI 是控制台、建模台和证据阅读台，不是营销站点。
- 页面会大量使用表格、筛选、详情抽屉、状态标签、分步表单和只读 JSON / contract 摘要。
- React + TypeScript 能提供足够的类型约束和组件化能力。
- Ant Design 对密集管理台的表格、表单、状态和布局支持成熟，适合 v1 快速形成完整操作面。

## 验收标准

- UI 设计围绕三类角色的任务闭环展开，而不是按 API 资源平铺。
- 管理员可以从页面设计中看清 source / engine / mapping 是否 ready，以及 routing 为什么失败。
- 业务专家可以从页面设计中看清 semantic object 为什么 not_ready，以及如何补齐依赖。
- 分析人员可以从页面设计中看清 session state、proposition context、证据、缺口和 runtime status 的边界。
- 文档不引入与当前 Marivo 边界冲突的能力假设。
- 文档可作为后续前端实现计划和页面原型设计的基线。
