# Agent Runtime Target Resolution v1 `marivo core` CLI 命令面契约

本文定义 `marivo core` CLI 的命令面、参数语义、退出码映射、输出格式与副作用。它是 T3（`marivo core` CLI / Runtime 实现）的唯一编码依据，实现阶段不得在命令面或退出码语义上扩展本文未定义的行为。

v1 产品边界与用户心智模型见 [`agent-runtime-target-resolution-v1-scope-note.zh.md`](./agent-runtime-target-resolution-v1-scope-note.zh.md)。配置语义见 [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md)。workspace root 解析见 [`agent-runtime-target-resolution-workspace-root.zh.md`](./agent-runtime-target-resolution-workspace-root.zh.md)。错误结构见 [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md)。HTTP MCP 适用边界见 [`agent-runtime-target-resolution-http-mcp-boundary.zh.md`](./agent-runtime-target-resolution-http-mcp-boundary.zh.md)。工作区布局见 [`agent-runtime-target-resolution-workspace-layout.zh.md`](./agent-runtime-target-resolution-workspace-layout.zh.md)。

## CLI 注册

根 `pyproject.toml` 新增单个入口点：

```toml
[project.scripts]
marivo = "app.cli:main"
```

CLI 模块使用 `argparse`（标准库，无新增依赖）解析子命令。`app.cli` 可为单文件或包，由实现决定，但入口函数签名必须为 `def main() -> None`。

## 退出码分配

退出码是 `marivo-mcp` runtime supervisor 映射 `TargetResolutionError` 的唯一通道。`marivo-mcp` 不解析 stderr 语义（见 T1.4 跨组件传播规则），因此退出码必须承载足够的诊断信息。

| 退出码 | 语义 | T1.4 映射 | 使用场景 |
|--------|------|-----------|----------|
| 0 | 成功 | — | 命令正常完成 |
| 1 | 通用失败 | `local_runtime_start_failed` | 未归入 2–6 的本地操作失败 |
| 2 | 配置无效 | `runtime_manifest_invalid` | 配置文件不存在、不可解析或违反 schema |
| 3 | workspace root 不可用 | `workspace_root_required` | 无法解析 workspace root（非绝对、不存在、非目录等） |
| 4 | 运行时未运行 | —（信息性） | `runtime status`/`runtime stop` 发现无运行中实例 |
| 5 | 健康检查失败 | `local_runtime_start_failed` | 服务进程存活但 `/health` 不返回 OK |
| 6 | 端口/地址不可用 | `local_runtime_start_failed` | 无法绑定到请求的 host:port |
| 10 | 无效 CLI 用法 | — | 参数错误、未知子命令等 |

设计原理：

- 2–6 承载语义信息，`marivo-mcp` 可直接映射到 T1.4 错误码，无需解析 stderr
- 1 是兜底码，涵盖无法归入 2–6 的失败
- 4 是信息性退出，不是严格意义上的错误——它告诉调用方"当前无可复用的运行时"，这在首次启动时是正常的
- 10 是 CLI 层面的参数错误，当 `marivo-mcp` 是调用方时不应出现

## 输出格式

两类受众：(a) `marivo-mcp` 读取 stdout，(b) 人类 operator 读取终端。

- **`--format json`**：stdout 输出结构化 JSON，供 `marivo-mcp` 程序化读取
- **`--format text`**（默认）：stdout 输出人类可读文本
- **自动检测**：当 stdout 不是 TTY 时，默认使用 `json`；是 TTY 时，默认使用 `text`
- **stderr**：始终为人类可读的诊断信息，不承载机器可解析的结构化数据

`marivo-mcp` 调用 `serve-local` 和 `runtime status` 时使用 `--format json`，以解析输出中的 endpoint 和 manifest 信息。其他命令（`init-local`、`doctor`、`runtime stop`）`marivo-mcp` 仅关心退出码。

## 命令 1：`marivo serve`

### 用途

启动一个使用显式配置的 Marivo HTTP 服务器（对现有 `uvicorn app.main:app` 行为的 CLI 封装）。

### 参数与标志

| 参数/标志 | 必填 | 默认值 | 说明 |
|-----------|------|--------|------|
| `--config` | 否 | `MARIVO_CONFIG` 环境变量 > cwd 下的 `marivo.yaml` | Marivo YAML 配置文件路径 |
| `--host` | 否 | `127.0.0.1` | 绑定地址 |
| `--port` | 否 | `8000` | 绑定端口 |
| `--log-level` | 否 | 配置中 `observability.log_level`，或 `INFO` | 日志级别 |
| `--format` | 否 | TTY 自动检测 | 输出格式 |

