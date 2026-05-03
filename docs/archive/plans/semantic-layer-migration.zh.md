# Factum Semantic Layer 迁移文档

## 1. 背景与目标

`docs/semantic/` 已经定义了 Factum semantic layer 的新设计规范：语义层不再停留在“metric + entity + mapping + SQL 表达式注册”的旧模型，而是升级为以 **typed semantic objects + typed binding + compiler/IR** 为核心的分析契约体系。

当前仓库中的实现仍以旧模型为主：

- 存储层只有 `semantic_entities`、`semantic_metrics`、`semantic_mappings`
- HTTP API 只覆盖 entity / metric / mapping
- `SemanticService` 只支持 entity / metric / mapping 生命周期
- runtime resolution 只解析 entity / metric
- compiler / IR 仍以 metric-centric SQL 组装为主

本项目当前 **尚未上线**，因此本次迁移采用 **直接迁移到目标状态** 的策略：

- **不考虑向前兼容**
- **不保留旧 contract 作为长期对外接口**
- **不做双写、双读、灰度兼容层**
- **优先得到与 `docs/semantic/` 一致的最终实现**

## 2. 迁移范围

本次迁移覆盖以下层面：

| 层面 | 当前状态 | 目标状态 |
| --- | --- | --- |
| Semantic objects | entity / metric 两类主对象 | entity / metric / process object / dimension / time + enum-set |
| Physical grounding | `semantic_mappings` 非类型化映射 | typed binding contract，显式 carrier / surface / relation |
| API | `/semantic/entities`、`/semantic/metrics`、`/semantic/mappings` | 全量 typed semantic object API + binding / profile surface |
| Runtime resolution | 仅解析 metric / entity | 统一解析 typed refs 与 binding refs |
| Compiler | 以 SQL compile helper 为主 | typed semantic compiler：normalize / validate / expand / derive / IR assemble |
| IR | 以 metric/entity resolution 为主 | 引用型 IR，承载 resolved semantic refs 与 compile report |
| Evidence integration | semantic refs 与 canonical refs 边界不完整 | semantic refs / canonical refs / artifact refs 分层明确 |
| Tests / docs | 只覆盖旧模型 | 覆盖新对象、新 compiler、新 contract 与迁移后文档 |

## 3. 迁移输入与设计依据

本迁移文档以以下资料为准：

### 3.1 新规范来源

- `docs/semantic/overview.md`
- `docs/semantic/metric-v2-schema.zh.md`
- `docs/semantic/process-object-schema.zh.md`
- `docs/semantic/entity-schema-contract.zh.md`
- `docs/semantic/dimension-schema-contract.zh.md`
- `docs/semantic/time-schema-contract.zh.md`
- `docs/semantic/enum-set-schema-contract.zh.md`
- `docs/semantic/typed-binding-contract.zh.md`
- `docs/semantic/evidence-integration.zh.md`
- `docs/semantic/compiler-compatibility-profile.zh.md`
- `docs/semantic/compiler-spec.zh.md`
- `docs/semantic/ir-schema-contract.zh.md`

### 3.2 当前实现基线

- `app/storage/schema.py`
- `app/api/models.py`
- `app/api/semantic.py`
- `app/semantic.py`
- `app/semantic_runtime/`
- `app/analysis_core/ir.py`
- `app/analysis_core/compiler.py`
- `tests/test_semantic.py`
- `tests/test_semantic_runtime.py`
- `docs/api/semantic.md`

## 4. 迁移原则

### 4.1 必须遵守

1. **HTTP-only**：只讨论 Factum 当前 HTTP 架构，不引入 MCP 假设。
2. **typed contracts over raw SQL**：外部与中间层 contract 以类型化语义对象表达，不再以 `definition_sql`、字符串维度数组、非结构化 mapping 作为长期主接口。
3. **semantic / binding / compiler / IR 分层**：
   - semantic object 负责“语义上是什么”
   - binding 负责“如何落到物理载体”
   - compiler 负责“如何组合、校验、扩展、推导”
   - IR 负责“稳定、可 lower 的内部计划表示”
4. **deterministic evidence**：事实抽取保持确定性；模型只用于解释，不用于定义证据结构。
5. **一次性切换到目标状态**：不做兼容模式，不保留旧 contract 作为默认入口。

### 4.2 明确非目标

- 不兼容旧版 semantic API payload
- 不保留 `definition_sql + dimensions[]` 作为 metric 主 schema
- 不继续让 `entity` 承担 process / binding / time capability 混合语义
- 不继续把 `semantic_mappings` 作为长期核心绑定模型
- 不把 compiler validation 延迟到 SQL 执行阶段

## 5. 当前状态与目标差距

### 5.1 存储层

| 文件 | 当前状态 | 目标要求 | 差距 |
| --- | --- | --- | --- |
| `app/storage/schema.py` | 只有 `semantic_entities`、`semantic_metrics`、`semantic_mappings` | 需要支持 process object、dimension、time、enum-set、typed binding、compatibility profile 所需结构 | 核心对象缺失；binding 结构错误；旧 schema 混入实现细节 |

### 5.2 API 与模型层

| 文件 | 当前状态 | 目标要求 | 差距 |
| --- | --- | --- | --- |
| `app/api/models.py` | `EntityCreateRequest`、`MetricCreateRequest`、`MappingCreateRequest` | 需要新增 process object / dimension / time / binding / compatibility profile request/response models | typed object shape 缺失 |
| `app/api/semantic.py` | 只有 entity / metric / mapping 路由 | 需要完整 semantic object + binding surface | HTTP 合同严重落后 |
| `docs/api/semantic.md` | 明确标注当前是 legacy implementation contract | 需要更新到迁移后的目标 surface | 文档与目标规范不一致 |

### 5.3 服务层

| 文件 | 当前状态 | 目标要求 | 差距 |
| --- | --- | --- | --- |
| `app/semantic.py` | 只支持 entity / metric / mapping CRUD 与 publish | 需要统一 semantic object lifecycle、binding validation、profile 生成入口 | 服务层能力不足，且旧字段模型会继续外溢 |

### 5.4 Runtime / Catalog

| 文件 | 当前状态 | 目标要求 | 差距 |
| --- | --- | --- | --- |
| `app/semantic_runtime/catalog.py` | 仅搜索/解析 published entity 与 metric | 需要解析 typed semantic refs、binding refs、process refs、time refs | runtime 只覆盖旧语义对象 |
| `app/semantic_runtime/repository.py` 相关实现 | 仅提供 metric/entity resolution | 需要统一 resolver | typed resolution 缺失 |

### 5.5 Compiler / IR

| 文件 | 当前状态 | 目标要求 | 差距 |
| --- | --- | --- | --- |
| `app/analysis_core/ir.py` | `ResolvedMetricIR`、`ResolvedEntityIR` 为主 | 需要引用型 semantic IR + compile report + typed binding / process / dimension / time resolution | IR 结构过窄 |
| `app/analysis_core/compiler.py` | 主要是 `build_metric_query()` 一类 SQL helper | 需要实现 normalize / validation / expansion / capability derivation / IR assembly | 还不是 semantic compiler |

### 5.6 测试层

| 文件 | 当前状态 | 目标要求 | 差距 |
| --- | --- | --- | --- |
| `tests/test_semantic.py` | 测试 entity / metric / mapping CRUD | 覆盖新对象生命周期、binding、非法组合校验 | 测试面不足 |
| `tests/test_semantic_runtime.py` | 只测 metric/entity resolution | 覆盖 typed refs resolution、compiler input/output、profile/validation | runtime/compiler 测试不足 |

## 6. 总体迁移策略

迁移按 **“先 contract，后 runtime，再 compiler，最后 evidence/read surface 对齐”** 的顺序推进。推荐分为 7 个阶段，每个阶段都形成独立可评审增量，但每个阶段结束后都应保持仓库处于 **仅包含目标态 contract** 的一致状态。

