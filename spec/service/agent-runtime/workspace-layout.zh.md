# Agent Runtime Target Resolution v1 工作区 `.marivo/` 布局契约

本文定义工作区作用域 `.marivo/` 目录的布局、各文件的创建/读取/写入/删除语义、缺失/损坏/过期处理、workspace 作用域规则和 `.gitignore` 集成。它是 T3（`marivo core` CLI / Runtime 实现）和 T4（`marivo-mcp` 目标解析）对本地状态文件操作的唯一编码依据。

CLI 命令语义见 [`cli-contract.zh.md`](./cli-contract.zh.md)。配置语义见 [`config-contract.zh.md`](./config-contract.zh.md)。workspace root 解析见 [`workspace-root.zh.md`](./workspace-root.zh.md)。错误结构见 [`error-taxonomy.zh.md`](./error-taxonomy.zh.md)。

## 布局总览

```
<workspace_root>/.marivo/
  marivo.yaml          # 运行时配置
  metadata.sqlite      # Metadata 存储（SQLite 文件）
  runtime.json         # 运行时 manifest（供 marivo-mcp 消费）
  logs/                # 日志目录（serve-local 首次启动时创建）
    marivo.log         # 服务器日志输出
  run/                 # PID 和锁文件目录（serve-local 首次启动时创建）
    marivo.pid         # daemon 进程 PID 文件
```

`init-local` 仅创建 `.marivo/` 和 `marivo.yaml`。`logs/`、`run/`、`runtime.json`、`metadata.sqlite` 由 `serve-local` 或应用运行时按需创建。

## 文件 1：`.marivo/marivo.yaml`

### 创建者

`init-local`（首次创建最小版本）。`serve-local`（若缺失则幂等创建）。Operator 可手动创建/编辑。

### 读取者

`serve-local`（读取以构造 `MarivoConfig`）。`doctor`（读取以校验）。FastAPI 应用启动时（通过 `app/config.py` 的 `load_config`）。

### 写入/变更者与时机

- `init-local` 仅在文件不存在时写入一次（幂等：不覆盖已有文件）
- Operator 可随时手动编辑
- `serve-local` 仅在文件缺失时写入（幂等 bootstrap），不覆盖已有文件
- `marivo-mcp` 不自动变更此文件

### 文件权限

`0644`（owner 读写，group/others 只读）。无需执行权限。

### 内容 schema / 格式约束

- 必须为合法 YAML
- 必须通过 `MarivoConfig.model_validate()` 校验（`extra="forbid"`）
- 本地模式最小有效内容：

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

- `metadata.path` 必须为 `.marivo/metadata.sqlite`（相对路径），解析到 `<workspace_root>/.marivo/metadata.sqlite`。相对路径基于配置文件所在目录（`.marivo/`）解析，与 `app_factory.py` 中 `config_path.parent / metadata_path` 行为一致
- 不允许包含 `sources`、`engines`、`bindings` 或 `mappings` 顶级块（遵循 CLAUDE.md 规则：`marivo.yaml` 是 runtime-only 配置）

### 缺失时

- `init-local` 创建它
- `serve-local` 创建它（幂等 bootstrap）
- 若 `serve-local` 运行时 workspace root 不可写，退出码 3

### 损坏时

- `doctor` 报告为失败检查项
- `serve-local` 退出码 2
- Operator 必须手动修复或删除；`init-local` 不覆盖已存在的文件（即使文件内容损坏）

### 过期时

配置文件无过期概念。每次启动时重新读取。

### 清理/轮转规则

无。文件持续存在直到手动删除。

## 文件 2：`.marivo/metadata.sqlite`

### 创建者

`SQLiteMetadataStore.initialize()`，在 `create_app()` 过程中调用。发生在 `serve-local` 启动服务器时。

### 读取者

FastAPI 应用运行时（通过 `SQLiteMetadataStore`）。`doctor`（检查可访问性）。

### 写入/变更者与时机

FastAPI 应用运行时，通过 `SQLiteMetadataStore` 方法。`marivo-mcp` 不直接写入此文件（T1.1 职责表）。

### 文件权限

文件 `0644`。目录（`.marivo/`）必须对服务器进程可写。

### 内容 schema / 格式约束

- SQLite 3 格式，WAL journal mode
- Schema 由 `app/storage/schema.py` 中的 `METADATA_DDL` 定义

### 缺失时

由 `SQLiteMetadataStore.initialize()` 在首次服务器启动时自动创建。`initialize()` 方法会创建父目录（`self.db_path.parent.mkdir(parents=True, exist_ok=True)`）。无需手动干预。

### 损坏时

- `doctor` 报告为失败检查项
- Operator 必须删除文件；下次启动时自动重建
- 损坏意味着数据丢失

### 过期时