### 退出码

0（成功）、2（配置无效/未找到）、6（端口不可用）、10（无效参数）

### stdout

- JSON：`{"status": "serving", "host": "<host>", "port": <port>, "config_path": "<abs_path>"}`
- 文本：`Marivo serving on http://<host>:<port> (config: <path>)`

### 副作用

启动前台 uvicorn 进程。不创建 `.marivo/`，不写入 `runtime.json`。

### 前置条件

配置文件存在且有效（或默认值可产生有效 `MarivoConfig`）。

### 后置条件

HTTP 服务器正在监听；`/health` 返回 OK。

### 与 `serve-local` 的区别

`serve` 是通用的显式配置启动命令，不管理 `.marivo/`、不写入 manifest、不关心 workspace root。适用于 operator 显式控制部署。`marivo-mcp` 不调用此命令——它使用 `serve-local`。

## 命令 2：`marivo serve-local`

### 用途

启动工作区作用域的本地 Marivo daemon，写入 `runtime.json` 供 `marivo-mcp` 发现和复用。

这是 `marivo-mcp` 在本地自动托管场景下调用的规范入口。

### 参数与标志

| 参数/标志 | 必填 | 默认值 | 说明 |
|-----------|------|--------|------|
| `--workspace-root` | 否 | 按 T1.3 优先级链解析 | 工作区根目录绝对路径 |
| `--host` | 否 | `MARIVO_LOCAL_HOST` 环境变量，或 `127.0.0.1` | 绑定地址（覆盖环境变量） |
| `--port` | 否 | `MARIVO_LOCAL_PORT` 环境变量，或 `0` | 绑定端口；`0` 表示 OS 分配（覆盖环境变量） |
| `--start-timeout-ms` | 否 | `MARIVO_START_TIMEOUT_MS` 环境变量，或 `15000` | 启动自检超时（毫秒，覆盖环境变量） |
| `--format` | 否 | TTY 自动检测 | 输出格式 |

### 环境变量

| 变量 | 优先级 | 说明 |
|------|--------|------|
| `MARIVO_WORKSPACE_ROOT` | 低于 `--workspace-root` 标志 | 工作区根目录路径 |
| `MARIVO_LOCAL_HOST` | 低于 `--host` 标志 | 绑定地址 |
| `MARIVO_LOCAL_PORT` | 低于 `--port` 标志 | 绑定端口 |
| `MARIVO_START_TIMEOUT_MS` | 低于 `--start-timeout-ms` 标志 | 启动超时 |

### 退出码

0（成功）、1（通用启动失败）、2（bootstrap 后配置无效）、3（workspace root 不可用）、5（健康检查失败）、6（端口不可用）、10（无效参数）

### stdout

- JSON：`{"status": "serving", "host": "<host>", "port": <port>, "base_url": "http://<host>:<port>", "workspace_root": "<abs_path>", "pid": <pid>, "config_path": "<abs_path>", "metadata_path": "<abs_path>"}`
- 文本：`Marivo local runtime serving on http://<host>:<port> (workspace: <path>)`

### stderr

人类可读的启动诊断信息与日志输出。

### 副作用

1. 解析 workspace root（按 T1.3 优先级链）
2. 创建 `.marivo/`（若不存在，幂等 mkdir）
3. 创建 `.marivo/marivo.yaml`（若不存在，写入最小配置，不覆盖已有文件）
4. 确保 `.marivo/metadata.sqlite` 的父目录存在
5. 以 daemon 模式启动 uvicorn 子进程
6. 写入 `.marivo/runtime.json`
7. 创建 `.marivo/run/` 目录并写入 `.marivo/run/marivo.pid`

### 前置条件

workspace root 可解析（按 T1.3）。`.marivo/` 的父目录可写。

### 后置条件

- `.marivo/runtime.json` 存在且有效
- HTTP 服务器正在监听，`/health` 返回 OK
- `.marivo/run/marivo.pid` 包含 daemon PID

### 启动流程

```
1. 解析 workspace_root（标志 > MARIVO_WORKSPACE_ROOT > 退出码 3）
2. 校验 workspace_root：绝对路径、存在、为目录、通过 os.path.realpath 解析
3. 确保 .marivo/ 存在（幂等 mkdir）
4. 确保 .marivo/marivo.yaml 存在（缺失时创建最小配置，不覆盖已有）
5. 确保 .marivo/metadata.sqlite 的父目录存在
6. 绑定 host:port；若 port=0，由 OS 分配
7. 以 daemon 模式启动 uvicorn 子进程
8. 轮询 GET /health 直到成功或 start_timeout_ms 超时
9. 写入 .marivo/runtime.json
10. 写入 .marivo/run/marivo.pid
11. 退出码 0，输出 JSON/文本
```