阶段顺序如下：

1. 语义对象与存储模型重建
2. API models 与 HTTP surface 重建
3. SemanticService 与 lifecycle/validation 重建
4. runtime resolution 与 catalog 重建
5. compiler / compatibility profile / IR 重建
6. evidence integration 与下游 read surface 对齐
7. 测试、文档、shared guide 收尾

## 7. 详细迁移步骤

以下步骤是执行顺序，也是最终落地时建议的任务拆分顺序。

### 阶段 1：重建 semantic object 存储模型

**目标**

把存储层从“entity/metric/mapping 三件套”重建为新规范要求的 typed object 存储结构。

**涉及文件**

- `app/storage/schema.py`
- 如有必要，新建 `app/storage/semantic_*.py` 或 repository 文件

**实施步骤**

1. 为以下对象补齐持久化结构：
   - `semantic_entities`
   - `semantic_metrics`（按 metric v2 重塑字段）
   - `semantic_process_objects`
   - `semantic_dimensions`
   - `semantic_time_objects` 或等价 time schema 表
   - `semantic_enum_sets`
   - `semantic_bindings`
   - `compiler_compatibility_profiles`
2. 为每类对象统一 draft / published / deprecated lifecycle 与 revision 规则。
3. 把治理、搜索、质量、catalog metadata 与对象主 contract 分层，避免把非对象本体字段继续塞进 object schema。
4. 明确 `semantic_mappings` 的处理方式：
   - 推荐：删除其“核心绑定模型”角色
   - 如短期保留，也仅作为过渡性内部实现表，并由 typed binding 单向派生，不对外暴露
5. 补齐必要唯一约束、外键、状态约束、ref 完整性约束。

**产出物**

- 新的 metadata schema
- 对象生命周期与 ref 完整性约束

**子任务拆分**

| 子任务 | 规模 | 说明 |
| --- | --- | --- |
| 1.1 定义新对象表与约束 | M | 只处理 DDL，不改 API |
| 1.2 收敛旧 metric/entity 字段 | M | 清理旧字段混杂问题 |
| 1.3 typed binding 存储模型落地 | M | 单独处理 binding、carrier、surface、relation |
| 1.4 compatibility profile 存储落地 | S | profile artifact 独立建模 |

**验证机制**

- 新对象都能在 metadata store 中完成建表
- 约束能阻止非法状态与缺失 ref
- 旧 `semantic_mappings` 不再承担主 contract 角色

### 阶段 2：重建 API models 与 HTTP surface

**目标**

让 HTTP API 与 typed semantic object contract 对齐。

**涉及文件**

- `app/api/models/`
- `app/api/semantic.py`
- 必要时新增 `app/api/compiler.py`
- `docs/api/semantic.md`

**实施步骤**

1. 定义新的 request/response models：
   - entity
   - metric v2
   - process object
   - dimension
   - time
   - enum-set
   - typed binding
   - compatibility profile
2. 把旧 `MetricCreateRequest.definition_sql`、`dimensions: list[str]` 改为目标态字段。
3. 把旧 entity 中混入的 process / time / binding 语义移出。
4. 新增或重写以下路由组：
   - `/semantic/entities`
   - `/semantic/metrics`
   - `/semantic/process-objects`
   - `/semantic/dimensions`
   - `/semantic/time`
   - `/semantic/enum-sets`
   - `/semantic/bindings`
   - `/compiler/compatibility-profiles`
5. 为 publish / list / get / update contract 统一错误语义与状态码。
6. 同步更新 `docs/api/semantic.md`，删掉旧 legacy 合同描述，换成目标态 API。

**产出物**

- 全量 typed semantic HTTP surface
- 与代码一致的 API 文档

**子任务拆分**

| 子任务 | 规模 | 说明 |
| --- | --- | --- |
| 2.1 新建 object request/response models | M | 只改模型与校验 |
| 2.2 新建 object routes | M | 每类对象一组路由 |
| 2.3 新建 binding/profile routes | M | 单独处理 binding 与 compiler artifacts |
| 2.4 更新 API 文档 | S | 以新 surface 为准 |

**验证机制**

- 每类对象都具备 create/get/list/update/publish 的完整路径
- 请求与响应 shape 不再依赖旧 legacy 字段
- 非法 payload 在 API 层可得到明确错误

### 阶段 3：重建 SemanticService 与生命周期治理

**目标**

把服务层从旧 CRUD 扩展为目标态 semantic object orchestration 层。

**涉及文件**

- `app/semantic.py`
- 必要时拆分为 `app/semantic_service/` 目录

**实施步骤**

1. 把 `SemanticService` 拆分为按对象职责划分的方法或子服务。
2. 为每类对象统一实现：
   - create
   - get/list
   - update
   - publish
   - deprecate（如保留）
3. 在服务层落实跨对象校验：
   - metric 与 process object 兼容性
   - dimension 与 enum-set 兼容性
   - binding target 与 source object surface 兼容性
   - time ref 与 binding/time schema 一致性
4. 收敛旧字段写入路径，阻止旧 contract 从服务层继续扩散。
5. 如 profile 采用“发布时生成”，则在 publish 流程中生成 compatibility profile。

**产出物**

- 统一语义对象服务层
- 服务层校验与生命周期治理

**子任务拆分**

| 子任务 | 规模 | 说明 |
| --- | --- | --- |
| 3.1 entity/metric 服务重塑 | M | 从旧字段模型迁到新 contract |
| 3.2 process/dimension/time/enum-set 服务新增 | M | 每类对象一个清晰实现块 |
| 3.3 binding 服务与校验实现 | M | 重点在 typed target 验证 |
| 3.4 publish/profile 逻辑接入 | S | 统一 lifecycle |

**验证机制**

- 每类对象服务都能单独通过单测
- publish 后对象进入 runtime 可见状态
- 跨对象非法组合在服务层被拒绝，而不是执行期失败

### 阶段 4：重建 runtime resolution 与 catalog

**目标**

让 runtime 层能够统一解析 typed semantic refs，而不是只解析 metric/entity 名称。

**涉及文件**

- `app/semantic_runtime/catalog.py`
- `app/semantic_runtime/repository.py`
- `app/semantic_runtime/resolution.py`
- `app/semantic_runtime/semantic_metadata.py`

**实施步骤**

1. 扩展 runtime repository，支持解析：
   - metric refs
   - entity refs
   - process refs
   - dimension refs
   - time refs
   - binding refs
2. 调整 catalog search / resolve，使其返回 typed object 信息，而不是 legacy payload。
3. 让 planner context 依赖新的 typed semantic resolution。
4. 收敛 graph / discovery 中对旧 `semantic_mappings` 的直接依赖。
5. 确保 runtime 读取面只暴露 published objects。

**产出物**

- typed runtime resolver
- 与新 contract 对齐的 catalog/discovery surface

**子任务拆分**

| 子任务 | 规模 | 说明 |
| --- | --- | --- |
| 4.1 repository 扩展 refs resolution | M | 核心解析逻辑 |
| 4.2 catalog search/resolve 重写 | M | 返回目标态对象 |
| 4.3 planner context 对齐 | S | 让 planner 消费新 contract |

**验证机制**

- 所有 typed refs 都可解析为一致结构
- 未发布对象不会进入 runtime 读取面
- catalog resolve 输出不再混杂旧 mapping 语义

### 阶段 5：重建 compiler、compatibility profile 与 IR

**目标**

让 compiler 真正成为 semantic compiler，而不是 SQL 拼装工具。

**涉及文件**

- `app/analysis_core/compiler.py`
- `app/analysis_core/ir.py`
- 必要时新增：
  - `app/analysis_core/validator.py`
  - `app/analysis_core/capability_profiles.py`
  - `app/analysis_core/typed_resolution.py`

