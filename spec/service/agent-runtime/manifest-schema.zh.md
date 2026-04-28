# Agent Runtime Target Resolution v1 Runtime Manifest Schema 契约

本文定义 `.marivo/runtime.json` 的完整 schema、字段约束、跨字段不变量、版本策略、生产者义务、消费者义务和无效/过期处理。它是 `serve-local`（生产者）和 `marivo-mcp` runtime supervisor / CLI 命令（消费者）对本地运行时发现的唯一编码依据。

工作区布局与文件生命周期见 [`workspace-layout.zh.md`](./workspace-layout.zh.md)（本文提取并深化该文档"文件 3"部分）。CLI 命令语义见 [`cli-contract.zh.md`](./cli-contract.zh.md)。错误结构见 [`error-taxonomy.zh.md`](./error-taxonomy.zh.md)。配置语义见 [`config-contract.zh.md`](./config-contract.zh.md)。运行时生命周期流程见 [`lifecycle.zh.md`](./lifecycle.zh.md)。

## Schema 定义

### JSON Schema (Draft 2020-12)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://marivo.dev/schemas/runtime-manifest/v0.1.0",
  "title": "MarivoRuntimeManifest",
  "description": "Marivo 本地运行时发现清单，由 serve-local 写入，由 marivo-mcp 和 CLI 命令消费",
  "type": "object",
  "properties": {
    "version": {
      "type": "string",
      "pattern": "^0\\.1\\.0$",
      "description": "manifest schema 版本"
    },
    "workspace_root": {
      "type": "string",
      "pattern": "^/",
      "description": "工作区根目录绝对路径"
    },
    "mode": {
      "type": "string",
      "enum": ["local"],
      "description": "运行时模式；serve-local 创建的 manifest 始终为 local"
    },
    "base_url": {
      "type": "string",
      "format": "uri",
      "pattern": "^http://",
      "description": "marivo-mcp 应使用的精确 HTTP endpoint"
    },
    "host": {
      "type": "string",
      "description": "实际绑定的主机地址"
    },
    "port": {
      "type": "integer",
      "minimum": 1,
      "maximum": 65535,
      "description": "实际绑定的端口号"
    },
    "pid": {
      "type": "integer",
      "minimum": 1,
      "description": "daemon 进程 PID（提示性，消费者必须重新校验）"
    },
    "started_at": {
      "type": "string",
      "format": "date-time",
      "description": "daemon 启动时间，ISO 8601 UTC 时间戳"
    },
    "config_path": {
      "type": "string",
      "pattern": "^/",
      "description": "marivo.yaml 绝对路径"
    },
    "metadata_path": {
      "type": "string",
      "pattern": "^/",
      "description": "metadata.sqlite 绝对路径"
    }
  },
  "required": [
    "version",
    "workspace_root",
    "mode",
    "base_url",
    "host",
    "port",
    "pid",
    "started_at",
    "config_path",
    "metadata_path"
  ],
  "additionalProperties": false
}
```

### 字段定义

| 字段 | 类型 | 约束 | 语义 |
|------|------|------|------|
| `version` | `string` | 必须匹配 `^0\.1\.0$` | manifest schema 版本。v1 固定为 `"0.1.0"`。版本策略见下文"Schema 版本策略"章节 |
| `workspace_root` | `string` | 绝对路径（`^/`），必须存在且为目录 | daemon 运行的工作区根目录。消费者校验时若路径不存在则视为 manifest 无效 |
| `mode` | `string` | 枚举 `["local"]` | 运行时模式。v1 中 `serve-local` 创建的 manifest 始终为 `"local"` |
| `base_url` | `string` | HTTP URL（`^http://`），必须等于 `http://{host}:{port}` | `marivo-mcp` 在本地解析完成后实际使用的精确 HTTP endpoint。不允许末尾斜杠 |
| `host` | `string` | 非空 | 实际绑定的主机地址（如 `"127.0.0.1"`） |
| `port` | `integer` | `[1, 65535]` | 实际绑定的端口号 |
| `pid` | `integer` | `[1, ∞)` | daemon 进程 PID。仅为提示用途——消费者必须在信任前重新校验（`os.kill(pid, 0)` + `/health`） |
| `started_at` | `string` | ISO 8601 UTC（`YYYY-MM-DDTHH:MM:SSZ`） | daemon 启动时间。消费者可用于日志/诊断，不用于存活判断 |
| `config_path` | `string` | 绝对路径（`^/`） | `marivo.yaml` 绝对路径。消费者可据此定位配置文件 |
| `metadata_path` | `string` | 绝对路径（`^/`） | `metadata.sqlite` 绝对路径。消费者可据此校验 metadata 可访问性 |