### daemon 行为

uvicorn 子进程必须在 `serve-local` 命令退出后继续运行。即 `serve-local` 派生子进程（通过 `subprocess.Popen` 或等价机制），等待健康检查通过，写入 manifest，然后退出码 0。daemon 继续运行。

### 与 `marivo-mcp` 的契约

`marivo-mcp` 调用此命令并检查退出码。退出码 0 表示 daemon 运行中且 `runtime.json` 有效。任何非零退出码表示启动失败，`marivo-mcp` 按本文退出码表映射到 `TargetResolutionError`。`marivo-mcp` 不解析 stdout 或 stderr 来判断成功/失败。

### 默认端口 0 的设计原理

本地自动托管场景下，`marivo-mcp` 不应假定任何特定端口可用。使用端口 `0` 让 OS 分配空闲端口，避免端口冲突。`runtime.json` 中的 `port` 和 `base_url` 记录实际分配的端口，`marivo-mcp` 从 manifest 读取实际 endpoint。

## 命令 3：`marivo init-local`

### 用途

创建 `.marivo/` 目录并写入最小配置文件，不启动服务器。

### 参数与标志

| 参数/标志 | 必填 | 默认值 | 说明 |
|-----------|------|--------|------|
| `--workspace-root` | 否 | 按 T1.3 优先级链解析 | 工作区根目录绝对路径 |
| `--format` | 否 | TTY 自动检测 | 输出格式 |

### 退出码

0（成功）、3（workspace root 不可用）、10（无效参数）

### stdout

- JSON：`{"status": "initialized", "workspace_root": "<abs_path>", "config_path": "<abs_path>", "metadata_path": "<abs_path>"}`
- 文本：`Initialized .marivo/ at <workspace_root>/.marivo/`

若 `.marivo/marivo.yaml` 已存在（幂等场景），JSON 中 `status` 为 `"already_initialized"`，退出码仍为 0。

### 副作用

1. 创建 `<workspace_root>/.marivo/` 目录
2. 创建 `<workspace_root>/.marivo/marivo.yaml`（仅当文件不存在时写入最小配置；幂等：不覆盖已有文件）
3. 不创建 `logs/`、`run/`、`runtime.json` 或 `metadata.sqlite`
4. 不启动任何进程

### 前置条件

workspace root 可解析且可写。

### 后置条件

`.marivo/` 目录存在。`.marivo/marivo.yaml` 存在且包含有效最小配置。无服务器运行。

### 幂等性

若 `.marivo/marivo.yaml` 已存在，`init-local` 不覆盖它，退出码 0，报告 `status: "already_initialized"`。这防止误操作覆盖用户定制配置。

### 最小 `marivo.yaml` 内容

```yaml
metadata:
  engine: sqlite
  path: .marivo/metadata.sqlite

governance:
  enabled: true

observability:
  log_level: INFO
  metrics_enabled: true
```

此配置中 `metadata.path` 为相对路径，解析到 `<workspace_root>/.marivo/metadata.sqlite`（相对于配置文件所在目录 `.marivo/`），与 `app_factory.py` 现有的路径解析行为一致。

不包含 `sources`、`engines`、`bindings` 或 `mappings` 块（遵循 CLAUDE.md 规则：`marivo.yaml` 是 runtime-only 配置）。

## 命令 4：`marivo doctor`

### 用途

运行配置、metadata、运行时清单和健康的诊断检查，只读报告，不修改任何状态。

### 参数与标志

| 参数/标志 | 必填 | 默认值 | 说明 |
|-----------|------|--------|------|
| `--workspace-root` | 否 | 按 T1.3 优先级链解析 | 工作区根目录绝对路径 |
| `--format` | 否 | TTY 自动检测 | 输出格式 |

### 退出码

0（全部检查通过）、2（配置无效）、3（workspace root 不可用）、4（运行时未运行——信息性，不一定是错误）

### stdout

JSON 格式：

```json
{
  "workspace_root": "/abs/path",
  "checks": [
    {"name": "workspace_root", "ok": true, "detail": "绝对路径，存在，为目录"},
    {"name": "dot_marivo_dir", "ok": true, "detail": ".marivo/ 存在且可写"},
    {"name": "config_file", "ok": true, "detail": ".marivo/marivo.yaml 有效"},
    {"name": "metadata_store", "ok": true, "detail": ".marivo/metadata.sqlite 可访问"},
    {"name": "runtime_manifest", "ok": false, "detail": ".marivo/runtime.json 不存在"},
    {"name": "runtime_health", "ok": false, "detail": "无运行中实例可供检查"}
  ],
  "ok": false,
  "summary": "4/6 checks passed; runtime not running"
}
```