**实施步骤**

1. 按 `docs/semantic/compiler-spec.zh.md` 重构 compiler pipeline：
   - normalize typed input
   - resolve refs
   - validate compatibility
   - expand derived requirements
   - derive capabilities
   - assemble IR
2. 按 `docs/semantic/ir-schema-contract.zh.md` 重塑 IR：
   - IR 保留引用与 derived fields，不复制整个 catalog object
   - 新增 process/dimension/time/binding resolution
   - 新增 compile report / validation result / profile usage trace
3. 把 capability derivation 从 object schema 中抽出，由 compiler 统一推导。
4. 把非法组合拒绝放到 compiler 阶段，而不是下沉到 SQL 执行失败。
5. 为 compatibility profile 定义生成与消费逻辑。

**产出物**

- typed semantic compiler
- 新 IR schema
- compatibility profile 消费路径

**子任务拆分**

| 子任务 | 规模 | 说明 |
| --- | --- | --- |
| 5.1 compiler pipeline 重构 | L | 本次迁移最大任务 |
| 5.2 IR schema 重构 | M | 与 compiler 联动 |
| 5.3 compatibility profile 接入 | M | profile 生成与消费 |
| 5.4 capability derivation 落地 | M | 从对象字段推导能力 |

**验证机制**

- compiler 能输出稳定 IR，而不是直接拼 SQL
- 非法 metric/process/dimension/binding 组合被显式拒绝
- capability 来源可追溯到核心字段与 profile，而非冗余配置

### 阶段 6：对齐 evidence integration 与下游读取面

**目标**

让 semantic refs、canonical refs、artifact refs 的边界与读写面一致。

**涉及文件**

- 相关 canonical / evidence runtime 文件
- `docs/api/context-surface.md`
- `docs/api/session-state.md`
- 需要时更新 `docs/agent-guide.md`

**实施步骤**

1. 明确 canonical evidence 中哪些位置持有 semantic refs，哪些位置持有 canonical artifact refs。
2. 禁止用 semantic refs 替代 canonical refs，或反之。
3. 如 compile/runtime 输出会进入 canonical pipeline，补齐 ref translation 规则。
4. 检查 context/state surface，确保只暴露外部可见、语义正确的 ref 结构。

**产出物**

- semantic/evidence ref 分层规则
- 下游 read surface 对齐

**子任务拆分**

| 子任务 | 规模 | 说明 |
| --- | --- | --- |
| 6.1 semantic ref / canonical ref 边界定义 | M | 规则先行 |
| 6.2 compile output 到 evidence 的接线 | M | 重点在 ref translation |
| 6.3 read surface 文档与实现收敛 | S | 保持对外一致 |

**验证机制**

- evidence payload 不混用 semantic refs 与 canonical refs
- context/state surface 的 ref 语义稳定且可解释

### 阶段 7：测试、文档与共享指南收尾

**目标**

确保迁移后的代码、文档、开发指南一致。

**涉及文件**

- `tests/test_semantic.py`
- `tests/test_semantic_runtime.py`
- 新增 compiler / binding / profile 测试
- `docs/api/semantic.md`
- `docs/agent-guide.md`
- 必要时 `README.md`

**实施步骤**

1. 以对象类型为维度补齐 API 测试。
2. 以 runtime/compiler 为维度补齐解析与校验测试。
3. 建立最小端到端路径：
   - 创建 semantic objects
   - 创建 binding
   - 发布 objects
   - 编译 typed intent
   - 得到 IR / evidence-ready result
4. 更新所有共享文档，使其与迁移后实现一致。
5. 删除或重写误导性的 legacy 文档段落。

**产出物**

- 完整测试覆盖
- 对齐后的开发/接口文档

**子任务拆分**

| 子任务 | 规模 | 说明 |
| --- | --- | --- |
| 7.1 API 测试补齐 | M | 每类对象一组测试 |
| 7.2 runtime/compiler 测试补齐 | M | 重点在 typed refs 与 validation |
| 7.3 端到端最小链路测试 | M | 验证迁移完成态 |
| 7.4 文档与 guide 更新 | S | 最后收口 |

**验证机制**

- 新测试覆盖所有新增 contract
- 文档内容与实现完全一致
- 旧 legacy 描述不再误导使用者

## 8. 推荐任务拆分与依赖关系

为了让任务可以直接进入开发，本节把迁移计划继续细化为 **可独立实现、可独立评审、可独立验收** 的开发项。以下清单以 **阶段 1 已完成，当前推进点为阶段 2 / 任务 2.1** 为前提；建议每个开发项控制在一个明确 PR 边界内，不跨越多个阶段目标。

### 8.1 当前推进点与执行约束

- **当前推进点**：已进入 **阶段 2 / 任务 2.1（API models）**。
- **默认前置条件**：阶段 1 的 DDL、约束、typed binding 基础存储已可用；如果阶段 1 仍有遗留问题，应先关闭阻塞项再推进阶段 2。
- **执行规则**：
  - 从本节开始，开发任务默认按“目标 contract 优先”推进，不再为 legacy payload 补兼容入口。
  - 每个任务都应同时给出代码改动、测试改动、文档影响面。
  - 如某任务需要拍板建模方案（例如 process subtype、profile 生成时机），必须先在第 11 节对应决策项上形成结论，再编码。

### 8.2 任务总览（从当前推进点开始）

| 任务 ID | 所属阶段 | 任务名称 | 依赖 | 建议规模 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| S2-01 | 阶段 2 / 2.1 | 公共 API base model 与枚举收敛 | 阶段 1 完成 | S | 已完成 |
| S2-02 | 阶段 2 / 2.1 | entity / metric request-response models 重塑 | S2-01 | M | 已完成 |
| S2-03 | 阶段 2 / 2.1 | process / dimension / time / enum-set models 落地 | S2-01 | M | 已完成 |
| S2-04 | 阶段 2 / 2.1 | binding / compatibility profile models 落地 | S2-01 | M | 已完成 |
| S2-05 | 阶段 2 / 2.2 | object 路由骨架与统一错误语义 | S2-02,S2-03,S2-04 | S | 已完成 |
| S2-06 | 阶段 2 / 2.2 | entity / metric routes 切换到新 contract | S2-05 | M | 已完成 |
| S2-07 | 阶段 2 / 2.2 | process / dimension / time / enum-set routes 落地 | S2-05 | M | 已完成 |
| S2-08 | 阶段 2 / 2.3-2.4 | binding / profile routes 与 API 文档更新 | S2-05 | M | 已完成 |
| S3-01 | 阶段 3 / 3.1 | SemanticService 职责拆分骨架 | S2-06,S2-07,S2-08 | S | 已完成 |
| S3-02 | 阶段 3 / 3.1 | entity / metric 服务迁移到 typed object contract | S3-01 | M | 已完成 |
| S3-03 | 阶段 3 / 3.2 | process / dimension / time / enum-set 服务新增 | S3-01 | M | 已完成 |
| S3-04 | 阶段 3 / 3.3 | binding 服务与跨对象校验实现 | S3-01,S3-03 | M | 已完成 |
| S3-05 | 阶段 3 / 3.4 | publish 生命周期与 profile 生成接线 | S3-02,S3-03,S3-04 | M | 已完成 |
| S4-01 | 阶段 4 / 4.1 | runtime repository typed loader 扩展 | S3-05 | M | 已完成 |
| S4-02 | 阶段 4 / 4.2 | catalog search / resolve 输出重写 | S4-01 | M | 已完成 |
| S4-03 | 阶段 4 / 4.3 | planner context 消费新 typed resolution | S4-02 | S | 已完成 |
| S5-01 | 阶段 5 / 5.1 | compiler normalize / resolve pipeline 重构 | S4-03 | M | 已完成 |
| S5-02 | 阶段 5 / 5.1 | compiler validation / derive 阶段落地 | S5-01 | M | 已完成 |
| S5-03 | 阶段 5 / 5.2 | IR schema、compile report、trace 输出重构 | S5-02 | M | 已完成 |
| S5-04 | 阶段 5 / 5.3-5.4 | compatibility profile 生成与消费闭环 | S3-05,S5-03 | M | 已完成 |
| S6-01 | 阶段 6 / 6.1 | semantic ref / canonical ref 边界固化 | S5-04 | S | 已完成 |
| S6-02 | 阶段 6 / 6.2-6.3 | evidence 接线与 read surface 对齐 | S6-01 | M | 已完成 |
| S7-01 | 阶段 7 / 7.1 | API 对象测试补齐 | S2-08,S3-05 | M | 已完成 |
| S7-02 | 阶段 7 / 7.2 | runtime / compiler / 反例测试补齐 | S4-03,S5-04 | M | 已完成 |
| S7-03 | 阶段 7 / 7.3-7.4 | 最小端到端链路与文档/guide 收口 | S6-02,S7-01,S7-02 | M | 已完成 |