## 跨字段不变量

### INV-1：`base_url` 与 `host:port` 一致性

`base_url` 必须精确等于 `http://{host}:{port}`，无尾斜杠，无额外路径段。

校验算法：

```
1. 解析 base_url 为 URL
2. 断言 scheme == "http"
3. 断言 url.hostname == host
4. 断言 url.port == port
5. 断言 url.path == "" 或 url.path == "/"
6. 重构 URL：f"http://{host}:{port}"
7. 断言重构 URL == base_url
```

违反此不变量的 manifest 视为 `runtime_manifest_invalid`。

### INV-2：路径绝对性

`workspace_root`、`config_path`、`metadata_path` 必须为绝对路径（以 `/` 开头）。相对路径在跨进程消费时会产生歧义。

### INV-3：路径下包含关系

`config_path` 必须以 `{workspace_root}/.marivo/marivo.yaml` 结尾。`metadata_path` 必须以 `{workspace_root}/.marivo/metadata.sqlite` 结尾。

这确保 manifest 中的路径与工作区布局一致。消费者不需要额外推断路径关系。

### INV-4：严格 schema

v1 manifest 不允许额外字段（`"additionalProperties": false"`）。消费者遇到未知字段应视为 `runtime_manifest_invalid`。

理由：manifest 是生产者与消费者之间的稳定契约，不允许实现随意添加字段。新字段必须通过 schema 版本升级引入。

## Schema 版本策略

### 版本格式

`version` 字段遵循 `"MAJOR.MINOR.PATCH"` 格式。v1 固定为 `"0.1.0"`。

### 兼容性语义

| 变更类型 | 版本号变更 | 对消费者的影响 |
|----------|-----------|---------------|
| 新增可选字段 | PATCH（如 `0.1.0` → `0.1.1`） | 消费者忽略未知可选字段；不视为错误 |
| 新增必要字段 | MINOR（如 `0.1.0` → `0.2.0`） | 消费者必须识别新必要字段；缺失时视为 `runtime_manifest_invalid` |
| 删除字段、重命名字段、改变字段类型、改变结构 | MAJOR（如 `0.1.0` → `1.0.0`） | 消费者必须完整重新实现解析逻辑 |

### 版本校验规则

```
1. 解析 manifest 中的 version 字段
2. 若 version 为 "0.1.0"：按本文 schema 校验
3. 若 version 的 MAJOR.MINOR 与消费者支持的 MAJOR.MINOR 相同，仅 PATCH 不同：按本文 schema 校验（忽略新增可选字段）
4. 若 version 的 MAJOR 或 MINOR 与消费者支持的不同：视为 runtime_manifest_invalid
   detail.manifest_path = 文件路径
   detail.parse_error = "unsupported manifest version: {version}"
   detail.missing_fields = None
```

### 版本升级约束

- 版本升级必须先更新本文档再实现
- 不允许实现在未更新本文档的情况下变更 schema
- `version` 字段本身是必要字段；缺少 `version` 的 manifest 视为无效

## 生产者义务

生产者为 `marivo serve-local`。以下义务是 `serve-local` 退出码 0 时的后置条件。

### P-1：仅在健康检查通过后写入

manifest 必须在 `GET /health` 返回成功之后写入。不存在"写入 manifest 后 daemon 又崩溃"的窗口。

实现要求：

```
1. 启动 daemon 子进程
2. 轮询 GET /health 直到成功或 start_timeout_ms 超时
3. 健康检查成功后，写入 runtime.json
4. 写入 run/marivo.pid
5. 退出码 0
```

若健康检查超时或失败，`serve-local` 退出码 5，**不写入** `runtime.json`。若启动过程更早失败（配置无效、端口不可用等），也不写入 `runtime.json`。

### P-2：原子写入

manifest 必须通过以下方式原子写入，避免消费者读到部分写入的文件：

