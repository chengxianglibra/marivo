# Agent Runtime Target Resolution v1 HTTP MCP 适用边界冻结说明

本文定义 `marivo-mcp` 在 Streamable HTTP transport 下的适用边界、准入条件和禁止行为。它是 HTTP MCP 目标解析与本地自动托管的唯一编码依据，实现阶段不得在本文约束之外扩展 HTTP MCP 的本地托管能力。

配置语义见 [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md)。用户心智模型见 [`agent-runtime-target-resolution-v1-scope-note.zh.md`](./agent-runtime-target-resolution-v1-scope-note.zh.md)。workspace root 解析见 [`agent-runtime-target-resolution-workspace-root.zh.md`](./agent-runtime-target-resolution-workspace-root.zh.md)。错误结构见 [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md)。

## 核心立场

**HTTP MCP 默认支持远程显式连接。** 本地自动托管仅在满足本文定义的全部准入条件时才允许。不存在"HTTP MCP 默认也能自动本地托管"的场景。

## 两种 transport 的目标解析差异

| 维度 | stdio transport | HTTP transport |
|------|-----------------|----------------|
| 进程归属 | agent 直接拉起 `marivo-mcp` 子进程 | `marivo-mcp` 作为独立 HTTP 服务运行，agent 通过 URL 连接 |
| 默认模式 | `auto`：有 `base_url` → 远程，无 → 本地自动托管 | `auto`：有 `base_url` → 远程，无 → 必须 workspace guard 通过才允许本地 |
| workspace root 来源 | `MARIVO_WORKSPACE_ROOT` → MCP `roots` → cwd | `MARIVO_WORKSPACE_ROOT` → cwd（无 MCP `roots`） |
| 本地自动托管默认 | 允许 | 不允许；必须通过 workspace guard |
| 客户端 workspace 元数据 | 有（MCP `roots`） | 无（HTTP transport 不传递客户端 workspace 语义） |

## HTTP MCP 本地自动托管的准入条件

HTTP transport 下的 `mode=local` 或 `mode=auto`→`local` 仅在以下三个条件**全部满足**时才允许继续解析本地目标：

### 条件 1：服务端可稳定解析唯一 workspace root

"稳定解析唯一 workspace root"指：

- `MARIVO_WORKSPACE_ROOT` 已设置且有效（非空、绝对路径、存在且为目录）
- 或 HTTP MCP 进程的 cwd 是一个明确的工作区目录

以下情况**不满足**此条件：

- `MARIVO_WORKSPACE_ROOT` 未设置或为空白
- cwd 为系统目录（`/`、`/tmp`、`/var`、`/etc`、用户 home 目录的根层级）
- cwd 不存在或不可访问

### 条件 2：服务端具备本地文件系统访问能力

"本地文件系统访问能力"指：

- `marivo-mcp` HTTP 服务进程可以读写 `<workspace_root>/.marivo/` 目录
- `marivo-mcp` HTTP 服务进程可以调用 `marivo serve-local` 子进程

以下情况**不满足**此条件：

- `marivo-mcp` 运行在容器中且未挂载工作区卷
- `marivo-mcp` 以只读文件系统运行
- `marivo serve-local` 命令不在 `PATH` 中或不可执行

### 条件 3：workspace guard 通过

workspace guard 是 HTTP MCP 启动时对本地自动托管前置条件的校验流程。详见下文。

## workspace guard

### 触发时机

workspace guard 在以下时机执行：

- `marivo-mcp-http` 启动时，若 `mode=local` 或 `mode=auto` 且 `MARIVO_BASE_URL` 缺失
- 不在每次工具调用时重复执行（guard 是启动时一次性检查）

### 校验步骤

```
1. 解析 workspace_root（按 workspace root 解析优先级链）
   - 若解析失败 → 报 workspace_root_required 错误，启动终止

2. 校验 workspace_root 可用性
   a. 检查 <workspace_root>/.marivo/ 是否可写（若不存在则检查 <workspace_root> 是否可写）
      - 若不可写 → 报 local_runtime_start_failed 错误，detail 含 "workspace_not_writable"
   b. 检查 marivo serve-local 是否可调用
      - 若不可调用 → 报 local_runtime_start_failed 错误，detail 含 "serve_local_not_found"

3. guard 通过 → 继续本地运行时发现或启动
```

### guard 失败行为

guard 失败时：

- 不允许静默降级为"无 workspace 的本地模式"
- 不允许静默切换到远程模式
- 必须以明确的 `TargetResolutionError` 终止启动
- 错误信息必须包含 guard 失败的具体原因（workspace 不可写 / serve-local 不可用）

