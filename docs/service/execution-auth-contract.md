# Execution Engine Authentication Contract

状态：draft design。

本文档定义 Marivo 在 execution engine 侧读取数据时的最小身份信息契约。

本文是服务/运行时设计说明，不是当前 HTTP API 契约，也不是当前 `connection` 字段的逐项实现说明。当前目标不是设计完整的企业认证框架，而是先为已支持的 `duckdb/trino` 收敛出一个最小、清晰、可扩展的边界。

## 背景

当前实现中，execution engine 的连接信息已经同时承载了：

- endpoint 信息
- namespace 默认值
- 用户相关字段，例如 `user`
- 可能的认证材料，例如 `password`、`http_headers`

但在当前版本讨论里，需求已经进一步收敛为：

- Trino 只需要支持 `user` 字段
- 不做用户身份认证
- 不做 token / password / proxy user / delegation 设计
- 需要允许 agent 在 analysis session 级别配置“本次分析使用哪个用户”

在这个约束下，继续保留 `credential_ref`、`delegation_ref`、`identity_forwarding` 这类设计会明显过度。当前最小 contract 应只回答两个问题：

1. engine 侧如何确定 Trino 连接使用的用户名
2. session 侧如何冻结本次分析的用户信息

## 目标

- 支持 agent 在 analysis session 级别配置用户信息
- 支持 Trino 在运行时读取 session 级用户并写入连接 `user`
- 保持 DuckDB 侧 contract 极小，不引入无意义 auth 字段
- 不把用户信息散落到每个 typed intent payload
- 为后续新增 engine 预留扩展点，但不为当前未实现能力预留复杂对象族

## 非目标

- 本轮支持用户身份认证
- 本轮支持 bearer token / OAuth / Kerberos / delegation token
- 本轮支持 proxy user / impersonation
- 本轮引入独立 `engine auth policy` 资源
- 让 typed semantic binding 感知 engine-side principal

## 设计原则

### 1. Session 冻结“本次是谁”

agent 代表哪个用户发起分析，应在 analysis session 边界确定，而不是散落在每个 intent 中重复传递。

### 2. Engine 只声明“如何取 username”

当前 engine 不需要声明复杂 auth mode，只需要说明：

- 是否消费 session 用户
- 若未提供 session 用户，fallback 到哪个默认用户名

### 3. 用户信息不等于认证信息

当前 `session_user` 只是运行时写入 Trino connection `user` 的字段，不代表 Marivo 已完成用户身份认证，也不代表 Trino 一定会基于该用户名执行原生 ACL。

### 4. 先满足当前 Trino 约束，再谈扩展

本轮仅覆盖：

- `duckdb`
- `trino`

### 5. Auth 不参与 source-to-engine projection

execution auth 只决定运行时连接使用哪个 execution identity，例如 Trino connection 的
`user`。它不决定 source object identity，也不决定 authority catalog 如何投影到 execution
catalog。source identity 仍来自 `source_object.authority_locator`，catalog projection 仍只来自
ready mapping。

且 Trino 只支持 username injection，不支持更复杂的 auth material。

## 最小模型

### Layer 1. Execution Engine

`execution engine` 仍然是 runtime execution authority。当前最小 auth 设计只需要描述用户名注入方式。

建议最小 schema：