```
1. 将 JSON 内容写入临时文件：<workspace_root>/.marivo/runtime.json.tmp.<pid>
   - 使用当前进程 PID 作为后缀，避免并发写入冲突
2. 调用 os.replace(tmp_path, manifest_path)
   - os.replace 在 POSIX 和 Windows 上均为原子操作
3. 若 os.replace 失败，删除临时文件，不保留部分状态
```

### P-3：字段值来源

| 字段 | 来源 |
|------|------|
| `version` | 硬编码 `"0.1.0"` |
| `workspace_root` | 解析后的 workspace root 绝对路径（`os.path.realpath`） |
| `mode` | 硬编码 `"local"` |
| `base_url` | `f"http://{host}:{port}"` |
| `host` | 实际绑定地址（与 `--host` 参数或默认值一致） |
| `port` | 实际绑定端口（OS 分配时从 daemon 获取实际端口） |
| `pid` | daemon 子进程 PID |
| `started_at` | `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")` |
| `config_path` | `os.path.join(workspace_root, ".marivo", "marivo.yaml")` |
| `metadata_path` | 从 marivo.yaml 中 `metadata.path` 解析后的绝对路径 |

### P-4：JSON 格式

- UTF-8 编码
- 缩进 2 空格
- 末尾换行
- 键按本文字段定义顺序排列（非必须，但推荐以提升可读性）
- 不包含 JSON 注释

### P-5：覆盖语义

`serve-local` 每次成功启动时覆盖已有 `runtime.json`（通过原子写入）。不追加、不合并、不保留历史版本。

## 消费者义务

消费者为 `marivo-mcp` runtime supervisor、`marivo runtime status`、`marivo runtime stop`、`marivo doctor`。以下义务定义消费者读取 manifest 时的校验链。

### 校验链

消费者必须按以下顺序校验 manifest，每个步骤失败时立即中止并返回对应错误：

```
1. JSON 解析
   失败 → runtime_manifest_invalid
   detail.parse_error = JSON 解析错误信息
   detail.missing_fields = None

2. 必要字段存在性
   检查所有 10 个必要字段是否存在
   失败 → runtime_manifest_invalid
   detail.parse_error = "missing required fields"
   detail.missing_fields = [缺失字段名列表]

3. 字段类型与格式
   按 schema 校验每个字段的类型和格式约束
   失败 → runtime_manifest_invalid
   detail.parse_error = 字段校验失败描述
   detail.missing_fields = None

4. 额外字段拒绝
   检查是否存在非 schema 定义的额外字段
   失败 → runtime_manifest_invalid
   detail.parse_error = "unexpected fields: {field_names}"
   detail.missing_fields = None

5. 跨字段不变量
   校验 INV-1（base_url 一致性）、INV-2（路径绝对性）、INV-3（路径下包含关系）
   失败 → runtime_manifest_invalid
   detail.parse_error = 不变量违反描述
   detail.missing_fields = None

6. PID 重新校验
   调用 os.kill(pid, 0) 检查进程是否存活
   ProcessLookupError / OSError → PID 已死亡（manifest 过期）
   PermissionError → PID 存活（进程存在但当前用户无权发信号）
   详见 runtime lifecycle contract

7. /health 重新校验
   GET {base_url}/health，超时 healthcheck_timeout_ms
   返回 {"status": "ok"} → manifest 有效且健康
   其他 → manifest 过期（进程不健康）
   详见 runtime lifecycle contract
```

步骤 1–5 的失败映射为 `runtime_manifest_invalid`。步骤 6–7 的失败映射为 manifest 过期状态，处理方式见 runtime lifecycle contract。

### C-1：不信任 PID

`pid` 字段仅为提示用途。消费者必须在信任 manifest 前重新校验 PID 存活性和 `/health` 响应。理由：

- PID 可能已被 OS 回收，指向一个完全不同的进程
- 即使 PID 存活，daemon 可能处于不健康状态（如锁死、半初始化）

### C-2：不导入 app 内部模块

`marivo-mcp` 直接通过文件 I/O 读取此文件（非子进程调用），不导入 `app` 内部模块来解析此文件。schema 是自包含且稳定的。

### C-3：读取错误处理

若文件存在但无法读取（权限错误、I/O 错误），消费者应将其视为 `runtime_manifest_invalid`，在 `detail.parse_error` 中记录具体的 I/O 错误信息。

