# Agent Runtime Target Resolution v1 失败面 Taxonomy

本文是目标解析运行时错误的唯一结构化编码依据。它定义错误结构 schema、每个错误标识的完整字段、跨组件传播规则和不变量，实现阶段必须使用本文注册的错误标识符和字段，不允许自由文本拼凑。

配置字段定义与 mode 解析规则见 [`config-contract.zh.md`](./config-contract.zh.md)。用户心智模型见 [`scope-note.zh.md`](./scope-note.zh.md)。workspace root 解析规则见 [`workspace-root.zh.md`](./workspace-root.zh.md)。

## 错误结构 Schema

所有目标解析错误使用统一的基类：

```python
class TargetResolutionError(RuntimeError):
    code: str               # 机器可读标识符，必须在本文 taxonomy 中注册
    message: str            # 默认英文消息模板，可包含 {placeholder}
    detail: dict[str, Any]  # 上下文字段，因 code 而异
    guidance: str | None    # 面向用户的修复建议
```

构造规则：

- `code` 必须是本文"错误标识完整定义"章节中注册的值之一
- `message` 是模板字符串，其中 `{placeholder}` 从 `detail` 对应字段填充
- `detail` 的字段集合因 `code` 而异，详见下文每个标识的定义
- `guidance` 不得为空字符串；若无可提供的修复建议，设为 `None`

## 错误标识完整定义

### `config_invalid`

| 字段 | 值 |
|------|-----|
| 触发条件 | `MARIVO_MODE` 值不在 `auto\|remote\|local` 范围内 |
| 抬出组件 | `marivo-mcp` config 加载 |
| detail | `{"mode_value": str, "allowed": ["auto", "remote", "local"]}` |
| message | `"无效的 MARIVO_MODE 值：{mode_value}"` |
| guidance | `"允许值：auto, remote, local"` |

### `remote_target_required`

| 字段 | 值 |
|------|-----|
| 触发条件 | `mode=remote` 但 `MARIVO_BASE_URL` 缺失或为空 |
| 抬出组件 | `marivo-mcp` target resolver |
| detail | `{}` |
| message | `"远程模式需要提供 Marivo 服务地址"` |
| guidance | `"请设置 MARIVO_BASE_URL"` |

### `remote_target_unreachable`

| 字段 | 值 |
|------|-----|
| 触发条件 | 远程目标连接失败（网络不可达、连接拒绝、超时等） |
| 抬出组件 | `marivo-mcp` target resolver |
| detail | `{"base_url": str, "status_code": int \| None, "timeout": bool}` |
| message | `"无法连接到远程 Marivo 服务：{base_url}"` |
| guidance | `"请检查地址是否正确、服务是否运行"` |

`detail` 字段语义：

- `base_url`：实际尝试连接的远程地址
- `status_code`：若服务返回了 HTTP 响应则为状态码，否则为 `None`
- `timeout`：是否因超时失败

### `workspace_root_required`

| 字段 | 值 |
|------|-----|
| 触发条件 | `mode=local` 或 `auto`→`local` 但无法解析 workspace root |
| 抬出组件 | `marivo-mcp` target resolver |
| detail | `{"tried_sources": [str, ...]}` |
| message | `"本地模式需要工作区目录"` |
| guidance | `"请设置 MARIVO_WORKSPACE_ROOT 或在项目目录中启动"` |

`detail` 字段语义：

- `tried_sources`：实际尝试过的来源列表，取值范围为 `["MARIVO_WORKSPACE_ROOT", "mcp_roots", "cwd"]` 的子集。仅记录实际尝试过的来源，不记录因 transport 类型而跳过的来源。

### `runtime_manifest_invalid`