### 8.3 细粒度开发任务列表

#### S2-01 公共 API base model 与枚举收敛

- **目标**：建立新 semantic HTTP contract 的公共基础层，避免后续对象模型重复定义 lifecycle、revision、ref、错误字段。
- **前置依赖**：阶段 1 schema 与对象生命周期枚举已稳定。
- **涉及文件**：
  - `app/api/models/base.py`
  - `app/api/models/__init__.py`
  - 必要时新增共享 validator / enum 模块
- **任务内容**：
  1. 定义所有 object request/response 共享的基础字段与 mixin，例如 `status`、`revision`、`ref`、`created_at`、`updated_at`。
  2. 收敛 draft / published / deprecated 的枚举表达，禁止在各对象 model 中继续散落字符串字面量。
  3. 收敛 API 层共用的 ref 格式、分页/列表字段、错误明细结构。
  4. 清理 `app/api/models/__init__.py` 的导出边界，保证后续 route 只从目标态 models 导入。
- **验收标准**：
  - 所有后续对象 model 都能复用统一 base model / enum，而不是重复声明相同字段。
  - API model 层不存在对 legacy payload 名称的默认兼容字段。
  - `app/api/models/__init__.py` 的导出集合可直接支撑 `app/api/semantic.py` 引入目标态 model。

#### S2-02 entity / metric request-response models 重塑

- **目标**：让 entity / metric API 输入输出完全切换到目标态 contract，去掉旧字段中心化设计。
- **前置依赖**：S2-01。
- **涉及文件**：
  - `app/api/models/entity.py`
  - `app/api/models/metric.py`
  - `app/api/models/_legacy.py`
- **任务内容**：
  1. 重定义 entity create / update / response model，移除混入 process/time/binding 的字段。
  2. 重定义 metric create / update / response model，移除对 `definition_sql`、旧 `dimensions: list[str]` 的主路径依赖。
  3. 对旧字段保留仅限内部迁移过渡引用；如仍需存在，必须明确标注为 legacy 且不再从路由主路径暴露。
  4. 为 entity / metric model 增加 ref、surface、依赖字段的结构化校验。
- **验收标准**：
  - entity / metric request-response model 已能表达目标态语义，且字段命名与设计文档一致。
  - route 层不再需要靠额外手工转换来补足核心字段。
  - `_legacy.py` 不再承担目标态对象导出职责。

#### S2-03 process / dimension / time / enum-set models 落地

- **目标**：补齐阶段 2 缺失的四类对象 model，使 HTTP surface 覆盖完整 typed object 集合。
- **前置依赖**：S2-01。
- **涉及文件**：
  - `app/api/models/process_object.py`
  - `app/api/models/dimension.py`
  - `app/api/models/time.py`
  - `app/api/models/enum_set.py`
- **任务内容**：
  1. 为四类对象分别定义 create / update / response / list item model。
  2. 明确 process object subtype、dimension 与 enum-set 的引用关系、time object 的最小 contract。
  3. 将所有跨字段约束尽量前置到 Pydantic 校验，而不是推迟到 service/runtime 报错。
  4. 为列表响应补齐稳定排序/摘要字段，避免 list surface 与 detail surface 混杂。
- **验收标准**：
  - 四类对象均有完整 model 文件与统一导出。
  - 非法组合（如 dimension 引用无效 enum-set）可以在 model 层或 API 层被显式拒绝。
  - list / detail / update 的字段边界清晰，无同名字段语义漂移。

#### S2-04 binding / compatibility profile models 落地

- **目标**：用显式 typed model 表达 binding 与 compiler artifact，而不是复用 mapping 风格 payload。
- **前置依赖**：S2-01。
- **涉及文件**：
  - `app/api/models/binding.py`
  - `app/api/models/compatibility_profile.py`
- **任务内容**：
  1. 定义 binding create / update / response model，明确 carrier、surface、relation、target object ref 的结构。
  2. 定义 compatibility profile 的读写 model，明确哪些字段为发布产物、哪些字段允许外部请求传入。
  3. 收敛 binding/profile 的 ref、状态与 revision 语义，避免单独特例。
  4. 移除 route/service 对“裸 JSON mapping payload”的主路径依赖。
- **验收标准**：
  - binding 已能表达 source/target/surface/relation 的 typed contract。
  - profile model 能明确区分生成态字段与消费态字段。
  - API 输入不再以 `mapping_json` 作为 binding 主契约。

#### S2-05 object 路由骨架与统一错误语义

- **目标**：为所有 semantic object 路由建立统一行为边界，避免不同对象 route 返回不同错误语义。
- **前置依赖**：S2-02、S2-03、S2-04。
- **涉及文件**：
  - `app/api/semantic.py`
  - 必要时新增 `app/api/compiler.py`
- **任务内容**：
  1. 为 create / list / get / update / publish 统一输入输出与异常映射模式。
  2. 收敛 404、409、422 等错误的触发条件与 detail 结构。
  3. 明确 publish 路径是否统一采用 `POST .../publish`，避免不同对象出现不同风格。
  4. 为 route 层引入共享 helper，减少重复 try/except 和重复参数解析。
- **验收标准**：
  - 所有对象 route 的状态码与错误 detail 结构一致。
  - route 层不再直接拼装 legacy 响应字段。
  - 新增对象路由时可以复用统一骨架，而非复制已有实现。

#### S2-06 entity / metric routes 切换到新 contract

- **目标**：把当前 entity / metric API 从 legacy 模式切换到目标态 model 与 service 调用约定。
- **前置依赖**：S2-05。
- **涉及文件**：
  - `app/api/semantic.py`
  - `app/api/models/entity.py`
  - `app/api/models/metric.py`
- **任务内容**：
  1. 替换 entity / metric create、update、list、get、publish 的请求响应 model。
  2. 移除 route 中对旧字段的转译逻辑，把 contract 对齐责任前移到 model/service。
  3. 对 list 查询参数进行最小必要收敛，避免保留不再需要的 legacy filter。
  4. 校准 route 返回字段，使 list/detail 响应与 docs 保持一致。
- **验收标准**：
  - entity / metric 路径全部使用目标态 models。
  - route 内不再访问 `definition_sql`、旧 `dimensions` 等 legacy 核心字段作为主逻辑。
  - API 响应字段与 `docs/api/semantic.md` 中对应章节一致。

#### S2-07 process / dimension / time / enum-set routes 落地

- **目标**：补齐四类对象的 HTTP 入口，使 semantic object surface 不再只有 entity / metric。
- **前置依赖**：S2-05。
- **涉及文件**：
  - `app/api/semantic.py`
  - `app/api/models/process_object.py`
  - `app/api/models/dimension.py`
  - `app/api/models/time.py`
  - `app/api/models/enum_set.py`