## 无效与过期处理

### 分类

| 状态 | 定义 | 错误映射 | 后续动作 |
|------|------|----------|----------|
| **有效且健康** | JSON 有效 + 必要字段完整 + 不变量满足 + PID 存活 + `/health` OK | — | 复用 runtime |
| **过期：PID 死亡** | JSON 有效但 PID 不存活 | —（信息性） | 启动新 daemon（见 lifecycle contract） |
| **过期：进程不健康** | JSON 有效、PID 存活但 `/health` 失败 | `local_runtime_start_failed`（若重启后仍不健康） | 尝试一次受控重启（见 lifecycle contract） |
| **无效** | JSON 无法解析、缺少字段、类型错误、不变量违反、或含额外字段 | `runtime_manifest_invalid` | **不触发自动启动**；要求用户干预 |

### 无效 manifest 不触发自动启动

`runtime_manifest_invalid` 不触发 `serve-local` 自动启动。理由：

- 无效 manifest 意味着工作区状态不可靠（文件损坏、手动篡改、版本不兼容）
- 在不可靠的工作区上自动启动新 daemon 可能导致数据丢失或状态进一步混乱
- 用户应通过 `marivo doctor` 诊断并手动修复，或删除无效 manifest 后重试

过期 manifest（PID 死亡或进程不健康）可触发自动启动，因为 manifest 结构本身是有效的，仅反映运行时状态变更。

### 过期 manifest 的覆盖

过期 manifest 在下次成功 `serve-local` 后被覆盖（生产者义务 P-5）。消费者不需要手动清理过期 manifest。

## 不变量

1. **写入后即刻有效**：`serve-local` 退出码 0 时，`runtime.json` 必须存在且反映当前运行状态。不存在"写入 manifest 后服务器又崩溃"的窗口——manifest 写入发生在健康检查通过之后。

2. **`base_url` 是 `marivo-mcp` 的唯一 endpoint 来源**：`marivo-mcp` 从 `runtime.json` 的 `base_url` 字段获取本地 daemon 的 endpoint，不从 PID 文件、进程列表或端口扫描推断。

3. **严格 schema**：v1 不允许额外字段。新字段必须通过 schema 版本升级引入。

4. **所有路径绝对**：`workspace_root`、`config_path`、`metadata_path` 必须为绝对路径，消除跨进程路径解析歧义。

5. **`base_url` 精确一致**：`base_url` 必须精确等于 `http://{host}:{port}`。消费者校验时若发现不一致，视为 `runtime_manifest_invalid`。

6. **原子写入**：manifest 通过 `write-to-tmp + os.replace` 原子写入，消费者不会读到部分写入的文件。

7. **消费者校验链完整**：消费者不得跳过校验链中的任何步骤直接信任 manifest 内容。

## 与其他契约的关系

| 契约 | 本文与其关系 |
|------|-------------|
| [`workspace-layout.zh.md`](./workspace-layout.zh.md) | 工作区布局文档定义了 `runtime.json` 的文件生命周期（创建/读取/写入/删除）；本文定义该文件的内容 schema 和生产/消费义务。本文提取并深化布局文档"文件 3"部分 |
| [`cli-contract.zh.md`](./cli-contract.zh.md) | CLI 命令契约定义了 `serve-local` 的 stdout JSON 输出格式；本文定义的 manifest 字段与 stdout JSON 字段一一对应。`serve-local` 的退出码决定了 manifest 是否被写入 |
| [`error-taxonomy.zh.md`](./error-taxonomy.zh.md) | 错误 taxonomy 定义了 `runtime_manifest_invalid` 的 `detail` 字段结构（`manifest_path`、`parse_error`、`missing_fields`）；本文定义触发该错误的校验链和具体条件 |
| [`config-contract.zh.md`](./config-contract.zh.md) | 配置契约定义了 `local_host`、`local_port` 等字段；本文的 `host`、`port` 字段是运行时实际值（OS 分配后），不一定等于配置默认值 |
| [`lifecycle.zh.md`](./lifecycle.zh.md) | 生命周期契约定义了 manifest 的读-校验-复用-重启流程；本文定义 manifest 的 schema 和校验规则。生命周期契约引用本文的校验链和错误映射 |
