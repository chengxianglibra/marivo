# Agent Runtime Target Resolution v1 最小本地配置 Bootstrap 契约

本文冻结 `marivo init-local` 和 `marivo serve-local` 生成的最小 `marivo.yaml` 内容、各字段选入理由、明确排除项、校验约束、写入规则与路径解析语义。它是 T3（`marivo core` CLI / Runtime 实现）中 `init-local` 和 `serve-local` 写入 `marivo.yaml` 的唯一编码依据。

CLI 命令语义见 [`agent-runtime-target-resolution-cli-contract.zh.md`](./agent-runtime-target-resolution-cli-contract.zh.md)。工作区布局见 [`agent-runtime-target-resolution-workspace-layout.zh.md`](./agent-runtime-target-resolution-workspace-layout.zh.md)。runtime manifest schema 见 [`agent-runtime-target-resolution-runtime-manifest-schema.zh.md`](./agent-runtime-target-resolution-runtime-manifest-schema.zh.md)。配置语义见 [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md)。

## 冻结的最小 `marivo.yaml`

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

此内容与 workspace-layout 契约（§文件 1）和 cli-contract 契约（§命令 3）中已冻结的最小配置完全一致。本文为该内容提供集中化的理由与约束说明。

注意：本文只定义本地 bootstrap 默认配置。生产级共享 metadata backend 的 MySQL Fresh-init v1 边界见 [`mysql-metadata-fresh-init-v1.zh.md`](./mysql-metadata-fresh-init-v1.zh.md)；MySQL metadata 不改变本地 bootstrap 默认使用 SQLite，也不改变 source/engine/mapping 通过 HTTP API 管理的边界。

## 字段选入理由

### `metadata.engine: sqlite`

- **选入理由**：`app_factory.py` 的 `_resolve_storage()` 在无外部 `metadata_store` 注入时，必须从 `MarivoConfig.metadata` 读取 `engine` 和对应后端配置。缺少 `metadata` 块将触发 `RuntimeError("Marivo config must define metadata.engine=sqlite|mysql when metadata_store is not provided")`。
- **省略后果**：本地启动必定失败。
- **值约束**：当前本地 bootstrap 固定写出 `sqlite`。MySQL metadata Fresh-init v1 是生产级共享 metadata store 路径，不改变 `init-local` / `serve-local` 的默认写出值。

### `metadata.path: .marivo/metadata.sqlite`

- **选入理由**：同上，`_resolve_storage()` 要求 `metadata.path` 存在。使用相对路径 `.marivo/metadata.sqlite`，基于配置文件所在目录（`.marivo/`）解析为 `<workspace_root>/.marivo/metadata.sqlite`，与 `app_factory.py` 中 `config_path.parent / metadata_path` 行为一致。
- **省略后果**：本地启动必定失败。
- **值约束**：必须为相对路径，解析基准为配置文件所在目录。不得使用绝对路径——绝对路径会破坏工作区可移植性。

### `governance.enabled: true`

- **选入理由**：显式声明治理已启用，为 operator 提供可见性。虽然 `GovernanceConfig.enabled` 默认值也是 `True`，但 bootstrap 配置应让 operator 无需查阅默认值表即可明确知道治理状态。
- **省略后果**：不写也行（默认 `True`），但本地首次启动时 operator 无法从配置文件直接判断治理是否启用。
- **值约束**：布尔值。设为 `false` 时治理 API 写入端点返回 HTTP 400，检查端点返回空列表——本地 bootstrap 不应禁用治理。

### `observability.log_level: INFO`

- **选入理由**：显式声明日志级别，避免运行时日志级别不确定。`LOG_LEVEL` 环境变量可覆盖此值，但配置文件应提供稳定基线。
- **省略后果**：不写也行（默认 `"INFO"`），但 bootstrap 配置应让 operator 知道日志输出级别，利于首次排障。
- **值约束**：Python `logging` 模块识别的级别名称。v1 不定义额外的值域约束。

### `observability.metrics_enabled: true`

- **选入理由**：显式声明 `/metrics` 端点可用。`MetricsCollector` 在 `metrics_enabled=True` 时才会被创建并挂载到 `TimingMiddleware`。
- **省略后果**：不写也行（默认 `True`），但 bootstrap 应让 operator 知道 `/metrics` 端点可用。
- **值约束**：布尔值。设为 `false` 时 `GET /metrics` 返回错误消息。

## 明确排除项

以下内容不得出现在 bootstrap 生成的 `marivo.yaml` 中：

| 排除项 | 排除理由 |
|--------|----------|
| `sources` 顶层块 | `extra="forbid"` 会拒绝；CLAUDE.md 规定 `marivo.yaml` 是 runtime-only 配置，source 通过 HTTP API 注册 |
| `engines` 顶层块 | 同上 |
| `bindings` 顶层块 | 同上 |
| `mappings` 顶层块 | 同上 |
| `calendar` 顶层块 | 日历对齐非本地 bootstrap 必需；默认 `region_code="CN"`, `snapshots=[]` 足够；日历快照需要绑定已注册的 source，不应在 bootstrap 阶段预设 |
| `governance.policies` | 治理策略通过 HTTP API 动态注册，不在 runtime config 预定义；`_register_configured_governance()` 是可选的启动时注册，本地 bootstrap 不预填 |
| `governance.quality_rules` | 同上 |
| 任何 source/engine/mapping inventory | 与 CLAUDE.md 规则冲突；这些对象通过 HTTP API 管理，不属于 runtime config |

## 校验约束

bootstrap 生成的 `marivo.yaml` 必须满足以下约束：

