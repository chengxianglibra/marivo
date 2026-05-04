# Agent Runtime Target Resolution 排障说明

本文面向使用 `marivo-mcp` 接入 Marivo 的用户和 operator。它只覆盖最小排障路径：判断当前连接目标、检查本地 runtime、处理远程不可达、理解 HTTP MCP 的本地托管限制。

总体模型见 [`overview.md`](./overview.md)。配置语义见 [`config-contract.zh.md`](./config-contract.zh.md)。错误结构见 [`error-taxonomy.zh.md`](./error-taxonomy.zh.md)。HTTP MCP 边界见 [`http-mcp-boundary.zh.md`](./http-mcp-boundary.zh.md)。

## 先判断当前接入结果

用户侧只需要区分两种结果：

- **本地自动托管**：没有配置远程 `base_url`，`marivo-mcp` 在当前 workspace 下创建或复用本地 Marivo。
- **远程显式连接**：配置了远程 `base_url`，`marivo-mcp` 只连接该远程 Marivo；远程不可达时直接失败，不回退本地。

可以用以下线索判断当前结果：

| 线索 | 本地自动托管 | 远程显式连接 |
|------|--------------|--------------|
| `MARIVO_BASE_URL` | 未设置 | 已设置 |
| `MARIVO_WORKSPACE_ROOT` | 应指向工作区根目录 | 通常不需要 |
| `.marivo/runtime.json` | 应存在或可由首次启动创建 | 不参与目标解析 |
| 典型日志 | `Marivo target resolved: local ...` | `Marivo target resolved: remote ...` |
| 失败原则 | 无 workspace 必须失败 | 远程不可达必须失败 |

不要把 `auto|remote|local` 当作用户需要理解的三种常规模式。它们是运行时内部 mode；用户排障时只判断“本地自动托管”还是“远程显式连接”。

## 本地自动托管排障

本地自动托管依赖 workspace root 和 `<workspace>/.marivo/` 状态。最小检查顺序如下：

1. 确认 `MARIVO_WORKSPACE_ROOT` 指向项目工作区，或 agent / MCP client 能把 workspace root 传给 `marivo-mcp`。
2. 查看 `<workspace>/.marivo/runtime.json` 是否存在。
3. 运行 `marivo runtime status --workspace-root <workspace>`，确认实际 `base_url`、PID 和健康状态。
4. 运行 `marivo doctor --workspace-root <workspace>`，查看配置文件、metadata、manifest、进程和 `/health` 检查。
5. 如果 manifest 无效，按错误提示处理；通常先运行 `doctor`，再决定是否删除 `<workspace>/.marivo/runtime.json` 后重新连接。

`runtime.json` 是本地 runtime 发现契约，不是用户编辑入口。它至少应能说明当前本地 runtime 的 `base_url`、`pid`、`workspace_root`、`config_path` 和 `metadata_path`。字段级 schema 见 [`manifest-schema.zh.md`](./manifest-schema.zh.md)。

常见本地错误：

| 错误码 | 含义 | 最小处理 |
|--------|------|----------|
| `workspace_root_required` | 本地自动托管无法确定工作区 | 设置 `MARIVO_WORKSPACE_ROOT`，或在明确项目目录中启动 |
| `runtime_manifest_invalid` | `.marivo/runtime.json` 损坏或缺字段 | 运行 `marivo doctor`，必要时删除 manifest 后重试 |
| `local_runtime_start_failed` | 本地 daemon 启动或健康检查失败 | 运行 `marivo doctor`，检查日志、端口、配置和权限 |

本地 runtime 命令是轻量运维辅助命令，不是第二套分析接口。分析行为仍通过 HTTP API 和 typed intent 完成。

## 远程显式连接排障

远程显式连接由 `MARIVO_BASE_URL` 或 `marivo-mcp init --base-url ...` 生成的配置决定。只要远程地址被显式配置，失败时就必须报错，不允许静默启动或复用本地 runtime。

最小检查顺序如下：

1. 确认 MCP server 进程环境中设置了正确的 `MARIVO_BASE_URL`。
2. 确认远程 Marivo HTTP 服务的 `GET /health` 可达。
3. 如果远程服务需要认证，确认 `MARIVO_API_TOKEN` 已配置到 MCP server 进程环境。
4. 遇到 `remote_target_unreachable` 时，修复远程地址、网络、服务进程或 token；不要尝试通过删除本地 `.marivo/` 修复。

`remote_target_unreachable` 的关键语义是 fail-closed：它说明远程目标不可达，并且 `marivo-mcp` 没有、也不应该回退到本地自动托管。

## HTTP MCP 排障

Streamable HTTP MCP 的默认发布路径是远程显式连接。也就是说，HTTP MCP server 通常应以远程 Marivo 为目标启动：

```bash
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
marivo-mcp-http
```

HTTP MCP 默认不做本地自动托管，原因是 HTTP transport 不携带客户端 workspace 语义。只有当 HTTP MCP server 进程能够稳定确定唯一 workspace root，并且通过 workspace guard 时，才允许本地自动托管。

本地 HTTP MCP 至少需要：

- 显式 `MARIVO_WORKSPACE_ROOT`
- 服务端可以读写 `<workspace>/.marivo/`
- `marivo serve-local` 可用
- workspace root 不是系统目录、临时目录或任意猜测路径

如果缺少 workspace root，HTTP MCP 本地模式应以 `workspace_root_required` 失败。它不能静默使用任意 cwd，也不能把远程失败回退成本地启动。

## 何时看哪份文档

| 问题 | 文档 |
|------|------|
| 用户接入模型与本地/远程边界 | [`overview.md`](./overview.md) |
| mode、环境变量、冲突处理 | [`config-contract.zh.md`](./config-contract.zh.md) |
| workspace root 解析优先级 | [`workspace-root.zh.md`](./workspace-root.zh.md) |
| `.marivo/` 文件布局 | [`workspace-layout.zh.md`](./workspace-layout.zh.md) |
| `runtime.json` 字段 | [`manifest-schema.zh.md`](./manifest-schema.zh.md) |
| `marivo` CLI 命令、退出码 | [`cli-contract.zh.md`](./cli-contract.zh.md) |
| 错误码与 detail 字段 | [`error-taxonomy.zh.md`](./error-taxonomy.zh.md) |
| HTTP MCP 本地托管限制 | [`http-mcp-boundary.zh.md`](./http-mcp-boundary.zh.md) |
| 安装、注册、smoke 命令 | [`../../marivo-mcp/README.md`](../../marivo-mcp/README.md) |