| 字段 | 值 |
|------|-----|
| 触发条件 | `.marivo/runtime.json` 存在但格式无效或缺少必要字段 |
| 抬出组件 | `marivo-mcp` runtime supervisor |
| detail | `{"manifest_path": str, "parse_error": str, "missing_fields": [str, ...] \| None}` |
| message | `"本地运行时清单无效：{manifest_path}"` |
| guidance | `"请运行 marivo doctor 诊断，或删除 {manifest_path} 重试"` |

`detail` 字段语义：

- `manifest_path`：无效 manifest 文件的绝对路径
- `parse_error`：JSON 解析失败的错误信息；若 JSON 合法但缺少字段，该值为字段校验失败的描述
- `missing_fields`：若 JSON 合法但缺少必要字段，列出缺失字段名；若为 JSON 解析失败，该值为 `None`

### `local_runtime_start_failed`

| 字段 | 值 |
|------|-----|
| 触发条件 | 本地 daemon 启动超时或健康检查失败 |
| 抬出组件 | `marivo-mcp` runtime supervisor |
| detail | `{"workspace_root": str, "timeout_ms": int, "exit_code": int \| None, "health_checked": bool}` |
| message | `"本地 Marivo 启动失败"` |
| guidance | `"请运行 marivo doctor 诊断本地环境"` |

`detail` 字段语义：

- `workspace_root`：本地运行时的工作区根目录绝对路径
- `timeout_ms`：配置的启动超时时间（毫秒）
- `exit_code`：若 daemon 子进程已退出则为退出码，若仍在运行但健康检查失败则为 `None`
- `health_checked`：是否至少成功完成了一次 `/health` 请求（即使返回非 200）

### `mcp_init_client_unsupported`

| 字段 | 值 |
|------|-----|
| 触发条件 | `marivo-mcp init --client <X>` 指定了不支持的客户端类型 |
| 抬出组件 | `marivo-mcp` init 命令 |
| detail | `{"client": str, "supported": [str, ...]}` |
| message | `"不支持的客户端类型：{client}"` |
| guidance | `"请使用 --print-config 手动配置"` |

`detail` 字段语义：

- `client`：用户指定的客户端类型标识符
- `supported`：当前支持的客户端类型列表

## 跨组件传播规则

### `marivo-mcp` → agent / 用户

`TargetResolutionError` 应通过以下方式暴露：

- **stdio transport**：MCP 协议的错误响应中包含 `code` 和 `message`。`detail` 写入 stderr 日志，不暴露给 agent。
- **HTTP transport**：MCP 工具调用的错误响应中包含 `code`、`message` 和 `guidance`。`detail` 写入服务端日志。
- **用户可见文案**：遵循 scope note 的原则——使用"本地自动托管 / 远程显式连接"语言，不向用户暴露 `auto|remote|local` 内部 mode。

### `marivo core` CLI → `marivo-mcp`

`marivo-mcp` 的 runtime supervisor 调用 `marivo serve-local` 等子命令时，应通过子进程退出码和 stderr 输出推断错误类型：

| CLI 行为 | 映射到 |
|----------|--------|
| 子进程正常退出（exit code 0） | 不报错；继续健康检查 |
| 子进程非零退出 + stderr 含端口/地址相关错误 | `local_runtime_start_failed`，`exit_code` 填入实际退出码 |
| 子进程未退出但健康检查超时 | `local_runtime_start_failed`，`timeout=True`，`exit_code=None` |
| 子进程退出 + stderr 含配置/路径相关错误 | `local_runtime_start_failed`，`health_checked=False` |

`marivo-mcp` 不解析 CLI stderr 的精确语义，仅区分"退出"和"超时"两大类。更细粒度的诊断由 `marivo doctor` 负责。

### HTTP transport 错误 → `marivo-mcp`

`MarivoHttpClientError` 已有的 `status_code` / `category` 字段可直接映射：

