# Agent Runtime Target Resolution v1 配置契约

本文定义 agent runtime target resolution v1 的配置语义、解析规则、冲突处理与不变量。它是 `marivo-mcp` 配置改造和目标解析实现的唯一编码依据，后续实现不应在调用点二次猜测 mode 语义或配置优先级。

v1 产品边界与用户心智模型见 [`agent-runtime-target-resolution-v1-scope-note.zh.md`](./agent-runtime-target-resolution-v1-scope-note.zh.md)。

## 配置面

### 最小用户配置面

v1 用户可见的配置收敛为 4 个字段。普通用户不应直接配置高级字段，除非进入高级模式或运维诊断场景。

| 字段 | 环境变量 | 类型 | 默认值 | 说明 |
|------|----------|------|--------|------|
| `mode` | `MARIVO_MODE` | `Literal["auto", "remote", "local"]` | `"auto"` | 运行时内部模式 |
| `base_url` | `MARIVO_BASE_URL` | `str \| None` | `None` | 远程 Marivo HTTP 地址 |
| `api_token` | `MARIVO_API_TOKEN` | `str \| None` | `None` | 远程鉴权 token |
| `workspace_root` | `MARIVO_WORKSPACE_ROOT` | `str \| None` | `None` | 本地工作区根目录 |

### 高级/内部配置面

以下字段仅用于高级模式或运维场景。`marivo-mcp init` 不在默认输出中包含这些字段。

| 字段 | 环境变量 | 类型 | 默认值 | 说明 |
|------|----------|------|--------|------|
| `local_host` | `MARIVO_LOCAL_HOST` | `str` | `"127.0.0.1"` | 本地 daemon 绑定地址 |
| `local_port` | `MARIVO_LOCAL_PORT` | `int` | `0` | 本地 daemon 绑定端口；`0` 表示由 OS 分配 |
| `start_timeout_ms` | `MARIVO_START_TIMEOUT_MS` | `int` | `15000` | 本地 daemon 启动超时（毫秒） |
| `healthcheck_timeout_ms` | `MARIVO_HEALTHCHECK_TIMEOUT_MS` | `int` | `2000` | 单次 `/health` 检查超时（毫秒） |

### 保留的传输配置面

以下字段控制 MCP 传输行为，与目标解析无关，保持现有语义不变。

| 字段 | 环境变量 | 类型 | 默认值 | 说明 |
|------|----------|------|--------|------|
| `transport` | `MARIVO_MCP_TRANSPORT` | `Literal["stdio", "streamable-http"]` | `"stdio"` | MCP 传输方式 |
| `timeout_ms` | `MARIVO_TIMEOUT_MS` | `int` | `600000` | 工具调用总超时 |
| `http.host` | `MARIVO_MCP_HOST` | `str` | `"127.0.0.1"` | HTTP transport 绑定地址 |
| `http.port` | `MARIVO_MCP_PORT` | `int` | `8000` | HTTP transport 绑定端口 |
| `http.streamable_http_path` | `MARIVO_MCP_STREAMABLE_HTTP_PATH` | `str` | `"/mcp"` | HTTP transport 路径 |
| `http.stateless_http` | `MARIVO_MCP_STATELESS_HTTP` | `bool` | `True` | 无状态 HTTP 模式 |
| `http.json_response` | `MARIVO_MCP_JSON_RESPONSE` | `bool` | `True` | JSON 响应模式 |

## 解析规则

以下算法可直接翻译为 `if/elif/else` 代码，实现不得在调用点补充额外判断或隐式降级。

```
1. 读取 MARIVO_MODE，默认值为 "auto"。
   - 若值不为 "auto"|"remote"|"local"，报 config_invalid 错误。

2. mode = "remote":
   a. MARIVO_BASE_URL 必须存在且非空，否则报 remote_target_required 错误。
   b. 忽略 MARIVO_WORKSPACE_ROOT（即使已设置）。
   c. 忽略 MARIVO_API_TOKEN 之外的本地相关配置。
   d. 尝试连接该远程目标。
   e. 若不可达，报 remote_target_unreachable 错误。
   f. 绝不回退本地。

3. mode = "local":
   a. 忽略 MARIVO_BASE_URL（即使已设置）。
   b. 忽略 MARIVO_API_TOKEN（即使已设置）。
   c. 解析 workspace_root：
      - 显式 MARIVO_WORKSPACE_ROOT > agent/client 传入 workspace 元数据 > MCP 进程启动 cwd
      - 若无法解析，报 workspace_root_required 错误。
   d. 解析或启动本地运行时（详见 runtime lifecycle contract）。

4. mode = "auto":
   a. 若 MARIVO_BASE_URL 存在且非空 → 按 remote 处理（步骤 2）。
   b. 否则 → 按 local 处理（步骤 3）。
```

### workspace_root 解析优先级

当 mode 为 `local` 或 `auto` 降级为 `local` 时，workspace_root 按以下顺序解析：

1. 显式 `MARIVO_WORKSPACE_ROOT` 环境变量
2. agent/client 传入的 workspace 元数据（如 MCP 初始化参数中的 workspace 路径）
3. MCP 进程启动时的当前工作目录（cwd）

若以上均无法提供有效路径，必须报 `workspace_root_required` 错误。不允许使用用户 home 目录、临时目录或其他任意路径作为静默默认值。

## 冲突处理

### mode × env 组合