```json
{
  "engine_id": "eng_...",
  "engine_type": "duckdb | trino",
  "display_name": "string",
  "status": "active | inactive | deprecated",
  "connection": {},
  "default_namespace": {
    "catalog": "string | null",
    "schema": "string | null"
  },
  "deployment_capabilities": {},
  "policy": {},
  "auth": {
    "mode": "none | username_only",
    "username_source": "session_user | fixed",
    "fallback_username": "string | null"
  },
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

#### `auth` 字段语义

- `mode`
  - `none`: engine 不消费任何 session 用户信息
  - `username_only`: runtime 需要为连接生成一个 `user` 字段
- `username_source`
  - `session_user`: 优先从 session 的 `execution_identity.session_user` 读取用户名
  - `fixed`: 永远使用固定 fallback 用户名
- `fallback_username`
  当 session 未提供用户，或 engine 配置为固定用户名时使用。

### Layer 2. Session Execution Identity

`execution identity` 是 analysis session 级上下文，用于冻结“本次分析使用哪个用户”。

推荐挂载点：

- session create

建议最小 schema：

```json
{
  "execution_identity": {
    "session_user": "alice",
    "actor_ref": "agent.alice"
  }
}
```

#### 字段语义

- `session_user`
  本次 analysis session 希望写入 execution engine 的用户名。
- `actor_ref`
  可选字段，用于 Marivo 自身审计、追踪或区分 agent 身份；它不直接参与 Trino 认证。

本轮刻意不引入以下字段：

- `requested_auth_mode`
- `credential_ref`
- `delegation_ref`
- `identity_forwarding`
- `group_refs`
- `tenant_ref`
- `authorization_scope`

这些都超出了“session 级用户名注入”的最小需求。

## 运行时规则

### 1. Trino

当 `engine_type=trino` 且 `auth.mode=username_only` 时，runtime 按以下顺序决定连接里的 `user`：

1. 如果 `auth.username_source=session_user` 且 session 提供了 `execution_identity.session_user`，使用该值
2. 否则若 `auth.fallback_username` 非空，使用 fallback
3. 否则返回明确配置错误

运行时只负责把最终得到的 username 写入 Trino connection：

```json
{
  "connection": {
    "host": "trino.example.com",
    "port": 8443,
    "http_scheme": "https",
    "catalog": "iceberg",
    "schema": "default",
    "user": "alice"
  }
}
```

当前不做：

- password 解析
- token 注入
- proxy principal
- impersonation

### 2. DuckDB

DuckDB 不消费 session 用户信息。

建议约束：

- `auth.mode = "none"`
- `auth.username_source = "fixed"`
- `auth.fallback_username = null`

即使 session 携带了 `execution_identity.session_user`，DuckDB 运行时也忽略它。

## 示例

### 示例 1：Trino 从 session 读取用户名

engine:

```json
{
  "engine_id": "eng_trino_prod",
  "engine_type": "trino",
  "auth": {
    "mode": "username_only",
    "username_source": "session_user",
    "fallback_username": "marivo"
  }
}
```

session:

```json
{
  "execution_identity": {
    "session_user": "alice",
    "actor_ref": "agent.alice"
  }
}
```

runtime 结果：

- Trino connection `user = "alice"`
- `actor_ref` 只写入 Marivo 审计，不参与 Trino 认证

### 示例 2：Trino 使用固定用户名

engine:

```json
{
  "engine_id": "eng_trino_prod",
  "engine_type": "trino",
  "auth": {
    "mode": "username_only",
    "username_source": "fixed",
    "fallback_username": "marivo"
  }
}
```

session:

```json
{
  "execution_identity": {
    "session_user": "alice",
    "actor_ref": "agent.alice"
  }
}
```

runtime 结果：

- Trino connection `user = "marivo"`
- session 里的 `session_user` 不参与 execution
- `actor_ref` 仍可用于审计

### 示例 3：不同 agent 在不同 session 中配置不同用户

同一个 Trino engine：

```json
{
  "engine_id": "eng_trino_prod",
  "engine_type": "trino",
  "auth": {
    "mode": "username_only",
    "username_source": "session_user",
    "fallback_username": "marivo"
  }
}
```

Agent A 创建 session：

```json
{
  "execution_identity": {
    "session_user": "alice",
    "actor_ref": "agent.alice"
  }
}
```

Agent B 创建 session：

```json
{
  "execution_identity": {
    "session_user": "bob",
    "actor_ref": "agent.bob"
  }
}
```

runtime 结果：

- Agent A 对应的 Trino connection `user = "alice"`
- Agent B 对应的 Trino connection `user = "bob"`
- 这只是 session 级用户名注入，不代表系统完成了用户认证

## 与当前实现的关系

当前实现中的：

- `connection.user`
- `connection.password`
- `connection.http_headers`

仍然是混合形态。

若按本文设计收敛，建议方向是：

- `connection.user` 不再由 engine 静态写死，而是优先从 session `execution_identity.session_user` 注入
- `password`、`http_headers` 不作为当前 contract 的一部分继续扩展
- session 级用户信息只出现一次，不在 intent payload 重复出现

## 审计契约

Marivo 至少应审计以下字段：

- `session_id`
- `engine_id`
- `source_id` / `mapping_id`
- `execution_identity.session_user`
- `execution_identity.actor_ref`
- `executed_at`

Marivo 应明确区分：

- `session_user`
  本次写入 execution engine connection 的用户名
- `actor_ref`
  Marivo 自身视角下的调用者 / agent 标识

## 结论

对当前 Marivo 的约束而言，execution auth 设计应继续简化为：

1. engine 只声明是否采用 `username_only`
2. agent 在 analysis session 级别配置 `execution_identity.session_user`
3. runtime 把 `session_user` 写入 Trino connection `user`

这已经足以支持“不同 agent 在不同 session 中配置不同用户信息”，同时避免提前引入 `credential_ref`、`delegation_ref`、proxy user、delegated token 等当前没有实现、也没有必要承诺的复杂设计。