- **任务内容**：
  1. 新增四类对象的 create / list / get / update / publish 路由。
  2. 收敛各路由的路径命名、path param 命名与返回 envelope。
  3. 为跨对象引用字段增加最小 API 层校验与明确错误信息。
  4. 确保路由顺序与导出方式不会影响已有 router 注册逻辑。
- **验收标准**：
  - 四类对象都有完整 CRUD-like + publish 路由。
  - 路径命名与迁移文档目标 surface 一致。
  - 非法 payload 会在 API 层返回明确错误，而不是 500。

#### S2-08 binding / profile routes 与 API 文档更新

- **目标**：完成阶段 2 收口，让 binding / profile 有正式 HTTP surface，并同步更新对外 API 文档。
- **前置依赖**：S2-05。
- **涉及文件**：
  - `app/api/semantic.py`
  - 必要时新增 `app/api/compiler.py`
  - `docs/api/semantic.md`
- **任务内容**：
  1. 新增 `/semantic/bindings` 路由组，覆盖 create / list / get / update / publish。
  2. 新增 `/compiler/compatibility-profiles` 路由组，明确 profile 的读取与生成入口。
  3. 更新 `docs/api/semantic.md`，按新对象分类重写请求/响应示例与错误说明。
  4. 删除或重写文档中仍把当前实现描述为 legacy 默认 contract 的段落。
- **验收标准**：
  - binding / profile 有正式 API surface，且与对象路由风格一致。
  - `docs/api/semantic.md` 不再依赖旧 contract 作为主叙述。
  - 文档中的示例 payload 能直接映射到代码中的 request/response model。

#### S3-01 SemanticService 职责拆分骨架

- **目标**：把当前大而全的 `SemanticService` 先拆出清晰职责边界，为后续对象化迁移降低修改风险。
- **前置依赖**：S2-06、S2-07、S2-08。
- **涉及文件**：
  - `app/semantic.py`
  - 必要时新增 `app/semantic_service/`
- **任务内容**：
  1. 识别 entity、metric、process、dimension、time、enum-set、binding、publish/profile 的职责边界。
  2. 先完成服务层内部结构拆分，再切对象实现，避免边改行为边改结构。
  3. 收敛 metadata 访问入口，减少 route 直接依赖底层 store 细节的机会。
  4. 约定统一的 service-level error 类型或错误返回结构。
- **验收标准**：
  - `SemanticService` 或其替代结构具备清晰按对象划分的职责边界。
  - 后续对象服务迁移无需继续在单个超大文件中追加分支。
  - route 层只依赖服务 contract，不依赖底层 metadata 表细节。

#### S3-02 entity / metric 服务迁移到 typed object contract

- **目标**：让 entity / metric 的 create、update、publish 完全遵循目标态对象契约。
- **前置依赖**：S3-01。
- **涉及文件**：
  - `app/semantic.py` 或 `app/semantic_service/entity.py`
  - `app/semantic.py` 或 `app/semantic_service/metric.py`
- **任务内容**：
  1. 重写 entity / metric 的持久化输入映射，去掉旧 contract 写入路径。
  2. 明确 publish 时哪些字段被冻结、哪些 metadata 允许继续演进。
  3. 把 entity / metric 的 ref 生成逻辑收敛为统一规则。
  4. 为 update 与 publish 增加状态合法性校验。
- **验收标准**：
  - entity / metric 服务不再依赖 legacy 主字段模型驱动存储。
  - draft/published/deprecated 流程在服务层有一致状态校验。
  - publish 后返回对象可被 runtime 层稳定消费。

#### S3-03 process / dimension / time / enum-set 服务新增

- **目标**：补齐四类对象的 service 实现与跨表写入逻辑。
- **前置依赖**：S3-01。
- **涉及文件**：
  - `app/semantic.py` 或新增对象服务文件
- **任务内容**：
  1. 实现四类对象的 create / get / list / update / publish。
  2. 明确 dimension 到 enum-set、time 到 binding / schema 的引用写入规则。
  3. 把对象级别的默认值、状态更新、revision 自增统一在服务层处理。
  4. 为 list / get 输出定义稳定返回结构，避免直接透传底层 row。
- **验收标准**：
  - 四类对象均可通过服务层完成全生命周期操作。
  - 引用关系在服务层被显式验证，不依赖运行时撞错。
  - revision 与状态字段行为一致。

#### S3-04 binding 服务与跨对象校验实现

- **目标**：为 binding 建立真正的 typed validation，而不是仅保存结构化 JSON。
- **前置依赖**：S3-01、S3-03。
- **涉及文件**：
  - `app/semantic.py` 或新增 binding 服务文件
- **任务内容**：
  1. 实现 binding create / update / publish。
  2. 校验 binding target 是否存在、source object surface 是否支持、relation 是否合法。
  3. 定义 binding 与 entity / metric / process / dimension / time 的兼容矩阵。
  4. 禁止服务层继续把 binding 当作 `semantic_mappings` 的别名。
- **验收标准**：
  - binding 非法 target / relation / surface 组合会被服务层显式拒绝。
  - binding 存储与返回结构均为 typed contract。
  - `semantic_mappings` 不再承担 binding 主实现职责。

#### S3-05 publish 生命周期与 profile 生成接线

- **目标**：统一对象发布治理，并接入 compatibility profile 的生成或登记逻辑。
- **前置依赖**：S3-02、S3-03、S3-04。
- **涉及文件**：
  - `app/semantic.py`
  - 可能新增 profile 相关服务文件
- **任务内容**：
1. 统一所有对象的 publish 前校验顺序。
2. 明确 publish 是否触发 compatibility profile 生成；当前采用“显式登记”策略，不在对象 publish 时自动生成 profile。
3. 为 publish 失败提供稳定错误分类，区分字段校验失败、引用缺失、兼容性失败。
4. 确保只有 published 对象进入 runtime 可见面。
- **验收标准**：
  - publish 逻辑对所有对象表现一致。
  - profile 生成/登记逻辑有明确入口和稳定副作用。
  - 未发布对象无法被 runtime / compiler 当作可用对象消费。

#### S4-01 runtime repository typed loader 扩展

- **目标**：把 runtime 读取层扩展为按 typed ref 加载对象，而不是只支持 entity / metric。
- **前置依赖**：S3-05。
- **涉及文件**：
  - `app/semantic_runtime/repository.py`
  - `app/semantic_runtime/semantic_metadata.py`
- **任务内容**：
  1. 为 entity、metric、process、dimension、time、binding 增加 typed loader。
  2. 收敛 published 过滤、revision 读取与 ref 解析逻辑。
  3. 把 runtime 对 metadata row 的拼装统一封装，避免各处重复 query。
  4. 为缺失对象、未发布对象、非法 ref 提供稳定错误类型。
- **验收标准**：
  - runtime repository 能解析所有目标态 typed refs。
  - 所有 loader 默认只返回 published 对象。
  - resolution 输出结构稳定，不混入底层表字段噪音。

#### S4-02 catalog search / resolve 输出重写

- **目标**：让 catalog/discovery 面向外暴露目标态对象摘要与详情，而不是 legacy payload。
- **前置依赖**：S4-01。
- **涉及文件**：
  - `app/semantic_runtime/catalog.py`
  - `app/semantic_runtime/resolution.py`
- **任务内容**：
  1. 重写 search 结果项，按 object kind 返回统一摘要字段。
  2. 重写 resolve 输出，确保返回 typed object 视图而非旧 mapping 拼装结果。
  3. 收敛 catalog 中对 `semantic_mappings` 的直接依赖。
  4. 补齐 search / resolve 的 ref 分类与错误信息。