数据库文件本身无过期概念。过期由应用层管理（session 状态等）。

### 清理/轮转规则

无自动化清理。Operator 可删除文件让系统重建。大数据库可手动执行 `VACUUM`。无日志轮转式自动清理。

### 与 `init-local` 的关系

`init-local` 创建 `.marivo/` 目录和最小 `marivo.yaml`，但不创建 SQLite 文件。文件在首次服务器启动时由应用的 `initialize()` 方法创建。这与 `init-local` 不启动服务器的原则一致。

## 文件 3：`.marivo/runtime.json`

### 创建者

`serve-local`（在 daemon 成功启动后写入）。

### 读取者

`runtime status`（读取并校验）。`runtime stop`（读取 PID）。`doctor`（读取并校验）。`marivo-mcp` runtime supervisor（用于运行时发现与复用）。

### 写入/变更者与时机

- `serve-local` 在 daemon 成功启动后写入（创建或覆盖）
- `runtime stop` 在成功关闭后删除

### 文件权限

`0644`。无需执行权限。

### 内容 schema / 格式约束

必须为合法 JSON。必要字段及类型：

```json
{
  "version": "0.1.0",
  "workspace_root": "/abs/path",
  "mode": "local",
  "base_url": "http://127.0.0.1:48231",
  "host": "127.0.0.1",
  "port": 48231,
  "pid": 12345,
  "started_at": "2026-04-21T10:20:30Z",
  "config_path": "/abs/path/.marivo/marivo.yaml",
  "metadata_path": "/abs/path/.marivo/metadata.sqlite"
}
```

字段语义：

| 字段 | 类型 | 约束 |
|------|------|------|
| `version` | `string` | manifest schema 版本，v1 为 `"0.1.0"` |
| `workspace_root` | `string` | 绝对路径，必须与实际 workspace root 一致 |
| `mode` | `string` | `serve-local` 创建的 manifest 始终为 `"local"` |
| `base_url` | `string` | `marivo-mcp` 应使用的精确 HTTP endpoint |
| `host` | `string` | 实际绑定的主机地址 |
| `port` | `integer` | 实际绑定的端口号 |
| `pid` | `integer` | daemon 进程 PID（提示性，消费者必须重新校验） |
| `started_at` | `string` | ISO 8601 UTC 时间戳 |
| `config_path` | `string` | 绝对路径 |
| `metadata_path` | `string` | 绝对路径 |

约束：

- 所有路径必须为绝对路径
- `base_url` 必须是 `marivo-mcp` 在本地解析完成后实际使用的精确 endpoint（T1.2 不变量 2：本地不漂移）
- `pid` 仅为提示用途，消费者必须在信任前重新校验（`os.kill(pid, 0)` + `/health`）
- v1 schema 不允许额外字段（严格 schema）

### 缺失时

正常状态——当没有本地运行时被启动时。`runtime status` 报告"未运行"（退出码 4）。`marivo-mcp` 将缺失解释为"需要启动新 daemon"。

### 损坏时（无效 JSON 或缺少必要字段）

- `runtime status` 退出码 2
- `doctor` 报告为失败检查项
- `marivo-mcp` 映射为 `runtime_manifest_invalid`（T1.4 错误码）
- 推荐修复：删除文件并通过 `serve-local` 重启，或运行 `marivo doctor`

### 过期时（合法 JSON 但 PID 死亡或健康检查失败）

- `runtime status` 退出码 4（PID 死亡）或 5（健康检查失败）
- `marivo-mcp` 将此视为"需要启动新 daemon"（经一次受控重启尝试后，见 T1 规范的"本地运行时启动流程"）
- 过期 manifest 在下次成功 `serve-local` 时被覆盖

### 清理/轮转规则

由 `runtime stop` 删除。由 `serve-local` 在每次成功启动时覆盖。无归档或轮转。

### `marivo-mcp` 消费方式

`marivo-mcp` 直接通过文件 I/O 读取此文件（非子进程调用），作为运行时发现的一部分。它不导入 `app` 内部模块来解析此文件。schema 是自包含且稳定的。`marivo-mcp` 在信任 manifest 前校验 JSON 结构和必要字段。

## 文件 4：`.marivo/logs/`

### 创建者

`serve-local` 在首次 daemon 启动时创建目录。

### 读取者

`doctor`（检查存在性和可写性）。Operator（手动检查）。

### 写入/变更者与时机

daemon 进程在运行时写入 `marivo.log`。

### 文件权限

目录 `0755`。日志文件 `0644`。

### 内容 schema / 格式约束

- `marivo.log` 包含结构化 JSON 日志输出（per `app/observability.py` 的 `JSONFormatter`）
- 日志内容无 schema 约束；仅用于诊断

### 缺失时

由 `serve-local` 在首次启动时自动创建。