文本格式：表格化显示检查名称、状态和详情。

### 副作用

无。只读诊断。

### 检查项（按顺序）

1. **workspace_root**：workspace root 是否绝对路径、存在且为目录？
2. **dot_marivo_dir**：`.marivo/` 是否存在？是否可写？
3. **config_file**：`.marivo/marivo.yaml` 是否存在？是否可解析？是否满足 `MarivoConfig` schema？
4. **metadata_store**：配置中的 metadata path 是否存在？是否可读？（解析相对于 `.marivo/` 的 `metadata.path`）
5. **runtime_manifest**：`.marivo/runtime.json` 是否存在？是否可解析为有效 JSON 且包含必要字段？
6. **runtime_health**：若 `runtime.json` 中有 PID，该 PID 是否存活？`GET /health` 是否返回 OK？

### 前置条件

workspace root 可解析。

### 后置条件

无（只读）。

### 退出码语义

- 0：所有检查通过，本地运行时健康
- 4：运行时未运行（manifest 缺失或进程不存活）——这不是配置错误，仅反映当前状态
- 2：配置文件问题——需要 operator 介入
- 3：workspace root 不可用——环境问题

## 命令 5：`marivo runtime status`

### 用途

读取 `runtime.json`，校验引用的进程是否存活且健康，报告当前 endpoint。

### 参数与标志

| 参数/标志 | 必填 | 默认值 | 说明 |
|-----------|------|--------|------|
| `--workspace-root` | 否 | 按 T1.3 优先级链解析 | 工作区根目录绝对路径 |
| `--format` | 否 | TTY 自动检测 | 输出格式 |

### 退出码

0（运行时运行中且健康）、2（manifest 无效）、3（workspace root 不可用）、4（运行时未运行 / manifest 缺失 / PID 不存活）、5（进程存活但健康检查失败）

### stdout

JSON（运行中）：

```json
{
  "status": "running",
  "pid": 12345,
  "base_url": "http://127.0.0.1:48231",
  "host": "127.0.0.1",
  "port": 48231,
  "workspace_root": "/abs/path",
  "started_at": "2026-04-21T10:20:30Z",
  "config_path": "/abs/path/.marivo/marivo.yaml",
  "metadata_path": "/abs/path/.marivo/metadata.sqlite"
}
```

JSON（未运行）：`{"status": "stopped", "workspace_root": "/abs/path"}`

文本：`Marivo local runtime running at http://127.0.0.1:48231 (pid 12345)` 或 `No local runtime running`

### 副作用

无（只读）。

### 校验逻辑

```
1. 读取 .marivo/runtime.json
2. 文件不存在 → 退出码 4
3. JSON 无效或缺少必要字段 → 退出码 2
4. 检查 manifest 中的 PID 是否存活（os.kill(pid, 0)）
5. PID 不存活 → 退出码 4（stale manifest）
6. 调用 GET /health at base_url，短超时（2 秒）
7. 健康检查失败 → 退出码 5
8. 健康检查成功 → 退出码 0，输出状态
```

### 前置条件

workspace root 可解析。

### 后置条件

无（只读）。

### `marivo-mcp` 使用

`marivo-mcp` 调用此命令（`--format json`）来发现已有本地运行时。退出码 0 → 复用；退出码 4 → 需要启动新 daemon；退出码 2/5 → 分别映射为 `runtime_manifest_invalid` / `local_runtime_start_failed`。

## 命令 6：`marivo runtime stop`

### 用途

向 `runtime.json` 中记录的 PID 发送 SIGTERM，然后清理 manifest 和 PID 文件。

### 参数与标志

| 参数/标志 | 必填 | 默认值 | 说明 |
|-----------|------|--------|------|
| `--workspace-root` | 否 | 按 T1.3 优先级链解析 | 工作区根目录绝对路径 |
| `--force` | 否 | `false` | 若 SIGTERM 后进程未终止，在超时后发送 SIGKILL |
| `--timeout-ms` | 否 | `5000` | SIGTERM 后等待优雅退出的毫秒数 |
| `--format` | 否 | TTY 自动检测 | 输出格式 |

### 退出码

0（成功停止）、3（workspace root 不可用）、4（无运行中运行时 / manifest 缺失）、1（停止失败——进程未终止）

