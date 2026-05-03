---
status: completed
canonical-path: spec/service/data-plane/ (pending update for datasource merge)
created: 2026-04-30
---

# Datasource 合并设计：Source + Engine + Mapping → Datasource

**Date:** 2026-04-30
**Status:** Approved

## 1. 目标

将当前 source / engine / mapping 三对象模型简化为单一 datasource 对象，消除跨对象投影复杂度。

核心动机：

- 不支持跨 source 联邦查询，source 与 engine 始终 1:1 配对
- 当前 mapping 仅允许同类型组合（duckdb→duckdb、trino→trino），投影层无实际价值
- 三对象模型的管理复杂度高于业务需求

## 2. 合并后的 Datasource 对象

```json
{
  "datasource_id": "ds_...",
  "datasource_type": "duckdb | trino",
  "display_name": "string",
  "connection": {
    // Trino: host, port, http_scheme, catalog, schema, user, ...
    // DuckDB: path
  },
  "sync_mode": "selected | all | none",
  "policy": {
    "allow_live_browse": true,
    "allow_sync": true,
    "allow_identity_reuse": false
  },
  "status": "active | inactive | deprecated",
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### 字段语义

- `datasource_id`：Marivo 内部稳定标识
- `datasource_type`：数据源类型，隐含 capabilities（只读派生，不在 API 写面出现）
- `display_name`：人类可读名称
- `connection`：连接参数，承载元数据同步与查询执行的连接信息
- `sync_mode`：元数据同步范围策略（selected / all / none）
- `policy`：operator 控制面，表达"允许 Marivo 暴露/使用什么"
- `status`：生命周期状态

### policy 字段

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `allow_live_browse` | bool | true | 是否允许实时浏览 catalog |
| `allow_sync` | bool | true | 是否允许执行 sync |
| `allow_identity_reuse` | bool | false | 是否允许分析时复用连接身份信息 |

## 3. allow_identity_reuse 运行时行为

`allow_identity_reuse` 替代当前 engine auth contract（auth.mode / username_source / fallback_username）。

### datasource_type=trino

| allow_identity_reuse | session 提供 session_user | 运行时行为 |
|---|---|---|
| `true` | 是 | Trino connection.user = session_user |
| `true` | 否 | Trino connection.user = connection 中的 user（如有） |
| `false`（默认） | 是 | Trino connection.user = session_user |
| `false` | 否 | **失败**，报 `session_user_missing` |

### datasource_type=duckdb

- `allow_identity_reuse` 不适用，创建/更新时静默忽略该字段（不报错，不存储）
- session 即使提供 session_user 也不报错，runtime 忽略

### 安全语义

默认 `false` 意味着默认要求 session 提供身份信息。只有 operator 显式设置为 `true` 才允许复用连接身份。

### 当前 engine auth 场景映射

| 当前 engine auth 配置 | 新 datasource policy |
|---|---|
| `auth.mode=none`（DuckDB） | 不适用，字段被忽略 |
| `auth.mode=username_only, username_source=session_user` | `allow_identity_reuse=false` |
| `auth.mode=username_only, username_source=fixed, fallback_username=marivo` | `allow_identity_reuse=true`，connection.user 写固定用户名 |

## 4. 删除的对象与字段

### 删除的独立对象

- `mapping`（source_execution_mappings 表、mapping_registry、/mappings API 全部删除）

### 删除的字段

| 来源 | 删除字段 | 原因 |
|---|---|---|
| source.authority | `catalog_system` | 由 datasource_type 隐含 |
| source.authority | `synthetic_catalog` | 不再区分 authority/execution catalog |
| engine | `auth` 整体 | 由 policy.allow_identity_reuse 替代 |
| engine | `default_namespace` | 直接用 connection 中的 catalog/schema |
| engine | `intrinsic_capabilities` | 由 datasource_type 派生 |
| engine | `deployment_capabilities` | 当前无实际消费者 |
| engine | `policy`（allowed_step_types, required_policy_support） | 当前无实际消费者 |
| source | `intrinsic_capabilities` | 由 datasource_type 派生 |

### 保留字段迁移

| 原字段 | 新位置 |
|---|---|
| source.authority.connection | datasource.connection |
| source.sync.mode | datasource.sync_mode |
| source.policy.allow_live_browse | datasource.policy.allow_live_browse |
| source.policy.allow_sync | datasource.policy.allow_sync |

## 5. Catalog Identity 与 Routing

### Catalog Identity

删除 mapping 和 synthetic_catalog 后，catalog identity 简化：

- **Trino datasource**：source object 的 authority_locator.catalog = Trino 连接中配置的 catalog
- **DuckDB datasource**：source object 的 authority_locator.catalog = DuckDB 原生 catalog 名（即 `main`）。注意：当前已 sync 的 DuckDB source object 的 authority_locator.catalog 存储的是 synthetic_catalog 值（如 `duckdb_local`），需要重新 sync 以更新为原生 catalog 名
- 执行时直接使用同一个 catalog，无需投影

### Routing 简化

| | 当前 | 新 |
|---|---|---|
| 路由路径 | source object → 找 mapping → 找 engine → 解析 execution catalog | source object → 所属 datasource → 直接执行 |
| catalog 解析 | 需要 mapping 的 catalog_mappings 投影 | authority_locator 直接作为 execution locator |
| 失败面 | mapping_missing, mapping_incomplete, mapping_invalid_type_combo, mapping_inactive_dependency | 不存在 mapping 相关失败面 |

### typed binding grounding

- 继续锚定 source object
- 编译阶段直接用 authority_locator 作为 execution locator
- 不再需要 mapping 投影步骤

## 6. API 变化

### 新端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/datasources` | 创建 datasource |
| GET | `/datasources` | 列出 datasource |
| GET | `/datasources/{id}` | 获取 datasource |
| PUT | `/datasources/{id}` | 更新 datasource |
| DELETE | `/datasources/{id}` | 删除 datasource |
| POST | `/datasources/{id}/sync` | 触发同步 |
| GET | `/datasources/{id}/browse/catalogs` | 浏览 catalog |
| GET | `/datasources/{id}/browse/schemas` | 浏览 schema |
| GET | `/datasources/{id}/browse/tables` | 浏览 table |
| POST | `/datasources/{id}/preview` | 预览表数据 |

### 删除端点

`/sources`、`/engines`、`/mappings` 全部删除。

### Readiness 简化

- datasource readiness = 连接可用 + status=active
- 失败面：`datasource_inactive`、`datasource_invalid_type`、`datasource_invalid_connection`
- 不再有跨对象依赖传播

### 删除依赖

- 删除 datasource 只需检查 typed binding
- 不再检查 mapping 依赖

## 7. 现有 Spec 处理

- `spec/service/data-plane/source-engine-mapping-contract.md` — 直接删除
- `spec/service/data-plane/execution-auth-contract.md` — 直接删除
- 新建 datasource spec 覆盖所有行为

## 8. 不变的部分

- `source_objects` 表保留，`source_id` 列重命名为 `datasource_id`
- authority_locator 语义不变（catalog/schema/table），只是 catalog 来源不再经过 mapping
- typed binding grounding 不变，继续锚定 source object
- session execution_identity 不变（session_user, actor_ref）
- 审计事件框架不变，只是 mapping 相关事件删除