### 损坏时

日志目录无损坏概念。单个日志文件可能被截断或包含不完整行；这不构成失败条件。

### 过期时

旧日志条目是正常现象。无自动过期处理。

### 清理/轮转规则

v1 不实现自动日志轮转。Operator 可手动截断或删除日志文件。若未来需要日志轮转，应通过正式契约版本升级引入，不在实现中 ad-hoc 添加。

### `init-local` 不创建此目录

与 `init-local` 不启动服务器的原则一致。`logs/` 是运行时产物，在首次使用时由 `serve-local` 创建。

## 文件 5：`.marivo/run/`

### 创建者

`serve-local` 在首次 daemon 启动时创建目录。

### 读取者

`runtime status`（读取 `marivo.pid` 作为补充信息，但 `runtime.json` 是权威来源）。`runtime stop`（读取 `marivo.pid` 作为补充）。`doctor`（检查存在性）。

### 写入/变更者与时机

- `serve-local` 在 daemon 启动时写入 `marivo.pid`
- `runtime stop` 在关闭时删除 `marivo.pid`

### 文件权限

目录 `0755`。PID 文件 `0644`。

### 内容 schema / 格式约束

- `marivo.pid` 包含单个整数：daemon 进程 PID，以十进制 ASCII 字符串表示，尾部换行
- v1 中 `run/` 不定义其他文件

### 缺失时

由 `serve-local` 在首次启动时自动创建。

### 损坏时

若 `marivo.pid` 包含非数字内容，`runtime status` 和 `runtime stop` 回退到从 `runtime.json` 读取 PID（权威来源）。PID 文件是辅助性的。

### 过期时

`marivo.pid` 中的 PID 可能指向已死亡或被复用的进程。消费者必须始终通过 `os.kill(pid, 0)` 和 `/health` 检查重新校验。`runtime.json` 是权威 manifest；`marivo.pid` 是 Unix 惯例补充。

### 清理/轮转规则

由 `runtime stop` 删除。由 `serve-local` 在每次启动时覆盖。

### `init-local` 不创建此目录

与 `init-local` 不启动服务器的原则一致。`run/` 是运行时产物，在首次使用时由 `serve-local` 创建。

## Workspace 作用域规则

以下规则源自 T1.3 和 T1.1 scope note，正式化为 `.marivo/` 目录的约束：

1. `.marivo/` 必须创建在 `<workspace_root>/.marivo/`，其中 `<workspace_root>` 按 T1.3 优先级链解析
2. `.marivo/` 不得创建在用户 home 目录（`~`）、`/tmp` 或任何系统目录
3. 实现必须在创建 `.marivo/` 前校验解析后的 workspace root 不是以下禁止路径：`/`、`/tmp`、`/var`、`/etc`、`~`（展开后）、或 `os.path.realpath(workspace_root)` 解析到系统目录的任何路径
4. `.marivo/marivo.yaml` 中的相对路径基于 `.marivo/`（配置文件所在目录）解析，与 `app_factory.py` 行为一致
5. `runtime.json` 始终包含绝对路径

## `.gitignore` 集成

项目 `.gitignore` 必须新增以下条目：

```
.marivo/
```

理由：`.marivo/` 包含本地运行时状态（PID 文件、SQLite 数据库、日志），不应被提交。虽然配置文件（`.marivo/marivo.yaml`）理论上可被提交，但它是自动生成的且工作区路径相关，因此整个 `.marivo/` 目录应被 gitignore。

## 创建/读取/写入/删除矩阵

| 文件 | 创建者 | 读取者 | 写入者 | 删除者 |
|------|--------|--------|--------|--------|
| `.marivo/`（目录） | `init-local`、`serve-local` | 所有命令 | N/A | Operator 手动 |
| `.marivo/marivo.yaml` | `init-local`、`serve-local`（缺失时） | `serve-local`、`doctor`、FastAPI app | `init-local`（仅首次），Operator | Operator 手动 |
| `.marivo/metadata.sqlite` | FastAPI `initialize()` | FastAPI app、`doctor` | FastAPI app | Operator 手动 |
| `.marivo/runtime.json` | `serve-local` | `runtime status`、`runtime stop`、`doctor`、`marivo-mcp` | `serve-local`（创建/覆盖） | `runtime stop` |
| `.marivo/logs/`（目录） | `serve-local` | `doctor`、Operator | Daemon 进程 | Operator 手动 |
| `.marivo/logs/marivo.log` | Daemon 进程 | Operator | Daemon 进程 | Operator 手动 |
| `.marivo/run/`（目录） | `serve-local` | `runtime status`、`doctor` | N/A | Operator 手动 |
| `.marivo/run/marivo.pid` | `serve-local` | `runtime status`（辅助） | `serve-local`（覆盖） | `runtime stop` |

