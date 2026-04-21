# Execution Engine Authentication And Authorization Contract

状态：draft design。

本文档定义 Factum 在 execution engine 侧读取数据时的身份认证与鉴权目标契约。

本文是服务/运行时设计说明，不是当前 HTTP API 契约，也不是当前 `connection` 字段的实现说明。它描述的是下一阶段应收敛到的边界：如何把 execution endpoint、身份策略、调用者身份上下文与运行时凭证解析拆开，而不是继续把它们都塞进 engine connection。

## 背景

当前实现中，execution engine 的连接信息已经同时承载了：

- endpoint 信息
- 静态连接默认值
- 身份材料，例如 `user`、`password`、`http_headers`

这对固定 service account 场景可以工作，但在以下场景会迅速失稳：

- 交互式分析需要按触发用户身份下推到 engine
- engine 支持 impersonation / proxy user / delegated token
- 同一 engine 在系统任务与人工分析任务中应采用不同 auth mode
- routing 需要先判断“当前身份是否可用”，再判断 capability

因此，Factum 需要把 execution auth 收敛为三个分离层次：

1. `execution engine`
2. `engine auth policy`
3. `execution identity context`

以及一个运行时阶段：

4. `credential resolution`

## 目标

- 将 execution endpoint 与身份策略分离
- 将静态 engine 配置与动态调用者身份分离
- 避免在 session、typed intent、semantic object 中持久化 secret 明文
- 支持 service account、delegated user、proxy user、hybrid 几类常见模式
- 让 routing 可以在 capability scoring 前做 auth-aware filtering
- 让审计面记录“谁以什么模式访问了哪个 engine”，而不是记录 secret 本体

## 非目标

- 定义某个具体引擎厂商的完整 auth SDK 接口
- 把 secret manager / vault / token broker 的实现细节纳入 Factum public contract
- 让 typed semantic binding 感知 engine-side principal
- 在本轮定义 row/column policy 的细粒度执行语义
- 把当前 HTTP request transport 认证与 engine-side data-plane 认证混成一层

## 设计原则

### 1. Engine 决定“支持哪些认证方式”

`execution engine` 只声明：

- 可用的 auth mode
- 是否支持 user impersonation
- 是否支持 delegation token
- 是否存在默认 service credential

它不直接持有“本次调用者是谁”的动态事实。

### 2. Session / request 决定“这次是谁”

分析任务必须有一份独立的 `execution identity context`，用于表达：

- 触发者身份
- 触发者组/租户/claims
- 本次调查允许使用的 auth mode
- 是否允许将用户身份下推到 engine

这份上下文应在 session 或 request 边界被冻结，而不是散落在每个 step 的 engine connection 里。

### 3. Runtime 决定“本次具体拿什么凭证”

secret 的解析、换取、续约与注入，属于运行时责任。Factum 不应要求外部调用方在 typed intent payload 中提交 access token、password 或 ticket 明文。

### 4. Secret 用引用，不用明文

只允许在 durable object 中保存：

- `credential_ref`
- `delegation_ref`
- `principal_template`
- `auth_mode`

不允许把以下内容作为 session / step / semantic object 的常规字段持久化：

- bearer token 明文
- database password 明文
- kerberos ticket / refresh token 明文

### 5. Routing 必须是 auth-aware

engine selection 不能只看 capability / performance / mapping。还必须先过滤“对当前 identity 可用”的 engine 候选集。

## 四层模型

### Layer 1. Execution Engine

`execution engine` 仍然是 runtime execution authority，但它的 auth 部分只保留静态能力与默认策略。

建议最小 schema：