- **验收标准**：
  - search / resolve 输出与新 semantic object contract 对齐。
  - catalog 输出中不再混杂 legacy mapping 语义。
  - 同类对象在 search 与 resolve 中字段命名保持一致。
- **完成说明**：
  - `search` 已按 `object_kind` 返回统一摘要字段，并覆盖 `entity`、`metric`、`process`、`dimension`、`time`、`binding` 六类 typed objects。
  - `resolve` 已切换为 typed ref 主导，且仅为 `entity` / `metric` 保留裸名称 alias。
  - catalog resolve 输出已收敛为 typed detail envelope，不再暴露 `mappings`、`physical_assets` 或 legacy payload 拼装结果。
  - 已补齐 invalid type filter、非法/缺失 ref、typed object search/resolve 的测试覆盖。

#### S4-03 planner context 消费新 typed resolution

- **目标**：让 planner context 与后续分析链路正式切换到新 runtime contract。
- **前置依赖**：S4-02。
- **涉及文件**：
  - `app/semantic_runtime/resolution.py`
  - planner/context 相关调用点
- **任务内容**：
  1. 识别 planner context 当前依赖的 legacy 语义对象字段。
  2. 改为消费 typed resolution 输出，并删除旧字段适配层。
  3. 确保 planner 不再绕过 runtime repository 直接读取 metadata 表。
  4. 校正 planner 错误信息，使其反映 typed ref/resolution 失败原因。
- **验收标准**：
  - planner context 不再依赖 legacy semantic payload。
  - 语义对象解析失败时，planner 返回的是 typed contract 相关错误。
  - runtime 与 planner 之间只通过 typed resolution contract 通信。
- **完成说明**：
  - `planner-context` 已切换为仅消费 published typed metric/entity contracts，不再依赖 `semantic_metrics` / `semantic_entities` 的 legacy 字段拼装。
  - `metrics[*]` / `entities[*]` 现直接返回 typed semantic object 结构，已移除 `legacy` 适配块。
  - 已同步更新 runtime / API 测试断言与相关文档说明，为阶段 5 compiler typed resolve 重构提供稳定输入面。

#### S5-01 compiler normalize / resolve pipeline 重构

- **目标**：建立 semantic compiler 的前半段流程：typed input 归一化与 ref 解析。
- **前置依赖**：S4-03。
- **涉及文件**：
  - `app/analysis_core/compiler.py`
  - 必要时新增 `app/analysis_core/typed_resolution.py`
- **任务内容**：
  1. 明确 compiler 输入对象的 typed contract。
  2. 拆出 normalize 阶段，把字符串式输入、对象 ref、默认值归一到统一结构。
  3. 接入 runtime resolution，把所有 semantic refs 转为可验证的 resolved object。
  4. 为 normalize / resolve 两阶段输出引入显式中间结构。
- **验收标准**：
  - compiler 前半段不再直接拼装 SQL 所需片段。
  - normalize 与 resolve 的边界清晰，可单独测试。
  - 所有 typed refs 在进入 validate 前均有统一 resolved 表达。
- **完成说明**：
  - 新增 `app/analysis_core/typed_resolution.py`，定义 `NormalizedCompilerRequest` 与 `ResolvedCompilerInputs`，将 compiler 前半段拆分为可单测的 normalize / resolve 两步。
  - `compile_step` 已统一先执行 normalize / resolve，再进入既有 SQL builder；保留原签名与现有 `CompiledQuery` 输出，避免扩散改动到 service / intent 调用面。
  - service 编译入口已默认注入 `SemanticRuntimeRepository`，使 `metric_query` / `aggregate_query` 主路径能消费 published typed metric / time / dimension refs。
  - 对尚未完全 typed 化的 legacy 维度桥接路径，compiler 现记录 `dimension_ref_unresolved` warning 而不直接中断执行，确保现有 intent 链路可继续运行。
  - 已补齐 normalize / resolve 单测，并验证 compiler/time-scope 相关回归路径通过。

#### S5-02 compiler validation / derive 阶段落地

- **目标**：把兼容性检查与派生能力计算提前到 compiler，而不是执行期兜底。
- **前置依赖**：S5-01。
- **涉及文件**：
  - `app/analysis_core/compiler.py`
  - `app/analysis_core/validator.py`
  - `app/analysis_core/capability_profiles.py`
- **任务内容**：
  1. 实现 metric / process / dimension / binding / time 的兼容性校验。
  2. 实现 derived requirements、capabilities、usage trace 的推导逻辑。
  3. 定义 validation result 的稳定结构，避免只抛字符串异常。
  4. 删除对象 schema 中不应保留的 capability 冗余字段依赖。
- **验收标准**：
  - 非法对象组合在 compiler 阶段被显式拒绝。
  - capability 来源可追溯到核心字段与 profile，而非硬编码散点。
  - validation 输出可直接写入 compile report。
- **完成说明**：
  - 新增 `app/analysis_core/validator.py`，把 compiler validation 收敛为稳定结构：`ValidationIssue` / `ValidationResult`，按 request shape、intent support、metric-process compatibility、binding-grounding、dimension compatibility、intent-specific gate 产出可机读诊断。
  - 新增 `app/analysis_core/capability_profiles.py`，统一从 metric/process 核心字段推导 compiler capabilities，并开始只读消费已发布 compatibility profile；profile 缺失与不满足分别走 `COMPILER_PROFILE_MISSING` / `COMPILER_PROFILE_NOT_SATISFIED` 诊断。
  - `compile_step` 主路径已固定为 normalize -> resolve -> validate -> derive -> SQL compile；在不改 `CompiledQuery` 返回签名的前提下，把 `compiler_validation`、`compiler_capabilities`、`compiler_profile_trace`、`compiler_usage_trace` 写入 metadata，为 `S5-03` compile report / IR 重构提供稳定输入。
  - service 编译入口已默认注入 published binding/profile 只读 reader，使 typed metric 主路径在 compiler 阶段即可校验 grounding 与 profile 约束，而不是等执行期兜底。
  - 维度收紧策略按任务决策落地：显式 `dimension.*` 无法解析时在 compiler 硬失败；legacy 名称桥接仍保留 warning，不一次性打断既有 intent 链路。

#### S5-03 IR schema、compile report、trace 输出重构

- **目标**：把 IR 从“拼好的执行载荷”改为“基于引用的中间表示 + 诊断结果”。
- **前置依赖**：S5-02。
- **涉及文件**：
  - `app/analysis_core/ir.py`
  - `app/analysis_core/compiler.py`
- **任务内容**：
  1. 重定义 IR：保留 refs、resolved 摘要、derived fields，而不复制完整 catalog object。
  2. 新增 compile report、validation summary、profile usage trace 的输出结构。
  3. 校正 compiler 输出类型标注与调用点消费方式。
  4. 删除仍假设 IR 是“接近 SQL payload”的旧字段。
- **验收标准**：
  - IR schema 以引用与派生结果为主，而不是 catalog object 镜像。
  - compile report 能解释成功/失败原因与 profile 使用路径。
  - 下游调用点可稳定消费新 IR，不再依赖 legacy 字段。
- **完成说明**：
  - `app/analysis_core/ir.py` 已新增 typed IR/compile contracts：`IrPlan`、`IrBundle`、`CompileReport`、`SemanticCompileError` 以及引用型 input snapshot / node / artifact 结构，IR 主体改为 refs + resolved 摘要，不再镜像完整 catalog object。
  - `CompiledQuery` 已扩展为一等承载 `ir_bundle` / `compile_error` 的 compiler 结果；成功编译时输出稳定 IR bundle，失败时抛出携带 `SemanticCompileError` 的结构化 compiler 异常。
  - `compile_step` 已在 normalize / resolve / validate / derive 之后装配 compile report，收敛 validation summary、profile usage trace、compiler usage trace 与 lowering requirements；`metadata` 仅保留执行链所需摘要，不再承载完整 compiler 诊断主 contract。
  - execution feedback 已能透传结构化 compile error，测试已覆盖新 IR bundle 输出、metadata 收敛与结构化错误映射。