1. **Pydantic 校验**：必须通过 `MarivoConfig.model_validate()` 校验，且 `extra="forbid"` 确保无多余字段。
2. **`metadata.engine`**：本地 bootstrap 仅写出字面量 `"sqlite"`；生产 MySQL metadata 已作为独立 shared metadata store 接入，但不改变本地 bootstrap 默认内容。
3. **`metadata.path`**：必须为相对路径，解析基准为配置文件所在目录（`<workspace_root>/.marivo/`）。
4. **YAML 合法性**：必须为合法 YAML 文档。

校验失败时：
- `serve-local` 退出码 2（配置无效）
- `doctor` 报告 `config_file` 检查失败
- `init-local` / `serve-local` 不覆盖已存在但损坏的配置文件

## Bootstrap 写入规则

1. **写入时机**：`init-local` 和 `serve-local` 仅在 `marivo.yaml` 不存在时写入。
2. **幂等性**：已存在的文件绝不覆盖——即使内容损坏。这防止误操作覆盖用户定制配置。
3. **文件位置**：写入 `<workspace_root>/.marivo/marivo.yaml`。
4. **文件权限**：`0644`（owner 读写，group/others 只读）。
5. **写入原子性**：实现应先写临时文件再重命名，或使用等价机制防止写入中断导致部分文件。

## 路径解析规则

`metadata.path` 的解析遵循以下规则：

```
metadata.path = ".marivo/metadata.sqlite"  （相对路径，写在 marivo.yaml 中）
配置文件位置   = "<workspace_root>/.marivo/marivo.yaml"
解析基准目录   = Path(config_path).parent = "<workspace_root>/.marivo/"
绝对路径       = "<workspace_root>/.marivo/" + ".marivo/metadata.sqlite"
              = "<workspace_root>/.marivo/metadata.sqlite"
```

此解析逻辑与 `app_factory.py` 中 `_resolve_storage()` 的 `config_path.parent / metadata_path` 行为一致。

`runtime.json` 的 `metadata_path` 字段由 `serve-local` 在写入 manifest 时将此相对路径解析为绝对路径填入。

## 与 `MarivoConfig` 默认值的关系

`MarivoConfig` 模型中，`metadata` 之外的块均有默认值：

| 块 | 默认值 | bootstrap 是否显式写出 | 理由 |
|----|--------|----------------------|------|
| `metadata` | `None` | **是**（必填） | 无此块则 `_resolve_storage()` 抛出 RuntimeError |
| `governance` | `GovernanceConfig(enabled=True, policies=[], quality_rules=[])` | **是**（显式 `enabled: true`） | 提供可见性；`policies` 和 `quality_rules` 使用默认空列表，不写出 |
| `observability` | `ObservabilityConfig(log_level="INFO", metrics_enabled=True)` | **是**（显式写出全部字段） | 提供排障可见性 |
| `calendar` | `CalendarConfig(default_region_code="CN", snapshots=[])` | 否 | 日历对齐非 bootstrap 必需 |

## 与现有契约的交叉引用

| 契约 | 本文与其关系 |
|------|-------------|
| [`agent-runtime-target-resolution-workspace-layout.zh.md`](./agent-runtime-target-resolution-workspace-layout.zh.md) | workspace-layout 定义了 `.marivo/marivo.yaml` 的创建/读取/写入语义和文件权限；本文定义写入内容的具体字段、理由与约束 |
| [`agent-runtime-target-resolution-cli-contract.zh.md`](./agent-runtime-target-resolution-cli-contract.zh.md) | cli-contract 定义了 `init-local` 和 `serve-local` 的命令规范与幂等性；本文定义这些命令写入 `marivo.yaml` 时必须使用的具体内容 |
| [`agent-runtime-target-resolution-runtime-manifest-schema.zh.md`](./agent-runtime-target-resolution-runtime-manifest-schema.zh.md) | manifest schema 定义了 `runtime.json` 的 `metadata_path` 字段；该字段由本文的 `metadata.path` 解析为绝对路径后填入 |
| [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md) | config-contract 定义了 `MARIVO_MODE`/`MARIVO_BASE_URL` 等 `marivo-mcp` 配置面；本文定义 `marivo.yaml` 运行时配置面——两者是独立的配置表面，不混用 |
| [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md) | 错误 taxonomy 定义了 `runtime_manifest_invalid`；本文的校验约束定义了触发该错误的配置层条件 |
| CLAUDE.md | 规定 `marivo.yaml` 是 runtime-only 配置，不添加 `sources`、`engines`、`bindings`、`mappings` inventory 块；本文的排除项是该规则的编码依据 |

## 不变量

1. **bootstrap 内容是确定性的**：`init-local` 和 `serve-local` 生成的 `marivo.yaml` 内容完全由本文规定，不得包含本文未定义的字段。

2. **不覆盖已有配置**：已存在的 `marivo.yaml` 绝不被覆盖——即使内容损坏。用户定制配置优先于 bootstrap 默认值。

3. **`metadata` 块是唯一必填块**：从 `MarivoConfig` 模型角度看，`governance` 和 `observability` 有默认值、`calendar` 有默认值；但从 bootstrap 角度看，显式写出 `governance.enabled` 和 `observability` 全部字段是为了 operator 可见性，不是因为模型要求。

4. **无 source/engine/mapping inventory**：bootstrap 配置不预填任何数据源、执行引擎或映射。这些对象通过 HTTP API 动态注册。

5. **相对路径基于配置文件目录解析**：`metadata.path` 必须为相对路径，解析基准为配置文件所在目录（`.marivo/`），与 `app_factory.py` 行为一致。
