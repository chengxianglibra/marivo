# Agent Runtime Target Resolution v1 Workspace Root 解析优先级冻结说明

本文是 workspace root 解析的唯一编码依据。它定义解析优先级链中每一步的精确语义、有效条件和边界条件，实现阶段不应在调用点二次猜测或补充隐式默认值。

配置字段定义与 mode 解析规则见 [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md)。用户心智模型见 [`agent-runtime-target-resolution-v1-scope-note.zh.md`](./agent-runtime-target-resolution-v1-scope-note.zh.md)。

## 适用场景

workspace root 解析仅在以下场景执行：

- `mode=local`
- `mode=auto` 且 `MARIVO_BASE_URL` 缺失或为空（即降级为 local）

`mode=remote` 时不解析 workspace root，`MARIVO_WORKSPACE_ROOT` 已设置也不使用。

## 解析优先级链

当 workspace root 需要解析时，按以下优先级依次尝试，取第一个有效值：

| 优先级 | 来源 | 精确语义 | 有效条件 |
|--------|------|----------|----------|
| 1（最高） | `MARIVO_WORKSPACE_ROOT` 环境变量 | 用户显式指定的绝对路径 | 非空字符串、绝对路径、解析后路径存在且为目录 |
| 2 | agent/client 传入 workspace 元数据 | MCP 初始化参数中的 `roots` 字段；取第一个有效目录条目 | 非空、绝对路径、解析后路径存在且为目录；仅 stdio transport 可用 |
| 3 | MCP 进程启动 cwd | `os.getcwd()` 的返回值 | 非空、绝对路径、解析后路径存在且为目录 |
| 4 | 无可用来源 | — | 报 `workspace_root_required` 错误 |

所有来源均无效时，必须报 `workspace_root_required` 错误。不允许使用任意路径继续。

## 精确判定规则

### 绝对路径

`os.path.isabs(path)` 返回 `True` 的路径为绝对路径。相对路径在任何优先级层级均视为无效，不参与后续判定。

### 路径存在且为目录

`os.path.isdir(resolved_path)` 返回 `True` 为有效。以下情况视为无效：

- 路径不存在
- 路径存在但为文件而非目录
- 路径存在但无访问权限（`os.path.isdir()` 返回 `False`）

### 符号链接

所有路径在判定前必须通过 `os.path.realpath()` 解析到真实路径。解析后的路径必须满足：

- 仍是绝对路径
- 存在且为目录

若符号链接指向的目标不存在或不是目录，该来源视为无效。

### 环境变量空白字符串

`MARIVO_WORKSPACE_ROOT` 为空白字符串（仅含空白字符或为空串）时，视为缺失，不进入优先级 1 判定，继续尝试优先级 2。

### MCP roots（优先级 2）

MCP 初始化参数中的 `roots` 字段是一个 URI 列表。解析规则：

1. 仅当 `transport=stdio` 时检查此来源。HTTP transport 无客户端 roots 语义，直接跳过优先级 2。
2. 遍历 `roots` 列表，对每个条目：
   - 若为 `file://` URI，提取本地路径
   - 若为绝对路径字符串，直接使用
   - 否则跳过该条目
3. 取第一个满足"绝对路径、解析后存在且为目录"的条目作为 workspace root
4. 若列表为空或无有效条目，继续尝试优先级 3

### cwd（优先级 3）

`os.getcwd()` 的返回值直接作为候选路径，按通用规则判定（绝对路径、解析后存在且为目录）。

若 `os.getcwd()` 抛出异常（如工作目录已被删除），该来源视为无效，继续尝试优先级 4——即报错。

## workspace root 的下游使用

### `marivo-mcp`

- 定位 `<workspace_root>/.marivo/runtime.json`：用于运行时发现与复用
- 定位 `<workspace_root>/.marivo/marivo.yaml`：用于本地配置发现
- 在 `marivo-mcp init` 生成的配置片段中填入 `MARIVO_WORKSPACE_ROOT`

### `marivo core`

- `marivo serve-local`：接受 `--workspace-root` 参数；若未提供，从调用方（`marivo-mcp`）传入的环境变量或参数获取
- `marivo init-local`：同上
- `marivo doctor`：读取 `.marivo/runtime.json` 中的 `workspace_root` 字段进行校验
- `marivo runtime status`：同上
- `marivo runtime stop`：同上

### `.marivo/` 目录

- 路径：`<workspace_root>/.marivo/`
- 不存在时由 `init-local` 或自动托管流程创建
- 不允许将 `.marivo/` 创建在用户 home 目录、临时目录或其他非工作区路径

## 不变量

1. **workspace root 不可缺失**：拿不到 workspace root 时必须报 `workspace_root_required` 错误。不允许使用用户 home 目录、临时目录、`/tmp`、`os.path.expanduser("~")` 等任意路径作为静默默认值。

2. **workspace root 不可漂移**：解析完成后，同一 MCP 进程生命周期内 workspace root 不可变更。后续工具调用必须使用同一个 workspace root，不允许因 cwd 变化而重新解析。

3. **远程模式忽略 workspace root**：`mode=remote` 时不解析 workspace root。`MARIVO_WORKSPACE_ROOT` 已设置也不使用。实现不应在远程模式下读取或校验 workspace root 相关路径。

4. **解析结果为绝对路径**：无论输入来源如何，最终解析结果必须是绝对路径。实现应在解析完成后断言 `os.path.isabs(result)`。

5. **解析结果为真实路径**：符号链接必须被解析。实现应在解析完成后使用 `os.path.realpath(result)` 作为最终值。

## 错误报告

当 workspace root 无法解析时，必须报 `workspace_root_required` 错误，包含以下结构化信息：

```python
{
    "tried_sources": [
        "MARIVO_WORKSPACE_ROOT",
        "mcp_roots",
        "cwd"
    ]
}
```

`tried_sources` 列表记录实际尝试过的来源，而非全部三个来源。例如，若 HTTP transport 跳过了 MCP roots，列表中不出现 `"mcp_roots"`。

错误结构定义见 [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md)。

## 与其他契约的关系

| 契约 | 本文与其关系 |
|------|-------------|
| [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md) | 1.2 定义了 `workspace_root` 字段和优先级序列（3 个来源）；本文补充每一步的精确语义、边界条件和下游使用 |
| [`agent-runtime-target-resolution-v1-scope-note.zh.md`](./agent-runtime-target-resolution-v1-scope-note.zh.md) | 1.1 定义了"本地自动托管必须以工作区为作用域"的产品边界；本文是该边界的编码依据 |
| [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md) | 错误 taxonomy 定义了 `workspace_root_required` 的完整结构化 schema；本文定义何时触发该错误 |
