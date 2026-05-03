# Source / Engine / Mapping 改造实施 Todo Task List

## 概述

本文将 [`docs/service/source-execution-mapping-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/source-execution-mapping-contract.md) 及相关运行时 / 接口文档拆解为一份可直接落地开发的实施清单，目标是在 **保留 HTTP-only 边界、保持 typed binding 继续锚定 source object、显式引入 authority-to-execution mapping** 的前提下，把当前 `source + engine + source-engine binding(namespace)` 模型一次性切到目标态三对象模型：

- `source`：metadata authority
- `execution engine`：runtime execution authority
- `mapping`：authority-to-execution projection contract

术语约定：

- 本文中的 `typed binding` 指 semantic typed binding，继续锚定 `source_object_ref`
- 本文中的 `/bindings` 指当前 source-engine binding HTTP contract，本轮会被 `mapping` 直接替换

一句话结论：

- v1 先做“**schema / 存储收敛 + registry / service / API 补齐 + routing / readiness 改造 + typed binding/runtime compile 对 authority locator 对齐 + 旧 binding 模型一次性删除**”。
- 不做 `/bindings` 兼容层，不做 `binding.namespace` 回读，不做 online migration/backfill，不做灰度双写。
- 任务拆解围绕八个交付面推进：contract 冻结、metadata schema、registry/service、HTTP/API、routing/readiness、旧模型清理、测试与文档。

## 文档依据

- [`docs/service/source-execution-mapping-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/source-execution-mapping-contract.md)
- [`docs/service/execution-auth-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/execution-auth-contract.md)
- [`docs/service/agent-runtime-target-resolution.md`](/Users/lichengxiang/source/oss/marivo/docs/service/agent-runtime-target-resolution.md)
- [`docs/api/sources.md`](/Users/lichengxiang/source/oss/marivo/docs/api/sources.md)
- [`docs/api/engines.md`](/Users/lichengxiang/source/oss/marivo/docs/api/engines.md)
- [`docs/agent-guide.md`](/Users/lichengxiang/source/oss/marivo/docs/agent-guide.md)

## 当前实现对照

当前仓库基线的关键事实：

- `sources` 已收敛为 `authority + sync + intrinsic_capabilities + policy` 读写模型。
- `engines` 已收敛为 `connection + default_namespace + intrinsic_capabilities + deployment_capabilities + policy` 读写模型。
- `source_objects.authority_locator_json` 已落地，sync runtime 已开始冻结 authority locator。
- `source_execution_mappings` 表和相关索引已落地，但尚未成为运行时唯一权威入口。
- `source_engine_bindings.namespace_json`、[`app/registry/binding_registry.py`](/Users/lichengxiang/source/oss/marivo/app/registry/binding_registry.py)、[`app/api/engines.py`](/Users/lichengxiang/source/oss/marivo/app/api/engines.py) 中的 `/bindings` 路由，以及 [`app/routing.py`](/Users/lichengxiang/source/oss/marivo/app/routing.py) 仍在消费旧 binding 模型。
- admin 侧仍暴露 `namespace_json` 表单，routing 仍按 binding namespace 拼接执行侧表名。

因此，下一阶段重点不是“再加一层兼容包装”，而是把已经落地的 schema 基础真正切成 **mapping-only** 主链路，并删除旧 binding 模型残留。

## 实施范围

### 本次必须覆盖

- 落地 `source / execution engine / mapping` 的最小持久化 contract 与读写边界。
- 让 `source_execution_mappings` 成为 source 到 engine 的唯一投影权威对象。
- 删除 `source_engine_bindings`、`binding.namespace`、`/bindings` 路由和所有运行时读写依赖。
- 让 routing、typed binding grounding、runtime compile 全部消费显式 mapping，而不是 `namespace` 猜测。
- 建立 source / engine / mapping 的 validate/readiness 失败面。
- 补齐 API、配置、admin、测试、文档与 metadata reset 说明。

### 本次明确不做

- schema-level rewrite
- table-level override / remap
- 多 execution catalog fan-out
- 新 federation planner contract
- execution auth 全量落地
- MCP 或非 HTTP 新执行边界
- 针对旧 `/bindings` 的兼容回读、双写、灰度和在线迁移

## 交付原则

- 粒度以“单个 owner 可独立完成并验收”为准，避免大而化之的“支持 mapping”。
- 边界以 contract 分层为准，不让 source、engine、mapping、routing、typed binding、sync 职责互相渗透。
- 每个任务都必须有明确交付物和验收标准，避免“代码写了但无法判断是否完成”。
- 本轮允许破坏性重构，因此默认采用“一次性切换 + 显式 reset”策略，不保留双权威或兼容壳层。

## 建议实施顺序

1. T1 冻结一次性切换策略与对象边界
2. T2 收完 metadata schema 与旧表删除边界
3. T3 落 source / engine / mapping registry 与 validate/readiness
4. T4 切 HTTP API、配置加载与 admin/read surface
5. T5 打通 source object authority locator、routing、compile 主链路
6. T6 删除旧 binding 模型残留并收敛 reset 路径
7. T7 补测试矩阵与 golden cases
8. T8 更新文档与收尾

说明：

- T2 已有一部分基础设施落地，后续重点是“让已有 schema 成为唯一权威”而不是继续并行保留旧表。
- T4 和 T5 可以并行推进，但都必须以“`/bindings` 不再存在”为前提。
- T6 不是兼容迁移，而是清理旧路径、重置本地 metadata 和更新测试模板。

## Todo Task List

## 一、Scope 与 Contract 冻结

- [x] 任务 1.1：冻结 v1 三对象边界
  - 交付物：scope note / decision record
  - 关键内容：`source` 只负责 metadata authority；`engine` 只负责 execution；`mapping` 是唯一 authority-to-execution projection contract
  - 验收标准：开发实现不再把 catalog/schema projection 塞回 source 或 engine

- [x] 任务 1.2：冻结 v1 支持矩阵
  - 交付物：support matrix
  - 范围：运行时只支持当前已实现的 `source={trino,duckdb}`、`engine={trino,duckdb}`；未实现类型不做 schema 预留
  - 验收标准：runtime 和文档不误报额外类型已支持

- [x] 任务 1.3：冻结一次性切换策略
  - 交付物：cutover note
  - 关键内容：`source_engine_bindings`、`binding.namespace`、`/bindings`、BindingRegistry、binding-based routing 在同一轮破坏性改造中一起删除；source 到 engine 的外部 contract 直接切到 `/mappings`
  - 验收标准：实现阶段不保留双写、双读或 legacy 回投影

- [x] 任务 1.4：冻结 authority locator 最小字段集
  - 交付物：object identity contract
  - 最小字段：`catalog`、`schema`、`table`
  - 验收标准：source object、typed binding grounding、routing compile 都基于同一 locator 语义

- [x] 任务 1.5：冻结 mapping resolution 顺序与失败面
  - 交付物：resolution note
  - 关键内容：先匹配 `authority_catalog`，再投影 `execution_catalog`，最后只在 authority schema 缺失时使用 `default_schema`
  - 验收标准：`mapping_missing`、`mapping_incomplete`、`mapping_invalid_namespace` 等失败面可直接编码实现

## 二、Metadata Schema 与持久化基础

- [x] 任务 2.1：扩展 `sources` 表以承载 authority 分层
  - 交付物：metadata schema + model note
  - 最小字段：`authority_json`、`intrinsic_capabilities_json`、`policy_json`
  - 验收标准：source 的 authority connection、synthetic catalog、policy 由统一结构表达

- [x] 任务 2.2：扩展 `engines` 表以承载 execution 分层
  - 交付物：metadata schema
  - 最小字段：`default_namespace_json`、`intrinsic_capabilities_json`、`deployment_capabilities_json`、`policy_json`
  - 验收标准：execution runtime 能区分固有能力、部署能力和运营开关

- [x] 任务 2.3：新增 `source_execution_mappings` 表
  - 交付物：metadata schema
  - 最小字段：`mapping_id`、`source_id`、`engine_id`、`priority`、`catalog_mappings_json`、`status`、`created_at`、`updated_at`
  - 验收标准：可唯一表示一个 `source -> engine` 的 catalog projection contract

- [x] 任务 2.4：为 `source_objects` 增加 authority locator 冻结字段
  - 交付物：metadata schema
  - 最小字段：`authority_locator_json`
  - 验收标准：sync 后对象 identity 不再依赖 `fqn` 推断 catalog/schema 归属

- [x] 任务 2.5：建立 schema 级约束与索引
  - 交付物：DDL / index / validate 基础
  - 范围：`(source_id, engine_id)` 唯一、同 mapping 下 `authority_catalog` 不重复、`synthetic_catalog` 一旦落地后禁止无感改写
  - 验收标准：关键不变量优先在存储层或 validate 层被拦住

## 三、Registry / Service / Validation / Readiness

- [x] 任务 3.1：收敛 `SourceRegistry`
  - 交付物：registry/service 改造
  - 关键内容：`register/get/list/update` 返回目标态 source 结构
  - 验收标准：source 读面已能稳定表达 authority、sync、policy、intrinsic facts

- [x] 任务 3.2：收敛 `EngineRegistry`
  - 交付物：registry/service 改造
  - 关键内容：能力分层从单一 `capabilities` 拆到 intrinsic/deployment/policy
  - 验收标准：routing 不再把 operator policy 当成 engine intrinsic fact

- [x] 任务 3.3：新增 `MappingRegistry`
  - 交付物：registry/service
  - 最小能力：create/get/list/update/delete/validate/readiness
  - 验收标准：mapping 成为独立一等对象，不再隐含在 binding service 内部

- [x] 任务 3.4：实现 source validate/readiness
  - 交付物：validate 逻辑
  - 范围：source type 合法、authority connection 合法、无原生 catalog 的 source 必填 `synthetic_catalog`
  - 验收标准：authority identity 不稳定的 source 不能进入 ready

- [x] 任务 3.5：实现 engine validate/readiness
  - 交付物：validate 逻辑
  - 范围：engine type 合法、default namespace 合法、deployment/policy 字段值域稳定
  - 验收标准：routing 只消费 active + ready engine

- [x] 任务 3.6：实现 mapping validate/readiness
  - 交付物：validate + readiness 逻辑
  - 范围：source/engine 存在、类型组合允许、catalog mappings 非空、authority catalog 集合法、execution catalog 合法
  - 验收标准：无完整 mapping 时明确 fail closed

## 四、HTTP API / Config / Read Surface 收敛

- [x] 任务 4.1：定义 mapping API 最小读写面
  - 交付物：HTTP contract
  - 建议最小集：`POST /mappings`、`GET /mappings`、`GET /mappings/{mapping_id}`、`PUT /mappings/{mapping_id}`、`DELETE /mappings/{mapping_id}`
  - 验收标准：operator 可显式治理 mapping，而不是再经过 source-engine binding 壳层

- [x] 任务 4.2：更新 `/sources` 与 `/engines` 响应模型
  - 交付物：API model 变更
  - 关键内容：source 暴露 `authority/sync/policy`；engine 暴露 `default_namespace/intrinsic_capabilities/deployment_capabilities/policy`
  - 验收标准：文档和接口返回一致，不再把扁平旧字段当目标态语义

- [x] 任务 4.3：删除 `/bindings` 路由与 request/response model
  - 交付物：HTTP breaking change
  - 范围：移除 `POST/GET/DELETE /bindings` 与 `GET /sources/{source_id}/engines` 的 binding 语义；必要信息改由 `/mappings` 和 routing detail 暴露
  - 验收标准：对外不再接受或返回 `binding.namespace`

- [x] 任务 4.4：更新 `marivo.yaml` 配置模型
  - 交付物：config schema
  - 范围：显式声明 `sources[].authority`、`engines[].default_namespace`、`mappings[]`
  - 验收标准：配置加载后可直接初始化目标态 registry，不需运行时猜测

- [x] 任务 4.5：更新 admin/read surfaces
  - 交付物：最小管理台/调试读面调整
  - 范围：source/engine 详情页、mapping 详情、routing detail；删除 `namespace_json` 输入框与 binding 详情入口
  - 验收标准：运维可看见 authority catalog、execution catalog、mapping 覆盖与 readiness 状态

## 五、Source Object / Sync / Routing / Compile 主链路

- [x] 任务 5.1：改造 sync 产物，冻结 authority locator
  - 交付物：sync runtime 改造
  - 关键内容：adapter sync 时按 source authority 生成 object identity；对 duckdb 等无原生 catalog 场景写入 `synthetic_catalog`
  - 验收标准：`source_object.authority_locator` 可稳定重放，不受 execution 侧 catalog 变化影响

- [x] 任务 5.2：收敛 table/source object 解析逻辑
  - 交付物：catalog query / routing 读逻辑改造
  - 关键内容：从 `fqn/native_name/parent_id` 推断，升级为优先读取 `authority_locator`
  - 验收标准：多 catalog、synthetic catalog 场景下不再歧义

- [x] 任务 5.3：重写 routing resolution 核心流程
  - 交付物：[`app/routing.py`](/Users/lichengxiang/source/oss/marivo/app/routing.py) 主逻辑改造
  - 顺序：解析 authority locator -> 找 active mapping -> 校验 engine capability/policy -> 解析 execution locator -> 生成 qualified names
  - 验收标准：没有 mapping 时明确报错，不再退回 `namespace` / default catalog 猜测

- [x] 任务 5.4：让 route detail 暴露 mapping 证据
  - 交付物：routing detail 扩展
  - 最小字段：`mapping_id`、`authority_catalog`、`execution_catalog`、`default_schema_applied`、`readiness_blockers`
  - 验收标准：engine 选择和 locator projection 均可解释

- [x] 任务 5.5：打通 typed binding grounding 到 execution compile 的边界
  - 交付物：runtime compile 改造
  - 关键内容：typed binding 继续引用 `source_object_ref`；compile 阶段读取 `authority_locator` 后再经 mapping 生成 execution locator
  - 验收标准：execution-side catalog 变化不要求重写 typed binding

- [x] 任务 5.6：建立 mapping-aware readiness diagnostics
  - 交付物：diagnostics surface
  - 范围：binding grounding 可否被至少一个 mapping 解析、某 metric/process 依赖的 carrier 是否存在 route blocker
  - 验收标准：问题在 compile/readiness 阶段暴露，而不是等执行 SQL 失败

## 六、旧 Binding 模型清理与一次性切换

补充说明：

- 任务 5.5 / 5.6 落地过程中，为了让尚未切到 mapping-only fixture 的 legacy 测试继续可跑，当前代码临时保留了两类兼容逻辑：
  - metric execution preflight 在“没有任何 ready engine 可用”的 legacy 本地 DuckDB 测试路径上允许 direct-execution fallback
  - 对旧 `source_objects` 行仍保留基于 `fqn` / source synthetic catalog 的 authority / execution locator 推断
- 这些兼容逻辑仅用于承接旧测试夹具，不代表目标态 contract；进入一次性切换阶段后必须连同对应旧 fixture / regression 用例一起清理或改写。

- [x] 任务 6.1：删除 `source_engine_bindings` 及其服务层残留
  - 交付物：schema / registry / service 清理
  - 范围：删除 `source_engine_bindings` 表、[`app/registry/binding_registry.py`](/Users/lichengxiang/source/oss/marivo/app/registry/binding_registry.py) 及所有调用链
  - 验收标准：运行时不再存在 source-engine binding 这一平行对象

- [x] 任务 6.2：删除 `binding.namespace` 相关 API / config / admin 字段
  - 交付物：breaking cleanup
  - 范围：删除 `BindingCreateRequest`、`namespace_json` 表单、配置中的旧 binding 输入
  - 验收标准：代码库中不再保留 `binding.namespace` 作为 source-to-engine projection 入口

- [x] 任务 6.3：清理旧测试、模板与共享 fixture 残留
  - 交付物：tests / fixtures cleanup
  - 范围：删除依赖 `source_engine_bindings`、`namespace_json` 的测试数据、模板校验和 admin 断言
  - 验收标准：测试夹具只反映 mapping-only 模型

- [x] 任务 6.4：清理 5.5 / 5.6 为 legacy 测试保留的兼容逻辑，并订正相关测试
  - 交付物：runtime cleanup + test rewrite
  - 范围：删除 metric preflight 的 legacy direct-execution fallback、删除旧 `source_object` locator 推断分支、将依赖这些兼容分支的 observe/attribute/detect/test/regression fixture 全部改写为显式 source + engine + mapping 建模
  - 验收标准：metric execution preflight 完全以 authority locator + active/ready mapping 为唯一权威；测试不再依赖 legacy fallback 才能通过

- [x] 任务 6.5：明确 metadata reset 路径
  - 交付物：operator/developer note
  - 关键内容：现有本地 metadata DB 与测试模板不做在线 backfill；通过 reset / rebuild 进入新模型
  - 验收标准：没有启动期 legacy backfill 分支；开发者可按文档完成 reset

## 七、测试与验收

- [x] 任务 7.1：补 metadata schema 初始化与 reset 测试
  - 交付物：storage / fixture tests
  - 场景：空库初始化、metadata reset 后重建、旧 binding 表不存在时的模板校验
  - 验收标准：schema bootstrap 稳定，不依赖 legacy migration

- [x] 任务 7.2：补 source / engine / mapping registry 单元测试
  - 交付物：unit tests
  - 场景：synthetic catalog 必填、mapping authority catalog 重复、非法 engine/source 组合、readiness fail closed
  - 验收标准：对象不变量稳定

- [x] 任务 7.3：补 routing 主链路测试
  - 交付物：unit/integration tests
  - 场景：single-catalog pass-through、authority->execution catalog remap、default_schema fallback、生效 priority、mapping 缺失失败
  - 验收标准：routing 只依赖 mapping，不依赖 binding namespace

- [x] 任务 7.4：补 sync + authority locator 测试
  - 交付物：integration tests
  - 场景：duckdb synthetic catalog、trino authority catalog、source object 回读 locator
  - 验收标准：identity 与 execution projection 解耦

- [x] 任务 7.5：补 typed binding compile 测试
  - 交付物：binding/runtime tests
  - 场景：binding grounding 保持不变但 mapping 改 execution catalog；compile 仍能正确产出 execution locator
  - 验收标准：typed binding 不泄漏 execution-side locator

- [x] 任务 7.6：准备最小 golden cases
  - 交付物：golden cases 文档或样例集
  - 建议样例：`duckdb->duckdb pass-through`、`trino authority -> trino execution remap`、`mapping missing`、`mapping incomplete`、`binding grounding unresolved`
  - 验收标准：每个样例都绑定一个明确 contract 边界或回归风险

## 八、文档与收尾

- [x] 任务 8.1：更新对外 API 文档
  - 交付物：文档 PR
  - 最少涉及：[`docs/api/sources.md`](/Users/lichengxiang/source/oss/marivo/docs/api/sources.md)、[`docs/api/engines.md`](/Users/lichengxiang/source/oss/marivo/docs/api/engines.md)，新增 `docs/api/mappings.md`
  - 验收标准：HTTP 文档与实现一致，不再出现 `/bindings` 兼容说明

- [x] 任务 8.2：更新服务设计文档交叉引用
  - 交付物：文档 PR
  - 范围：[`docs/service/source-execution-mapping-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/source-execution-mapping-contract.md)、[`docs/service/execution-auth-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/execution-auth-contract.md)、[`docs/service/agent-runtime-target-resolution.md`](/Users/lichengxiang/source/oss/marivo/docs/service/agent-runtime-target-resolution.md)
  - 验收标准：source/engine/mapping/auth/routing 的边界不冲突

- [x] 任务 8.3：更新共享 agent 指南
  - 交付物：[`docs/agent-guide.md`](/Users/lichengxiang/source/oss/marivo/docs/agent-guide.md) 相关段落
  - 验收标准：后续 agent 不会再把 source-engine binding 或 `binding.namespace` 当目标态 contract

## 关键接口与类型变化

- Source public shape 已从扁平 `connection/capabilities/sync_mode` 收敛为 `authority/sync/intrinsic_capabilities/policy`
- Engine public shape 已从扁平 `connection/capabilities` 收敛为 `connection/default_namespace/intrinsic_capabilities/deployment_capabilities/policy`
- `mapping` 将成为唯一 source-to-engine projection object，并直接替换当前 `/bindings`
- `source_object` 继续保留并消费 `authority_locator`
- `binding.namespace` 与 `source_engine_bindings` 将被彻底删除，而不是保留兼容投影

## 关键开发触点

优先关注以下实现落点：

- [`app/storage/schema.py`](/Users/lichengxiang/source/oss/marivo/app/storage/schema.py)
- [`app/storage/sqlite_metadata.py`](/Users/lichengxiang/source/oss/marivo/app/storage/sqlite_metadata.py)
- [`app/registry/source_registry.py`](/Users/lichengxiang/source/oss/marivo/app/registry/source_registry.py)
- [`app/registry/engine_registry.py`](/Users/lichengxiang/source/oss/marivo/app/registry/engine_registry.py)
- [`app/registry/binding_registry.py`](/Users/lichengxiang/source/oss/marivo/app/registry/binding_registry.py)
- 新增 `app/registry/mapping_registry.py`
- [`app/api/sources.py`](/Users/lichengxiang/source/oss/marivo/app/api/sources.py)
- [`app/api/engines.py`](/Users/lichengxiang/source/oss/marivo/app/api/engines.py)
- [`app/api/models.py`](/Users/lichengxiang/source/oss/marivo/app/api/models.py)
- [`app/routing.py`](/Users/lichengxiang/source/oss/marivo/app/routing.py)
- [`app/static/admin/execution-engines.js`](/Users/lichengxiang/source/oss/marivo/app/static/admin/execution-engines.js)
- [`tests/shared_fixtures.py`](/Users/lichengxiang/source/oss/marivo/tests/shared_fixtures.py)

## 验收标准

- 任意 source object 的 identity 都可在不看 execution engine 的前提下稳定确定
- routing 对 catalog 对齐只依赖显式 mapping，不依赖默认 namespace 猜测
- typed binding grounding 不需要写 execution-side locator
- source-engine `/bindings`、`binding.namespace`、`source_engine_bindings` 在 runtime / API / config / admin / tests 中全部消失
- 缺少 mapping、mapping 覆盖不全、synthetic catalog 缺失等问题都在 validate/readiness 阶段明确失败
- metadata reset 路径明确，且不再维护 legacy backfill 分支
- 文档、API、配置、测试全部与目标态一致

## 假设与默认选择

- 默认文件名采用现有 `plan/` 目录风格：`YYYY-MM-DD-...-todo-task-list.zh.md`
- 默认以中文 task list 落地，和现有中文规划文档保持一致
- 默认采用“一次性切换到 mapping-only 模型”的破坏性路线，而不是保留 `/bindings` 兼容层
- 默认现有本地 metadata DB 和测试模板通过 reset / rebuild 进入新模型，不提供在线迁移或回读
- 默认 v1 runtime 仍只要求把当前已实现的 `duckdb/trino` 跑通