| mode | `MARIVO_BASE_URL` | `MARIVO_WORKSPACE_ROOT` | `MARIVO_API_TOKEN` | 结果 |
|------|--------------------|------------------------|--------------------|------|
| `remote` | 缺失 | — | — | 错误：`remote_target_required` |
| `remote` | 已设置 | 已设置 | — | 忽略 `workspace_root`；连接远程 |
| `remote` | 已设置 | — | 已设置 | 连接远程并附带 token |
| `remote` | 已设置 | 已设置 | 已设置 | 忽略 `workspace_root`；连接远程并附带 token |
| `local` | 已设置 | — | — | 忽略 `base_url`；warning 日志 |
| `local` | 缺失 | 缺失 | — | 错误：`workspace_root_required` |
| `local` | 已设置 | 已设置 | 已设置 | 忽略 `base_url` 和 `api_token`；warning 日志 |
| `local` | 缺失 | 已设置 | — | 解析本地运行时 |
| `auto` | 已设置 | — | — | 按 remote 处理 |
| `auto` | 已设置 | — | 已设置 | 按 remote 处理，附带 token |
| `auto` | 缺失 | 缺失 | — | 按 local 处理 → `workspace_root_required` 错误 |
| `auto` | 缺失 | 已设置 | — | 按 local 处理 |
| `auto` | 缺失 | 已设置 | 已设置 | 按 local 处理；忽略 `api_token` |

### warning 日志规范

当 `mode=local` 但 `MARIVO_BASE_URL` 已设置时，实现应记录一条 warning：

```
MARIVO_BASE_URL is set but mode=local; base_url will be ignored
```

当 `mode=local` 但 `MARIVO_API_TOKEN` 已设置时，实现应记录一条 warning：

```
MARIVO_API_TOKEN is set but mode=local; api_token will be ignored
```

warning 不应阻止启动，但必须出现在日志中，以便运维排障。

## 必须满足的不变量

1. **远程不回退**：已配置的远程目标绝不能静默回退到本地。`remote` mode 下，远程不可达 = 明确错误。
2. **本地不漂移**：本地解析绝不能静默切换到某个任意不同的 endpoint。`runtime.json` 中的 `base_url` 必须与 MCP 实际使用的 endpoint 精确一致。
3. **目标可观测**：MCP 启动时应清晰记录解析后的目标。日志中必须出现类似以下行：
   ```
   Marivo target resolved: remote http://team-marivo:8000
   Marivo target resolved: local auto-managed at http://127.0.0.1:48231
   ```
4. **workspace 不可缺失**：`workspace_root` 在 `local` 或 `auto`→`local` 场景下不可缺失。拿不到 workspace 时必须失败，不允许使用任意路径继续。
5. **base_url 是目标提示**：`MARIVO_BASE_URL` 是远程目标地址，不是第二种传输模式。它的存在决定目标类型，不改变协议边界。

## 错误分类

以下错误标识符用于结构化错误输出，不依赖自由文本拼凑。

| 错误标识 | 触发条件 | 用户可见信息建议 |
|----------|----------|------------------|
| `remote_target_required` | `mode=remote` 但 `MARIVO_BASE_URL` 缺失或为空 | "远程模式需要提供 Marivo 服务地址" |
| `remote_target_unreachable` | 远程目标连接失败 | "无法连接到远程 Marivo 服务：{base_url}" |
| `workspace_root_required` | `mode=local` 或 `auto`→`local` 但无法解析 workspace root | "本地模式需要工作区目录；请设置 MARIVO_WORKSPACE_ROOT 或在项目目录中启动" |
| `runtime_manifest_invalid` | `.marivo/runtime.json` 存在但格式无效或缺少必要字段 | "本地运行时清单无效：{path}" |
| `local_runtime_start_failed` | 本地 daemon 启动超时或健康检查失败 | "本地 Marivo 启动失败：{reason}" |
| `mcp_init_client_unsupported` | `marivo-mcp init --client <X>` 指定了不支持的客户端 | "不支持的客户端类型：{client}；请使用 --print-config 手动配置" |
| `config_invalid` | `MARIVO_MODE` 值不在 `auto|remote|local` 范围内 | "无效的 MARIVO_MODE 值：{value}；允许值：auto, remote, local" |

## 与 `marivo-mcp` 现有配置的映射

本文档冻结的是目标态配置契约。`marivo-mcp` 当前实现与目标态之间的差距如下：

| 当前状态 | 目标态 | 改造要点 |
|----------|--------|----------|
| `base_url: str`（必填，`min_length=1`） | `base_url: str \| None` | 字段改为可选；必填约束移至 mode-aware 校验 |
| `MARIVO_BASE_URL required` 硬错误 | mode-aware 校验 | `mode=remote` 时才要求 `base_url` 必填 |
| 无 `mode` 字段 | `mode: Literal["auto", "remote", "local"]` | 新增字段及环境变量 `MARIVO_MODE` |
| 无 `workspace_root` 字段 | `workspace_root: str \| None` | 新增字段及环境变量 `MARIVO_WORKSPACE_ROOT` |
| 无本地运行时相关字段 | `local_host`、`local_port`、`start_timeout_ms`、`healthcheck_timeout_ms` | 新增字段及对应环境变量 |
| `load_config_from_env()` 无 mode 分支 | 按本契约解析规则实现 mode 分支 | 插入目标解析逻辑，替代直接使用 `base_url` |
| `MarivoHttpClient` 构造时使用静态 `base_url` | 目标解析后使用动态 `base_url` | 客户端构造延迟到目标解析完成之后 |

实现改造详见后续任务（T4：`marivo-mcp` 目标解析 / Config / Init 主链路）。