```json
{
  "engine_id": "eng_...",
  "engine_type": "trino | spark | duckdb",
  "display_name": "string",
  "status": "active | inactive | deprecated",
  "connection": {},
  "default_namespace": {
    "catalog": "string | null",
    "schema": "string | null"
  },
  "capability_profile": {},
  "auth": {
    "supported_modes": [
      "service_account",
      "delegated_user",
      "proxy_user",
      "hybrid"
    ],
    "default_mode": "service_account",
    "supports_impersonation": false,
    "supports_group_context": false,
    "supports_token_forwarding": false,
    "service_credential_ref": "string | null"
  },
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

#### Engine auth 字段语义

- `supported_modes`
  该 engine 支持的认证/授权模式列表。
- `default_mode`
  当 session/request 未显式指定时使用的默认模式。
- `supports_impersonation`
  engine 是否允许 runtime 以“代理用户”方式执行。
- `supports_group_context`
  engine 是否允许带入 groups / roles / session roles。
- `supports_token_forwarding`
  engine 是否允许把外部 delegation token 下推给 engine connector。
- `service_credential_ref`
  指向 secret manager / runtime credential store 的默认 service credential 引用。

### Layer 2. Engine Auth Policy

`engine auth policy` 用于定义某个 engine 在 Factum 内应如何解释和使用身份。

它是 engine 的受治理配置，而不是用户每次请求都重写的临时参数。

建议最小 schema：

```json
{
  "policy_id": "eap_...",
  "engine_id": "eng_...",
  "status": "active | inactive | deprecated",
  "policy_mode": "service_account | delegated_user | proxy_user | hybrid",
  "principal_mapping": {
    "principal_source": "actor_ref",
    "principal_template": "{actor_ref}",
    "group_source": "group_refs",
    "group_template": "{group_refs}"
  },
  "credential_strategy": {
    "kind": "secret_ref | token_exchange | impersonation_only",
    "credential_ref": "string | null",
    "delegation_ref_required": false
  },
  "authorization": {
    "requires_engine_access_check": true,
    "deny_if_principal_missing": true
  },
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

#### Engine auth policy 回答什么

- 当前 engine 在 Factum 中默认按哪种模式取身份
- 调用者身份如何映射成 engine principal
- 是否需要 delegation token / credential ref
- 若 identity 缺失，是否允许回落到 service account

#### Engine auth policy 不回答什么

- 本次分析到底是谁触发的
- token 明文是什么
- 本次是否允许绕过用户身份强制采用 service account

这些属于 `execution identity context` 或 runtime 决策。

### Layer 3. Execution Identity Context

`execution identity context` 是分析任务级上下文。它不是 engine 配置，而是“当前这次分析是谁发起、允许以什么模式进入数据平面”的事实快照。

最合适的挂载点通常是：

- session create
- 或 request-level context injection

建议最小 schema：

```json
{
  "actor_ref": "user.alice",
  "group_refs": ["group.bi", "group.finance_readers"],
  "tenant_ref": "tenant.finance",
  "auth_mode": "delegated_user",
  "delegation_ref": "deleg_...",
  "allowed_engine_ids": ["eng_trino_prod"],
  "authorization_scope": {
    "data_domains": ["ads", "orders"],
    "region": "cn"
  }
}
```

#### Identity context 字段语义

- `actor_ref`
  当前触发主体。
- `group_refs`
  当前主体的组、角色或权限域。
- `tenant_ref`
  当前租户/域。
- `auth_mode`
  本次请求希望使用的 auth 模式；必须是 engine/policy 支持的模式之一。
- `delegation_ref`
  指向 token broker / credential broker 的短期身份引用。
- `allowed_engine_ids`
  可选的 engine allowlist。
- `authorization_scope`
  可被冻结到 session 内的 authz scope 快照，用于运行时与审计。

### Layer 4. Credential Resolution

credential resolution 不是 public schema object，而是运行时过程。

它负责：

1. 读取 engine 与 auth policy
2. 读取 execution identity context
3. 解析出 `effective_principal`
4. 从 `credential_ref` / `delegation_ref` 获取有效凭证
5. 把凭证注入 execution connection
6. 返回一个只在内存中短暂存活的 execution-ready connection context

这个阶段不得把 secret 明文重新写回 metadata store、session state 或 canonical artifact。

## 推荐的 auth mode

### 1. `service_account`

含义：

- engine 侧总是使用受治理的系统账号
- 用户身份不直接进入 engine principal

适用：

- 定时报表
- 批任务
- 无 per-user engine auth 诉求的内部环境

优点：

- 简单
- 凭证管理稳定

代价：

- engine 无法直接按用户做原生鉴权
- 审计只能依赖 Factum 自身日志，而不是 engine-side principal

### 2. `delegated_user`

含义：

- runtime 依据 `delegation_ref` 或 token exchange，以当前用户身份换取 engine-side credential

适用：

- 交互式分析
- 需要 engine 原生 ACL 生效
- 需要把审计落在用户 principal 上

优点：

- 符合企业数据平台常见期望

代价：

- 需要 token broker / credential exchange
- 凭证生命周期管理更复杂

### 3. `proxy_user`

含义：

- Factum 以 service credential 连接 engine
- 同时在 engine/session 中注入被代理 principal

适用：

- Trino/Spark 允许 proxy user / session user override
- 不希望 Factum 直接掌握用户私有凭证

优点：

- 运行时更稳定
- 不必传递用户密钥本体

代价：

- 依赖 engine 侧代理信任配置

### 4. `hybrid`

含义：

- 对交互式请求使用 delegated/proxy
- 对系统任务回落到 service account

适用：

- 同一 engine 同时服务人工分析与后台任务

## 什么时候带入身份信息

### 配置 execution engine 时

只带静态 auth 能力与默认策略，不带具体调用者身份。

适合在 engine 配置时确定的内容：

- `supported_modes`
- `default_mode`
- `service_credential_ref`
- 是否支持 impersonation / token forwarding
- principal mapping 规则

不适合在 engine 配置时写死的内容：

- 具体用户 id
- 具体 delegation token
- 这次分析的 group/tenant/scope

### 创建分析 session 时

优先在 session create 边界带入 `execution identity context`。

原因：

- session 是 investigation lifecycle 的 authority boundary
- 同一 session 内通常应保持一致的身份/授权语义
- 便于审计“谁发起了这次调查”

适合在 session create 冻结的内容：

- `actor_ref`
- `group_refs`
- `tenant_ref`
- `auth_mode`
- `allowed_engine_ids`
- `authorization_scope`
- `delegation_ref`

### 执行 step / typed intent 时

默认不应在每个 typed intent payload 中重复提交 secret 或完整 identity payload。

step 执行阶段只应允许极少量 override，例如：

- `auth_mode_override`
- `engine_id_override`

且 override 只能在 session 允许的边界内生效。

### Runtime 连接 engine 时

真正的凭证材料只应在 runtime 建立 execution connection 时注入：

- bearer token
- password
- signed ticket
- session header
- proxy principal

它们不应成为 canonical session/schema 的常规持久化字段。

## 推荐的注入方式

### 1. 引用式注入

最推荐。

session / request 中只传：

- `credential_ref`
- `delegation_ref`
- `actor_ref`

runtime 再向 secret manager / token broker 解析。

### 2. Header / session property 注入

适合 Trino / Spark 这类引擎。

例如：

- HTTP Authorization header
- session user / proxy user
- client tags / session properties 中的审计字段

但这些都属于 runtime materialization，不应直接暴露为 typed intent 通用字段。

### 3. 进程内 credential object

运行时可以生成一个只在内存中存在的 credential object：

```json
{
  "effective_principal": "alice",
  "credential_kind": "bearer_token",
  "expires_at": "timestamp"
}
```

该对象仅用于当前 execution，不应持久化。

## Routing 与 Auth 的关系

routing 必须拆成两个阶段：

1. auth-aware candidate filtering
2. capability / cost / policy scoring

建议顺序：

1. 根据 mapping 找到可覆盖目标 object 的 engine 候选
2. 根据 auth policy + identity context 过滤掉当前 identity 不可用的 engine
3. 对剩余候选做 capability / performance / cost / policy scoring
4. 选中最高分 engine

否则会出现：

- 先选出最优 engine
- 再在 execution 阶段才发现当前用户无权访问

这种失败不应是正常主路径。

## 审计契约

Factum 至少应审计以下字段：

- `session_id`
- `engine_id`
- `source_id` / `mapping_id`
- `auth_mode`
- `effective_principal`
- `actor_ref`
- `delegation_kind`
- `credential_kind`
- `authorization_scope_snapshot`
- `executed_at`

Factum 不应在审计日志中记录：

- access token 明文
- password 明文
- refresh token 明文
- ticket 明文

## 与当前实现的关系

当前实现中的：

- `connection.user`
- `connection.password`
- `connection.http_headers`

更接近“runtime auth material + endpoint defaults”的混合体。

后续若演进到本文模型，建议按以下方向收敛：

- endpoint / namespace 默认值保留在 `execution engine.connection`
- 默认 service credential 改为 `service_credential_ref`
- 代理用户 / delegation token 不再直接持久化在 engine connection
- session create 增加 `execution_identity_context`
- routing 增加 auth-aware filtering

## 示例

### 示例 1：交互式分析，使用 delegated user

engine:

```json
{
  "engine_id": "eng_trino_prod",
  "engine_type": "trino",
  "auth": {
    "supported_modes": ["service_account", "delegated_user"],
    "default_mode": "delegated_user",
    "supports_impersonation": false,
    "supports_group_context": true,
    "supports_token_forwarding": true,
    "service_credential_ref": "secret://trino/prod/service"
  }
}
```

session identity context:

```json
{
  "actor_ref": "user.alice",
  "group_refs": ["group.bi"],
  "tenant_ref": "tenant.finance",
  "auth_mode": "delegated_user",
  "delegation_ref": "deleg_7d31",
  "allowed_engine_ids": ["eng_trino_prod"]
}
```

runtime 解析结果：

- `effective_principal = alice`
- 通过 `deleg_7d31` 换取短期 token
- token 仅用于本次 Trino 连接

### 示例 2：后台任务，使用 service account

engine auth policy:

```json
{
  "policy_id": "eap_trino_batch",
  "engine_id": "eng_trino_prod",
  "policy_mode": "service_account",
  "credential_strategy": {
    "kind": "secret_ref",
    "credential_ref": "secret://trino/prod/service",
    "delegation_ref_required": false
  }
}
```

session identity context:

```json
{
  "actor_ref": "system.scheduler",
  "group_refs": ["group.system_jobs"],
  "auth_mode": "service_account"
}
```

### 示例 3：使用 proxy user

engine auth policy:

```json
{
  "policy_id": "eap_trino_proxy",
  "engine_id": "eng_trino_prod",
  "policy_mode": "proxy_user",
  "principal_mapping": {
    "principal_source": "actor_ref",
    "principal_template": "{actor_ref}",
    "group_source": "group_refs",
    "group_template": "{group_refs}"
  },
  "credential_strategy": {
    "kind": "secret_ref",
    "credential_ref": "secret://trino/prod/service",
    "delegation_ref_required": false
  },
  "authorization": {
    "requires_engine_access_check": true,
    "deny_if_principal_missing": true
  }
}
```

runtime 解析结果：

- Factum 使用 service credential 连接 Trino
- 同时把 `actor_ref -> effective_principal` 作为 proxy principal 注入

## 结论

Factum 应把 execution-side 身份认证与鉴权收敛为四层：

1. `execution engine`
2. `engine auth policy`
3. `execution identity context`
4. `credential resolution`

并固定以下边界：

- engine 配置只保留静态 auth 能力与默认策略
- session / request 带入调用者身份上下文
- runtime 解析和注入短期凭证
- routing 在 capability scoring 前先做 auth-aware filtering
- secret 只通过引用与运行时解析进入 execution，不进入 canonical durable payload

这是在企业环境中同时满足“数据平面按用户鉴权”和“Factum 保持稳定可治理 contract”的最小充分设计。