### stdout

- JSON：`{"status": "stopped", "pid": 12345, "workspace_root": "/abs/path"}`
- JSON（已停止）：`{"status": "already_stopped", "workspace_root": "/abs/path"}`
- 文本：`Stopped Marivo local runtime (pid 12345)` 或 `No local runtime running`

### 副作用

1. 从 `.marivo/runtime.json` 读取 PID
2. 向 PID 发送 SIGTERM
3. 等待最多 `--timeout-ms` 让进程退出
4. 若 `--force` 且进程仍存活：发送 SIGKILL
5. 删除 `.marivo/runtime.json`
6. 删除 `.marivo/run/marivo.pid`
7. 不删除 `.marivo/marivo.yaml`、`.marivo/metadata.sqlite` 或 `.marivo/logs/`

### 前置条件

workspace root 可解析。

### 后置条件

`.marivo/runtime.json` 已删除。`.marivo/run/marivo.pid` 已删除。daemon 进程已终止（或原本已死亡）。

## 与 T1 契约的对齐

| T1 契约 | 本文对齐点 |
|---------|-----------|
| T1.1 职责表 | CLI 命令属于 `marivo core` 职责；`marivo-mcp` 调用 `serve-local` 作为子进程，不内嵌启动逻辑 |
| T1.2 配置解析算法 | `serve-local` 使用相同的 workspace root 解析优先级链 |
| T1.3 workspace root 优先级链 | 所有命令接受 `--workspace-root` 标志，具有相同的解析语义 |
| T1.4 错误 taxonomy 七个错误码 | 退出码映射到 T1.4 错误码；跨组件传播通过退出码 + stderr |
| T1.5 HTTP MCP 边界 | `serve-local` 可被 `marivo-mcp` 的 workspace guard 检查可用性 |

## 不变量

1. **退出码是 `marivo-mcp` 的唯一诊断通道**：`marivo-mcp` 不解析 CLI 的 stdout 或 stderr 语义来判断成功/失败。退出码 0 = 成功，非零 = 失败，具体语义按本文映射表。

2. **`serve-local` 是本地 daemon 的规范入口**：`marivo-mcp` 不内嵌 Marivo 的应用启动逻辑，仅通过 `subprocess` 调用 `marivo serve-local`。

3. **`serve` 不管理 `.marivo/`**：`marivo serve` 不创建 `.marivo/`、不写入 `runtime.json`、不关心 workspace root。它是现有 `uvicorn` 行为的 CLI 封装。

4. **`init-local` 不启动服务器**：`init-local` 只创建目录和最小配置文件。服务器启动是 `serve-local` 的职责。

5. **默认端口 `0` 避免冲突**：`serve-local` 默认端口 `0`（OS 分配），不假定特定端口可用。实际端口记录在 `runtime.json`。

6. **幂等 bootstrap**：`serve-local` 和 `init-local` 对 `.marivo/marivo.yaml` 的创建是幂等的——已存在时不覆盖。

7. **daemon 独立于 `serve-local` 进程**：`serve-local` 派生 daemon 子进程后自身退出。daemon 进程的生命周期由 `runtime.json` 和 `runtime stop` 管理。

8. **stderr 不承载机器可解析语义**：stderr 始终为人类可读的诊断信息。`marivo-mcp` 不从中提取结构化数据。

## 与其他契约的关系

| 契约 | 本文与其关系 |
|------|-------------|
| [`agent-runtime-target-resolution-v1-scope-note.zh.md`](./agent-runtime-target-resolution-v1-scope-note.zh.md) | 1.1 定义了 `marivo core` CLI 的职责范围（本地运行时管理）；本文定义具体命令面与语义 |
| [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md) | 1.2 定义了 `MARIVO_LOCAL_HOST` 等高级配置字段；本文定义这些字段如何通过 CLI 标志覆盖 |
| [`agent-runtime-target-resolution-workspace-root.zh.md`](./agent-runtime-target-resolution-workspace-root.zh.md) | workspace root 解析文档定义了优先级链；本文的 `--workspace-root` 标志是优先级链的第一级 |
| [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md) | 错误 taxonomy 定义了 `TargetResolutionError` 的结构；本文定义 CLI 退出码到 T1.4 错误码的映射 |
| [`agent-runtime-target-resolution-workspace-layout.zh.md`](./agent-runtime-target-resolution-workspace-layout.zh.md) | 工作区布局文档定义了 `.marivo/` 内各文件的语义；本文定义各命令对这些文件的创建/读取/写入/删除行为 |