## 禁止行为

1. **不允许默认本地自动托管**：HTTP MCP 的文档、命令帮助、默认行为不得暗示"不提供 `base_url` 也能自动本地托管"。未通过 workspace guard 时，`mode=auto` 且无 `base_url` 必须明确失败。

2. **不允许静默 cwd 回退**：当 `MARIVO_WORKSPACE_ROOT` 未设置时，HTTP MCP 不得静默使用 cwd 作为 workspace root，除非 cwd 通过 workspace guard 的可用性校验。特别地，系统目录不得被接受为 workspace root。

3. **不允许跨工作区状态泄漏**：HTTP MCP 本地自动托管的工作区状态（`.marivo/`、`runtime.json`）必须隔离在准入的 workspace root 下。不同请求不得使用不同的 workspace root。

4. **不允许远程不可达回退本地**：与 stdio transport 一致，`mode=remote` 时远程不可达必须报 `remote_target_unreachable`，不回退本地。

5. **不允许 HTTP MCP 成为第二个 operator 控制面**：HTTP MCP 不暴露本地运行时管理能力（如 `serve-local`、`runtime stop`）作为 MCP 工具。这些操作通过 `marivo core` CLI 完成。

## 推荐的用户接入路径

### 远程显式连接（默认推荐）

```bash
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://team-marivo:8000 \
MARIVO_API_TOKEN=$MARIVO_API_TOKEN \
marivo-mcp-http
```

agent 注册配置：

```json
{
  "mcpServers": {
    "marivo": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### 本地自动托管（需满足准入条件）

```bash
MARIVO_MODE=local \
MARIVO_WORKSPACE_ROOT=/abs/path/to/workspace \
marivo-mcp-http
```

agent 注册配置：

```json
{
  "mcpServers": {
    "marivo": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

注意：本地自动托管场景下，用户仍需显式设置 `MARIVO_WORKSPACE_ROOT`，因为 HTTP transport 无法从客户端获取 workspace 语义。

### `marivo-mcp init` 对 HTTP transport 的处理

`marivo-mcp init` 在检测到 HTTP transport 场景时：

- 默认推荐远程显式连接路径
- 若用户请求本地自动托管，必须在 `init` 输出中包含 `MARIVO_WORKSPACE_ROOT`
- 若用户未提供 `--base-url` 且未提供 `--workspace-root`，`init` 应提示用户补充 workspace root，而不是生成一个无 workspace 的本地配置

## 不变量

1. **HTTP MCP 默认远程**：HTTP MCP 的默认预期用途是远程显式连接。文档和命令帮助应以远程路径为首要示例。

2. **本地需显式准入**：HTTP MCP 的本地自动托管必须通过 workspace guard。不存在"HTTP MCP 自动推断本地模式"的默认路径。

3. **guard 是一次性检查**：workspace guard 在启动时执行一次。通过后，后续工具调用不再重复 guard。guard 不通过则启动终止。

4. **workspace root 不可漂移**：与 workspace root 解析优先级文档一致，HTTP MCP 进程生命周期内 workspace root 不可变更。

5. **HTTP MCP 不暴露运行时管理工具**：`serve-local`、`init-local`、`runtime stop`、`runtime status`、`doctor` 是 `marivo core` CLI 的职责，不作为 MCP 工具暴露。

6. **错误文案遵循用户心智模型**：HTTP MCP 的错误信息使用"远程显式连接"和"本地自动托管"语言，不向用户暴露 `auto|remote|local` 内部 mode 标识符。

## 与其他契约的关系

| 契约 | 本文与其关系 |
|------|-------------|
| [`agent-runtime-target-resolution-v1-scope-note.zh.md`](./agent-runtime-target-resolution-v1-scope-note.zh.md) | 1.1 定义了"HTTP MCP 默认不支持本地自动托管"的产品边界；本文是该边界的编码依据，定义准入条件和 guard 机制 |
| [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md) | 1.2 定义了 transport 配置字段和 mode 解析规则；本文约束 HTTP transport 下 mode 解析的额外前置条件 |
| [`agent-runtime-target-resolution-workspace-root.zh.md`](./agent-runtime-target-resolution-workspace-root.zh.md) | workspace root 解析文档定义了优先级链和 HTTP transport 跳过 MCP roots 的规则；本文定义 HTTP transport 下 workspace root 的准入校验 |
| [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md) | 错误 taxonomy 定义了 `workspace_root_required` 和 `local_runtime_start_failed` 的结构化 schema；本文定义 HTTP MCP 场景下这些错误的触发时机 |