| `MarivoHttpClientError` 情况 | 映射到 |
|-------------------------------|--------|
| 连接拒绝 / 网络不可达 | `remote_target_unreachable`，`status_code=None`，`timeout=False` |
| 连接超时 | `remote_target_unreachable`，`status_code=None`，`timeout=True` |
| HTTP 响应 5xx | `remote_target_unreachable`，`status_code=<实际值>`，`timeout=False` |
| HTTP 响应 4xx | 不映射为 `remote_target_unreachable`；4xx 是业务错误，不是目标不可达 |

## 与现有错误类的关系

| 现有类 | 位置 | 与 `TargetResolutionError` 的关系 |
|--------|------|----------------------------------|
| `MarivoMcpConfigError(RuntimeError)` | `marivo-mcp/src/marivo_mcp/config.py` | 被 `TargetResolutionError` 替代。现有 `MARIVO_BASE_URL is required` 等硬编码消息迁移为 `remote_target_required` 等 `code` |
| `MarivoHttpClientError(RuntimeError)` | `marivo-mcp/src/marivo_mcp/http_client.py` | 保持独立，但作为 `remote_target_unreachable` 的上游来源。resolver 捕获 `MarivoHttpClientError` 后转换为 `TargetResolutionError` |
| `MarivoMcpDependencyError(RuntimeError)` | `marivo-mcp/src/marivo_mcp/sdk.py` | 不属于目标解析错误，保持独立 |
| `ToolError(BaseModel)` | `marivo-mcp/src/marivo_mcp/models.py` | 属于 MCP 工具层错误响应模型，不属于运行时目标解析错误，保持独立 |
| `SemanticServiceError(Exception)` | `app/semantic_service/errors.py` | 属于 app 内部错误，与 `marivo-mcp` 无关 |
| `ExecutionError(ValueError)` | `app/execution/errors.py` | 属于 app 内部错误，与 `marivo-mcp` 无关 |

设计一致性：`TargetResolutionError` 的 `code` + `detail` + `guidance` 模式与 `SemanticServiceError`（`code` + `category`）和 `ExecutionError`（`code` + `category` + `detail` + `retryable`）保持一致的"结构化错误"设计模式，但属于 `marivo-mcp` 包，不依赖 app 内部错误类。

## 不变量

1. **所有目标解析错误必须使用 `TargetResolutionError`**：实现中不允许使用自由文本 `RuntimeError("some message")` 或 `MarivoMcpConfigError("some message")` 来报告目标解析失败。

2. **`code` 必须在本文注册**：实现中不允许新增未在本文 taxonomy 中注册的 `code` 值。若需新增，必须先更新本文。

3. **`guidance` 不得为空字符串**：每个 `TargetResolutionError` 实例的 `guidance` 要么是包含修复建议的非空字符串，要么是 `None`（仅当确实无可提供的建议时）。

4. **`detail` 字段必须与 `code` 对应**：每个 `code` 有固定的 `detail` 字段集合（见上文），实现不得在 `detail` 中添加未定义的额外字段。

5. **远程不可达绝不回退本地**：`remote_target_unreachable` 不触发任何降级逻辑。报错即停止，不允许静默切换到本地模式。

6. **错误文案遵循用户心智模型**：面向用户的错误消息（`message` 和 `guidance`）使用"本地自动托管 / 远程显式连接"语言，不向用户暴露 `auto|remote|local` 内部 mode 标识符。

## 与其他契约的关系

| 契约 | 本文与其关系 |
|------|-------------|
| [`config-contract.zh.md`](./config-contract.zh.md) | 1.2 定义了错误标识概要表和触发条件；本文补充完整 schema、detail 字段、跨组件传播规则 |
| [`scope-note.zh.md`](./scope-note.zh.md) | 1.1 定义了"不暴露内部 mode"和"不允许静默回退"的产品边界；本文的错误文案不变量是该边界的编码约束 |
| [`workspace-root.zh.md`](./workspace-root.zh.md) | workspace root 解析文档定义了何时触发 `workspace_root_required`；本文定义该错误的结构化字段 |