## 错误条件与退出码映射

| 条件 | 命令 | 退出码 | T1.4 映射 |
|------|------|--------|-----------|
| `.marivo/` 不可写 | `serve-local` | 3 | `workspace_root_required` |
| `marivo.yaml` 无效 | `serve-local` | 2 | `runtime_manifest_invalid` |
| `marivo.yaml` 缺失（自动创建） | `serve-local` | N/A（创建它） | N/A |
| `metadata.sqlite` 损坏 | `doctor` | 0（报告失败检查项） | N/A（仅诊断） |
| `runtime.json` 缺失 | `runtime status` | 4 | N/A（信息性） |
| `runtime.json` 无效 JSON | `runtime status` | 2 | `runtime_manifest_invalid` |
| `runtime.json` 过期（PID 死亡） | `runtime status` | 4 | N/A（信息性） |
| `runtime.json` PID 存活但不健康 | `runtime status` | 5 | `local_runtime_start_failed` |

## 权威性层级

当同一信息存在多个来源时，权威性排序为：

1. **`runtime.json`**：权威 manifest。包含完整的运行时状态（PID、endpoint、路径、时间戳）
2. **`marivo.pid`**：辅助来源。仅包含 PID，是 Unix 惯例。与 `runtime.json` 冲突时以 `runtime.json` 为准
3. **`marivo.yaml`**：配置来源。不包含运行时状态，但定义了 metadata path 等静态配置

消费者（`runtime status`、`runtime stop`、`marivo-mcp`）应始终以 `runtime.json` 为权威来源。`marivo.pid` 仅在 `runtime.json` 不可读时作为降级参考。

## 不变量

1. **`.marivo/` 必须在工作区作用域内**：不允许在用户 home、`/tmp`、或系统目录创建。无 workspace 上下文时，本地模式必须明确失败。

2. **`runtime.json` 是 `marivo-mcp` 的唯一发现契约**：`marivo-mcp` 不导入 `app` 内部模块、不解析 `marivo.pid`、不通过进程列表发现 daemon。它只读取 `runtime.json`。

3. **`init-local` 不创建运行时产物**：`init-local` 仅创建 `.marivo/` 目录和 `marivo.yaml`。`logs/`、`run/`、`runtime.json`、`metadata.sqlite` 均为运行时产物，由 `serve-local` 或应用按需创建。

4. **`runtime.json` 写入后即刻有效**：`serve-local` 退出码 0 时，`runtime.json` 必须存在且反映当前运行状态。不存在"写入 manifest 后服务器又崩溃"的窗口——manifest 写入发生在健康检查通过之后。

5. **相对路径基于 `.marivo/` 解析**：`marivo.yaml` 中的所有相对路径基于配置文件所在目录（`.marivo/`）解析，与 `app_factory.py` 现有行为一致。这是 `metadata.path: .marivo/metadata.sqlite` 正确解析为 `<workspace_root>/.marivo/metadata.sqlite` 的前提。

6. **`runtime.json` 中所有路径为绝对路径**：`workspace_root`、`config_path`、`metadata_path` 必须为绝对路径，消除路径解析歧义。

7. **`.gitignore` 覆盖整个 `.marivo/`**：不区分"可提交的配置文件"和"不应提交的运行时状态"——整个 `.marivo/` 目录被 gitignore。

## 与其他契约的关系

| 契约 | 本文与其关系 |
|------|-------------|
| [`scope-note.zh.md`](./scope-note.zh.md) | 1.1 定义了"本地自动托管必须以工作区为作用域"的产品边界；本文是该边界的文件系统布局编码依据 |
| [`config-contract.zh.md`](./config-contract.zh.md) | 1.2 定义了 `workspace_root` 字段和 `MARIVO_LOCAL_PORT` 默认值 `0`；本文定义 `.marivo/` 内文件的路径约定 |
| [`workspace-root.zh.md`](./workspace-root.zh.md) | workspace root 解析文档定义了 `.marivo/` 必须在 workspace root 下的约束；本文定义 `.marivo/` 内部结构 |
| [`error-taxonomy.zh.md`](./error-taxonomy.zh.md) | 错误 taxonomy 定义了 `runtime_manifest_invalid` 的 `detail.manifest_path` 字段；本文定义该路径指向的具体文件 |
| [`http-mcp-boundary.zh.md`](./http-mcp-boundary.zh.md) | HTTP MCP 边界文档定义了 workspace guard 检查 `.marivo/` 可写性和 `serve-local` 可用性；本文定义 `.marivo/` 的具体文件结构 |
| [`cli-contract.zh.md`](./cli-contract.zh.md) | CLI 命令契约定义了各命令的副作用（创建/删除文件）；本文定义这些副作用操作的具体文件语义 |