#### S5-04 compatibility profile 生成与消费闭环

- **目标**：让 compatibility profile 真正参与发布与编译流程，形成闭环。
- **前置依赖**：S3-05、S5-03。
- **涉及文件**：
  - profile 存储/服务相关文件
  - `app/analysis_core/compiler.py`
  - `app/analysis_core/capability_profiles.py`
- **任务内容**：
  1. 明确 profile 生成入口（发布时或编译时），并实现对应逻辑。
  2. 在 compiler 中消费 profile，参与 capability derivation 与兼容性判断。
  3. 为 profile 缺失、过期、与对象 revision 不匹配等情况定义错误路径。
  4. 把 profile usage trace 纳入 compile report。
- **验收标准**：
  - profile 不是孤立 artifact，而是已进入 compiler 主路径。
  - profile 缺失或不匹配时，compiler 会返回明确诊断。
  - profile 生成与消费都能绑定到具体对象 revision。
- **完成说明**：
  - compatibility profile 继续采用显式登记 + publish 策略；对象 publish 不自动生成 profile，`docs/api/semantic.md` 与共享 guide 已同步收敛这一入口语义。
  - `compiler_compatibility_profiles` 已新增 `subject_revision`，profile publish 会冻结当前 published subject revision；draft profile 不暴露伪造的 revision 绑定。
  - compiler 消费 profile 时已校验 `subject_revision` 与当前 resolved subject revision 一致性；profile 缺失、revision 不匹配、约束不满足分别走 `COMPILER_PROFILE_MISSING`、`COMPILER_PROFILE_REVISION_MISMATCH`、`COMPILER_PROFILE_NOT_SATISFIED`。
  - `compile_report.profile_usage_trace` 已携带 `subject_ref`、`profile_ref`、`subject_revision`、`resolved_subject_revision` 与应用原因，目标测试已覆盖发布绑定、trace 输出与 revision mismatch 失败路径。

#### S6-01 semantic ref / canonical ref 边界固化

- **目标**：在代码与文档里明确 semantic refs 和 canonical refs 的职责边界。
- **前置依赖**：S5-04。
- **涉及文件**：
  - evidence / canonical 相关实现
  - `docs/api/context-surface.md`
  - `docs/api/session-state.md`
- **任务内容**：
  1. 罗列哪些载荷允许持有 semantic refs，哪些载荷必须转换为 canonical refs。
  2. 为 ref translation 制定单向规则，禁止双向混用。
  3. 在代码中增加必要的边界检查与命名收敛。
  4. 更新相关文档，形成统一术语表述。
- **验收标准**：
  - semantic refs 与 canonical refs 的出现位置有明确边界。
  - 编译输出、runtime 输出、evidence 输入之间的 ref 语义一致。
  - 文档中的术语与代码字段命名一致。
- **完成说明**：
  - 新增 `app/evidence_engine/ref_boundary.py`，统一识别 stable semantic refs 与 canonical typed refs，并提供双向“禁止混用”断言入口。
  - `app/evidence_engine/state_view.py`、`app/evidence_engine/context_view.py` 已在 canonical read payload 返回前执行边界检查，确保 `state/context` 只暴露 canonical refs 与 provenance handles。
  - `app/analysis_core/compiler.py` 已在 `ir_bundle` 与 compiler metadata 产出后执行反向检查，确保 compiler/IR 主 contract 不混入 `finding_ref`、`proposition_ref`、`artifact_refs` 等 canonical evidence refs。
  - 已补齐 ref boundary 单测、read surface 边界测试与 compiler 断言，并同步更新 `docs/api/context-surface.md`、`docs/api/session-state.md`、`docs/agent-guide.md` 的术语与边界说明。

#### S6-02 evidence 接线与 read surface 对齐

- **目标**：完成 compile/runtime 到 evidence 的接线，并确保外部读取面只暴露语义正确的引用。
- **前置依赖**：S6-01。
- **涉及文件**：
  - evidence runtime 相关文件
  - `docs/api/context-surface.md`
  - `docs/api/session-state.md`
  - `docs/agent-guide.md`
- **任务内容**：
  1. 将 compile/runtime 输出中的 semantic refs 按规则翻译到 evidence 所需 ref。
  2. 检查 `session-state`、`context-surface` 是否暴露了不应暴露的 runtime 内部状态或错误 ref 类型。
  3. 校正 API 文档与 guide 中对 read surface 的描述。
  4. 补齐最小验证用例，证明 evidence payload 不混用两套 ref。
- **验收标准**：
  - evidence 接口输入输出不混用 semantic/canonical refs。
  - `docs/api/context-surface.md`、`docs/api/session-state.md`、`docs/agent-guide.md` 三处描述一致。
  - read surface 只暴露外部可见、可解释的引用与状态。
- **完成说明**：
  - 已新增 `step_metadata` 持久化面，并在 compiler 驱动的 step 执行路径中写入 ref-only typed semantic snapshot，供 evidence 通过 `step_ref` 回溯 semantic anchors。
  - `app/evidence_engine/proposition_seeding_run.py` 已优先通过 `artifact -> step_id -> step_metadata` 解析双侧 subject；缺失 step metadata 时仍保留既有 artifact 摘要兜底，不伪造 semantic refs。
  - canonical read surfaces 继续保持 canonical-only：未新增 semantic refs 字段，并同步更新 `docs/api/context-surface.md`、`docs/api/session-state.md`、`docs/agent-guide.md` 对 step lineage metadata 用途的说明。
  - 已补充 step metadata 持久化测试与 proposition seeding 优先使用 step metadata 的验证用例。

#### S7-01 API 对象测试补齐

- **目标**：为阶段 2-3 的所有 object / binding / profile HTTP contract 建立稳定测试。
- **前置依赖**：S2-08、S3-05。
- **涉及文件**：
  - `tests/test_semantic.py`
  - 必要时新增对象级 API 测试文件
- **任务内容**：
  1. 以对象类型为维度补齐 create / list / get / update / publish 测试。
  2. 为非法 payload、非法状态迁移、缺失 ref 等错误路径补齐测试。
  3. 收敛测试构造数据方式，避免每类对象都重复搭建 fixture。
  4. 覆盖 binding 与 profile 的主路径与关键反例。
- **验收标准**：
  - 所有目标态对象路由都有正向与关键反例测试。
  - API 测试断言的是目标态字段与错误语义，而不是 legacy 载荷。
  - 新增对象 contract 变更会直接反映到测试失败。
- **完成说明**：
  - 已移除 legacy `/semantic/mappings` HTTP surface，并同步删除对应 API 测试与文档/UI 入口，统一改为 typed binding 语义。
  - 已补齐 typed semantic object / binding 相关测试覆盖，并将 compiler typed-dimension 测试断言对齐到当前 plain dimension 兼容规范。
  - 已为 legacy 测试夹具补上 test-only typed bridge：直接写入 `legacy_semantic_mappings`，并在需要时自动发布最小 typed entity / metric / dimension / time / binding 合约，保证迁移期测试稳定。
  - 已修复 intent / runtime 依赖测试对 legacy mapping 写路径的残留使用，完成全量回归：`make lint`、`make typecheck`、`make test` 通过。

#### S7-02 runtime / compiler / 反例测试补齐

- **目标**：覆盖 typed resolution、compiler validation、profile 使用和关键失败场景。
- **前置依赖**：S4-03、S5-04。
- **涉及文件**：
  - `tests/test_semantic_runtime.py`
  - `tests/test_semantic_schema.py`
  - 必要时新增 compiler / profile 测试
- **任务内容**：
  1. 为 runtime typed refs resolution 补齐正向与未发布对象反例测试。
  2. 为 compiler normalize / validate / derive / IR 输出补齐分阶段测试。
  3. 为非法 metric/process、dimension/enum-set、binding target/surface 不匹配补齐反例。
  4. 为 profile 缺失、profile revision 不匹配等场景补齐测试。
- **验收标准**：
  - 关键 typed resolution 与 compiler 阶段均有独立测试覆盖。
  - 第 9 节列出的关键反例在测试中都有对应断言。
  - 测试能够区分 schema 错误、service 错误、compiler 错误三个层级。
- **完成说明**：
  - 已在 `tests/test_semantic_runtime.py` 补齐 typed runtime 对非法 ref、未发布 time/dimension/process/binding ref 的拒绝路径，并收紧 catalog bare-name alias 错误断言。
  - 已在 `tests/test_compiler_typed_resolution.py` 补齐 compiler validator 关键错误码覆盖：`COMPILER_PROCESS_REQUIRED`、`COMPILER_METRIC_PROCESS_INCOMPATIBLE`、`COMPILER_PROFILE_NOT_SATISFIED`、`COMPILER_BINDING_MISSING`、`COMPILER_BINDING_INVALID`、`COMPILER_DIMENSION_UNSUPPORTED`、`COMPILER_DIMENSION_TIME_ANCHOR_MISMATCH`，连同既有 profile 缺失 / revision mismatch / typed dimension unresolved 场景形成完整反例集。
  - 已完成仓库校验：`make lint`、`make typecheck`、`make test` 通过。

#### S7-03 最小端到端链路与文档/guide 收口

- **目标**：验证迁移完成态，并收齐所有对外文档与共享开发指南。
- **前置依赖**：S6-02、S7-01、S7-02。
- **涉及文件**：
  - 端到端测试文件
  - `docs/api/semantic.md`
  - `docs/agent-guide.md`
  - 必要时 `README.md`
- **任务内容**：
  1. 建立一条最小端到端链路：创建对象 → 创建 binding → publish → compile typed intent → 输出 IR / evidence-ready result。
  2. 对照实现重新梳理 `docs/api/semantic.md`、`docs/agent-guide.md` 与必要的 README 描述。
  3. 删除或重写误导性 legacy 说明，避免仓库中残留“当前默认 contract 仍是旧版”的信息。
  4. 在文档中标注 semantic/compiler/evidence 三层边界与推荐开发顺序。
- **验收标准**：
  - 至少有一条可运行的端到端测试覆盖迁移后的核心链路。
  - `docs/api/semantic.md`、`docs/agent-guide.md`、相关说明文档已与实现一致。
  - 仓库对外描述中不再把 legacy semantic contract 当作主路径。
- **完成说明**：
  - 已新增 `tests/test_semantic_typed_end_to_end.py`，覆盖最小 typed semantic 闭环：创建并发布 `time/entity/metric/binding`、运行 runtime resolve 与 planner-context、编译 `metric_query` 产出 IR bundle、生成并持久化 `typed_semantic_snapshot`，并断言 semantic/canonical ref 边界未混用。
  - 已更新 `docs/api/semantic.md`、`docs/agent-guide.md`、`docs/api/README.md`、`README.md`，明确 typed semantic object / binding / compiler profile 为主路径，补齐 compiler/evidence handoff 与推荐开发顺序说明。
  - 已清理 `app/static/admin.html`、`app/static/user.html` 中仍把 `definition_sql` 或 legacy `/semantic/mappings` 当主入口的展示，避免仓库内置 UI 继续误导使用者。
  - 已完成仓库校验：`make lint`、`make typecheck`、`make test` 通过。

## 9. 全局验证机制

迁移完成不能只以“代码能运行”作为标准，必须同时通过以下验证。

### 9.1 Schema 验证

- metadata store 能成功初始化所有新表
- 新对象约束、ref 约束、状态约束有效
- 旧 `semantic_mappings` 不再作为主 binding contract

### 9.2 API 契约验证

- 所有 object routes 都有 create/get/list/update/publish 路径
- payload 中不再出现应由 compiler 派生的 capability 冗余字段
- 非法 payload 会返回明确错误，而不是静默降级

### 9.3 Service 验证

- 服务层能阻止跨对象非法组合
- publish 生命周期一致
- typed binding 校验失败能提供明确错误信息

### 9.4 Runtime 验证

- runtime 只能解析 published objects
- typed refs resolution 输出稳定
- planner context 使用新语义对象，而不是 legacy 载荷

### 9.5 Compiler 验证

- compiler 输入是 typed contract
- compiler 输出是稳定 IR / compile report
- 非法组合在 compiler 阶段被拒绝
- capability 从核心字段与 compatibility profile 推导

### 9.6 Evidence Integration 验证

- semantic refs 与 canonical refs 不混用
- compile/runtime 输出接入 evidence pipeline 时 ref 语义一致

### 9.7 测试验证

- `pytest` 覆盖 API、runtime、compiler、binding、profile
- 至少存在一条端到端最小路径测试
- 关键反例测试齐全：
  - 非法 metric/process 组合
  - 无效 dimension/enum-set 组合
  - binding target 与 source object surface 不匹配
  - 未发布对象进入 runtime/compile

### 9.8 文档一致性验证

- `docs/semantic/` 仍作为设计来源
- `docs/api/` 更新为迁移后 HTTP surface
- `docs/agent-guide.md` 与实现一致

## 10. 阶段验收标准

### 阶段 1 验收

- 新对象表存在
- 旧主 schema 不再阻碍目标态 contract

### 阶段 2 验收

- HTTP API 已完整覆盖目标态对象与 binding/profile

### 阶段 3 验收

- 服务层已支持所有对象与生命周期，并具备基本校验

### 阶段 4 验收

- runtime 能解析 typed refs 并产出稳定 resolved object

### 阶段 5 验收

- compiler 已从 SQL helper 升级为 semantic compiler
- IR 已切换到引用型 schema

### 阶段 6 验收

- semantic refs / canonical refs 边界已经在实现与文档中统一

### 阶段 7 验收

- 测试覆盖与文档更新完成，仓库对外描述与实现一致

## 11. 风险与关键决策

迁移执行前应优先拍板以下事项，否则容易在中途返工：

1. **process object 的建模方式**
   - 单表多 subtype 还是 subtype 分表
2. **dimension 与 enum-set 的关系**
   - `enum_set_ref` 是条件必填还是可选
3. **typed binding 的最小 contract**
   - field surface / relation surface / time surface 的最小可发布粒度
4. **compatibility profile 的生成时机**
   - 发布时预计算，还是 compile 时按需生成
5. **旧 `semantic_mappings` 的最终处理**
   - 删除、保留内部兼容表，还是迁移为 binding 派生物
6. **metric v2 对 `definition_sql` 的替代方案**
   - 必须在开始重构 API 与 compiler 前统一

建议先形成简短决策记录，再进入实际编码。

## 12. 建议执行顺序

如果只保留一个最小执行顺序，按下面走：

1. 先定 object schema / binding schema / profile schema
2. 再改 API models 与 HTTP routes
3. 再改服务层
4. 再改 runtime resolution
5. 再改 compiler / IR
6. 最后收 evidence、tests、docs

这个顺序能最大程度避免“上层 contract 反复推翻下层实现”。

## 13. 完成定义

本迁移在以下条件同时满足时才算完成：

- 新 semantic layer 主对象、binding、compiler、IR 已全部按目标态落地
- 旧 semantic contract 不再作为默认或主路径存在
- runtime / compiler / evidence 的 ref 分层正确
- 测试覆盖新路径与关键错误路径
- `docs/api/`、`docs/agent-guide.md`、相关说明文档全部同步

在此之前，不应宣布 semantic layer 迁移完成。
