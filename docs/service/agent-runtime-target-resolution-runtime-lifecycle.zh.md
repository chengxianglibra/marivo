# Agent Runtime Target Resolution v1 Runtime Lifecycle 契约

本文定义本地运行时的启动、复用、重启、停止和失败生命周期。它是 `marivo-mcp` runtime supervisor 和 `marivo core` CLI 实现本地运行时管理的唯一编码依据，后续实现不得在生命周期判定或重启策略上扩展本文未定义的行为。

manifest schema 见 [`agent-runtime-target-resolution-runtime-manifest-schema.zh.md`](./agent-runtime-target-resolution-runtime-manifest-schema.zh.md)。CLI 命令语义见 [`agent-runtime-target-resolution-cli-contract.zh.md`](./agent-runtime-target-resolution-cli-contract.zh.md)。错误结构见 [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md)。配置语义见 [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md)。工作区布局见 [`agent-runtime-target-resolution-workspace-layout.zh.md`](./agent-runtime-target-resolution-workspace-layout.zh.md)。

## 状态机

### 状态定义

| 状态 | 含义 | 触发条件 |
|------|------|----------|
| `no_manifest` | `runtime.json` 不存在 | 首次启动、或 `runtime stop` 清理后 |
| `manifest_valid_healthy` | manifest 有效、PID 存活、`/health` OK | 正常运行中 |
| `manifest_stale_pid_dead` | manifest 有效但 PID 不存活 | daemon 崩溃、被杀、或系统重启 |
| `manifest_stale_unhealthy` | manifest 有效、PID 存活但 `/health` 失败 | daemon 锁死、半初始化、或端口被占 |
| `manifest_invalid` | manifest JSON 无效或违反 schema | 文件损坏、手动篡改、版本不兼容 |
| `starting` | 正在启动新 daemon | `marivo-mcp` 调用 `serve-local` |
| `restart_attempted` | 已尝试一次重启且失败 | 重启后 daemon 仍不健康 |
| `failed` | 终态：所有恢复尝试已耗尽 | 重启失败或 manifest 无效 |

### 状态转移图

```
no_manifest ──────────────────────── starting ──┐
       │                                         │
       │              ┌──────────────────────────┘
       │              │
       │              ▼
       │        manifest_valid_healthy  ←── /health OK
       │              │
       │              │ PID 死亡
       │              ▼
       │    manifest_stale_pid_dead ──── starting
       │              │                      │
       │              │ 启动失败              │ /health OK
       │              ▼                      ▼
       │       restart_attempted       manifest_valid_healthy
       │              │
       │              │ 再失败
       │              ▼
       │           failed
       │
       │              /health 失败
       │    manifest_valid_healthy ─── manifest_stale_unhealthy
       │                                      │
       │                                      │ 尝试重启
       │                                      ▼
       │                                 starting
       │                                      │
       │                         ┌────────────┤
       │                         │            │
       │                    /health OK    启动失败
       │                         │            │
       │                         ▼            ▼
       │               manifest_valid_healthy  restart_attempted
       │                                            │
       │                                            │ 再失败
       │                                            ▼
       │                                         failed
       │
       │  manifest 无效（不触发自动启动）
       │  no_manifest / manifest_stale_* ─── manifest_invalid ─── failed
       │
```

## 读取-校验-决策流程

当 `marivo-mcp` 在 `local` 或 `auto`→`local` 模式下启动时，执行以下流程：

```
1. 解析 workspace_root
   失败 → workspace_root_required（见 T1.3 优先级链）

2. 构造 manifest 路径：<workspace_root>/.marivo/runtime.json

3. 尝试读取 manifest
   文件不存在 → 跳到步骤 8（启动新 daemon）

4. 执行 schema 校验链（见 manifest schema 契约的消费者校验链）
   步骤 1-5（JSON 解析 → 必要字段 → 类型格式 → 额外字段 → 跨字段不变量）
   任一失败 → runtime_manifest_invalid
   → 不触发自动启动，要求用户干预
   → 流程终止

5. PID 校验：os.kill(manifest.pid, 0)
   ProcessLookupError / OSError → PID 已死亡
   → 跳到步骤 8（启动新 daemon）
   PermissionError → PID 存活，继续步骤 6

6. /health 校验：GET {manifest.base_url}/health
   超时 = healthcheck_timeout_ms
   返回 {"status": "ok"} → manifest 有效且健康
   → 复用该 runtime，使用 manifest.base_url
   → 流程完成

7. /health 失败
   → manifest 过期（进程不健康）
   → 检查 restart_attempted 标志
   → 若 restart_attempted == False：
       跳到步骤 9（受控重启）
   → 若 restart_attempted == True：
       报 local_runtime_start_failed
       detail.workspace_root = workspace_root
       detail.health_checked = True
       detail.exit_code = None
       → 流程终止

8. 启动新 daemon
   a. 调用 marivo serve-local --workspace-root <workspace_root> --format json
   b. 等待子进程退出
   c. 子进程退出码 0：
      - 重新读取 manifest（serve-local 已写入）
      - 对新 manifest 执行 schema 校验链
      - 校验成功 → 复用该 runtime
      - 校验失败 → runtime_manifest_invalid（不应该发生，但防御性处理）
   d. 子进程退出码非 0：
      - 映射到对应 TargetResolutionError（见"失败分类"章节）
      - 流程终止

9. 受控重启
   a. 设置 restart_attempted = True（进程内标志，不持久化）
   b. 先调用 marivo runtime stop --workspace-root <workspace_root>
      - 清理旧 manifest 和 PID 文件，避免 serve-local 误判
   c. 再调用 marivo serve-local --workspace-root <workspace_root> --format json
   d. 等待子进程退出
   e. 子进程退出码 0：
      - 重新读取 manifest
      - 对新 manifest 执行 schema 校验链
      - 校验成功 → 复用该 runtime
      - 校验失败 → runtime_manifest_invalid
   f. 子进程退出码非 0：
      - 报 local_runtime_start_failed
      - 流程终止
```

### 重启尝试追踪

`restart_attempted` 是 `marivo-mcp` 进程内的内存布尔标志，不持久化到磁盘。理由：

- 生命周期绑定于 `marivo-mcp` 进程实例。进程重启后重置为 `False`，允许重试
- 持久化到 manifest 会引入生产者/消费者耦合——manifest 的职责是运行时发现，不是重启状态追踪
- 不影响并发安全性——每个 `marivo-mcp` 实例独立追踪自己的重启尝试

## PID 校验语义

### `os.kill(pid, 0)` 行为

| 异常 | 含义 | 处理 |
|------|------|------|
| 无异常 | 进程存在且当前用户有权发信号 | PID 存活 |
| `ProcessLookupError` | 进程不存在 | PID 已死亡 |
| `PermissionError` | 进程存在但当前用户无权发信号 | PID 存活 |
| `OSError`（其他） | 系统级错误（如无效 PID） | PID 已死亡 |

`PermissionError` 被视为"PID 存活"的理由：进程确实存在，仅当前用户无权向其发信号。这在不同用户运行 `marivo-mcp` 和 daemon 时可能出现。

### PID 复用风险

OS 可能将已死亡 daemon 的 PID 分配给完全不同的进程。风险缓解：

1. PID 校验后必须紧跟 `/health` 校验
2. `/health` 校验使用 manifest 中的 `base_url`，该 URL 绑定到 daemon 实际监听的端口
3. 即使 PID 被复用，非 Marivo 进程不会在 daemon 端口上返回 `{"status": "ok"}`
4. 两者联合校验构成"PID 存在 + 端口响应正确"的双重验证

## `/health` 校验

### 请求规格

```
GET {base_url}/health
Accept: application/json
Connection: close
```

超时：`healthcheck_timeout_ms`（默认 2000ms），包含连接超时和读取超时。

### 响应判定

| 响应 | 判定 |
|------|------|
| HTTP 200，body 包含 `{"status": "ok"}` | 健康 |
| HTTP 200，body 不含 `{"status": "ok"}` | 不健康（防御性处理，不应发生） |
| HTTP 非 200 | 不健康 |
| 连接超时 | 不健康 |
| 连接拒绝 | 不健康（daemon 未监听或已崩溃） |

### `serve-local` 启动时的轮询

`serve-local` 在启动 daemon 后轮询 `/health`：

```
1. 记录开始时间 t0
2. 循环：
   a. GET /health，超时 healthcheck_timeout_ms
   b. 成功 → 跳出循环
   c. 失败 → 检查 elapsed = now - t0
   d. elapsed >= start_timeout_ms → 启动超时，退出码 5
   e. 否则等待 200ms，继续循环
3. 写入 runtime.json（见 manifest schema 契约 P-1）
```

### `marivo-mcp` 复用时的单次检查

`marivo-mcp` 复用已有 manifest 时执行单次 `/health` 请求（不轮询）。单次失败即判定为不健康，进入重启或失败路径。

## 受控重启

### 触发条件

仅 `manifest_stale_unhealthy`（PID 存活但 `/health` 失败）触发受控重启。

`manifest_stale_pid_dead`（PID 已死亡）不触发"重启"，而是启动新 daemon（步骤 8）。理由：PID 死亡意味着旧 daemon 已不存在，无需先停止。

`manifest_invalid` 不触发任何自动操作（见下文）。

### 重启流程

```
1. restart_attempted = True
2. marivo runtime stop --workspace-root <workspace_root>
   - 向旧 PID 发送 SIGTERM
   - 等待进程退出（最多 5000ms，见 CLI 契约）
   - 删除 runtime.json 和 marivo.pid
3. marivo serve-local --workspace-root <workspace_root> --format json
4. 等待子进程退出
5. 退出码 0 → 读取新 manifest → 复用
6. 退出码非 0 → local_runtime_start_failed，流程终止
```

### 一次重启限制

v1 限制最多一次受控重启尝试。第二次失败直接报错，不重试。理由：

- 连续失败通常意味着系统性问题（端口冲突、配置损坏、权限不足），重试不会解决
- 避免在启动循环中浪费用户时间
- 用户可通过 `marivo doctor` 诊断根因后手动修复

## 无效 manifest 处理

### 不触发自动启动

当 manifest 被判定为 `runtime_manifest_invalid` 时，`marivo-mcp` 不自动调用 `serve-local`。理由：

1. 无效 manifest 意味着工作区状态不可靠（文件损坏、手动篡改、schema 版本不兼容）
2. 在不可靠状态上自动启动新 daemon 可能：
   - 覆盖用户手动定制的配置
   - 在损坏的 metadata 上执行操作导致数据丢失
   - 掩盖根本问题，使排障更困难
3. 用户干预路径：
   - 运行 `marivo doctor` 诊断
   - 删除无效 manifest：`rm <workspace_root>/.marivo/runtime.json`
   - 重新连接 `marivo-mcp`（将触发步骤 8：启动新 daemon）

### 错误映射

```
runtime_manifest_invalid
  code = "runtime_manifest_invalid"
  detail.manifest_path = "<workspace_root>/.marivo/runtime.json"
  detail.parse_error = 具体校验失败原因
  detail.missing_fields = 缺失字段列表（若适用）
  guidance = "请运行 marivo doctor 诊断，或删除 {manifest_path} 重试"
```

## 失败分类

### `serve-local` 退出码映射

| `serve-local` 退出码 | 含义 | `marivo-mcp` 映射 |
|----------------------|------|-------------------|
| 0 | daemon 运行中且健康 | 读取新 manifest → 复用 |
| 1 | 通用启动失败 | `local_runtime_start_failed`，`exit_code=1` |
| 2 | 配置无效 | `local_runtime_start_failed`，`exit_code=2` |
| 3 | workspace root 不可用 | `workspace_root_required`（不应发生——步骤 1 已校验） |
| 5 | 健康检查失败 | `local_runtime_start_failed`，`exit_code=5`，`health_checked=True` |
| 6 | 端口不可用 | `local_runtime_start_failed`，`exit_code=6` |
| 10 | 无效参数 | `local_runtime_start_failed`，`exit_code=10`（不应发生） |

### `local_runtime_start_failed` detail 字段

| 场景 | `workspace_root` | `timeout_ms` | `exit_code` | `health_checked` |
|------|------------------|--------------|-------------|------------------|
| daemon 启动后健康检查超时 | 当前 workspace | `start_timeout_ms` | `None` | `True` |
| daemon 进程异常退出 | 当前 workspace | `start_timeout_ms` | 实际退出码 | `False` |
| 重启后仍不健康 | 当前 workspace | `start_timeout_ms` | `None` | `True` |
| 端口不可用 | 当前 workspace | `start_timeout_ms` | 6 | `False` |

### 完整失败路径映射

| 失败路径 | 错误码 | 触发位置 |
|----------|--------|----------|
| workspace root 无法解析 | `workspace_root_required` | 步骤 1 |
| manifest JSON 无效 | `runtime_manifest_invalid` | 步骤 4 |
| manifest 缺少字段 | `runtime_manifest_invalid` | 步骤 4 |
| manifest 跨字段不变量违反 | `runtime_manifest_invalid` | 步骤 4 |
| 首次启动 daemon 失败 | `local_runtime_start_failed` | 步骤 8 |
| 重启 daemon 失败 | `local_runtime_start_failed` | 步骤 9 |
| 重启后 daemon 仍不健康 | `local_runtime_start_failed` | 步骤 7（`restart_attempted == True`） |

## 超时语义

### 两个超时值

| 超时 | 来源 | 默认值 | 含义 |
|------|------|--------|------|
| `start_timeout_ms` | 配置契约 | 15000 | daemon 启动的总时间预算（从派生子进程到健康检查通过） |
| `healthcheck_timeout_ms` | 配置契约 | 2000 | 单次 `/health` HTTP 请求超时（连接 + 读取） |

### 关系

- `start_timeout_ms` 必须大于 `healthcheck_timeout_ms`。若配置违反此约束，`marivo-mcp` 启动时应报 `config_invalid` 错误
- `serve-local` 在 `start_timeout_ms` 内以 200ms 间隔轮询 `/health`，每次请求超时 `healthcheck_timeout_ms`
- `marivo-mcp` 复用检查时执行单次 `/health`，超时 `healthcheck_timeout_ms`

### `runtime stop` 的超时

`runtime stop` 的 `--timeout-ms`（默认 5000ms）控制 SIGTERM 后等待优雅退出的时间。此超时独立于上述两个超时值。

## 并发考量

### 多个 `marivo-mcp` 实例同时启动

v1 采用"最后写入者胜出"策略，不引入分布式锁：

```
1. 两个 marivo-mcp 实例同时发现无健康 runtime
2. 两者都调用 serve-local
3. 两个 daemon 各自绑定不同 OS 分配端口
4. 两者各自写入 runtime.json
5. 后写入者覆盖先写入者的 manifest
6. 先启动的 daemon 成为孤儿进程（无 manifest 指向它）
```

后果：

- 先启动的 daemon 继续运行但不再被引用，占用端口和内存
- 用户可通过 `marivo doctor` 发现孤儿进程（PID 存活但不在 manifest 中）
- 孤儿进程无害——它只监听 localhost，不暴露数据
- 用户可手动终止：`kill <pid>` 或 `marivo runtime stop`

v1 不解决此问题的理由：

- 本地开发场景下，同时启动多个 `marivo-mcp` 实例是罕见情况
- 引入分布式锁（如 PID 文件锁）增加复杂度和新的失败面
- 孤儿进程无害，不需要紧急清理

### `serve-local` 与 `runtime stop` 并发

`serve-local` 在写入 manifest 前不检查是否有其他 `serve-local` 实例正在运行。`runtime stop` 在删除 manifest 前不检查是否有 `serve-local` 正在启动。

v1 接受此竞态窗口。后果：

- `runtime stop` 删除 manifest 后，`serve-local` 可能正在写入新 manifest
- 最终结果取决于写入顺序
- 用户可通过 `marivo runtime status` 确认最终状态

## `marivo-mcp` 退出与 daemon 生命周期

### `marivo-mcp` 退出不停止 daemon

`marivo-mcp` 进程退出时**不**停止本地 daemon。理由：

1. daemon 是工作区作用域的，独立于任何特定 `marivo-mcp` 实例
2. 用户可能同时运行多个 agent 连接同一个本地 daemon
3. "本地自动托管"的用户心智模型是"注册即用"——daemon 在后台持续运行
4. `marivo-mcp` 重启后可直接复用已有 daemon（通过读取 `runtime.json`）

### daemon 终止方式

daemon 在以下情况下终止：

| 终止方式 | 触发者 | 清理 |
|----------|--------|------|
| `marivo runtime stop` | 用户或 operator | 删除 `runtime.json` + `marivo.pid` |
| `SIGTERM` / `SIGKILL` | 系统或用户 | `runtime.json` 可能残留（过期状态） |
| 系统关机 | OS | `runtime.json` 可能残留（过期状态） |
| daemon 崩溃 | 自身 | `runtime.json` 可能残留（过期状态） |

残留的 `runtime.json` 在下次 `marivo-mcp` 启动时被识别为过期状态（PID 不存活），触发新 daemon 启动。

## `runtime stop` 清理语义

### 删除的文件

| 文件 | 删除 | 理由 |
|------|------|------|
| `.marivo/runtime.json` | 是 | 权威运行时 manifest，删除表示"无运行中实例" |
| `.marivo/run/marivo.pid` | 是 | PID 文件，删除表示"无 daemon 追踪" |

### 保留的文件

| 文件 | 保留 | 理由 |
|------|------|------|
| `.marivo/marivo.yaml` | 是 | 用户配置，可能已定制；`serve-local` 可复用 |
| `.marivo/metadata.sqlite` | 是 | 业务数据，删除意味着数据丢失 |
| `.marivo/logs/` | 是 | 诊断数据，`doctor` 和 operator 可能需要 |
| `.marivo/logs/marivo.log` | 是 | 同上 |

### 后置条件

`runtime stop` 成功后（退出码 0）：

- daemon 进程已终止
- `.marivo/runtime.json` 不存在
- `.marivo/run/marivo.pid` 不存在
- `.marivo/marivo.yaml` 仍存在且未被修改
- `.marivo/metadata.sqlite` 仍存在且未被修改
- 下次 `marivo-mcp` 启动将进入 `no_manifest` 状态，触发新 daemon 启动

## 与 `marivo core` CLI 的分工

| 职责 | `marivo core` CLI | `marivo-mcp` runtime supervisor |
|------|-------------------|-------------------------------|
| 启动 daemon | `serve-local`：派生进程、健康检查、写入 manifest | 调用 `serve-local` 子进程 |
| 停止 daemon | `runtime stop`：SIGTERM、清理文件 | 不直接参与停止 |
| 检查状态 | `runtime status`：读取 manifest、校验 PID + `/health` | 读取 manifest（直接文件 I/O） |
| 诊断问题 | `doctor`：全面检查 | 通过 CLI 退出码和错误码推断 |
| 写入 manifest | `serve-local`：原子写入 | 只读消费 |
| 删除 manifest | `runtime stop`：删除 | 不删除 manifest |
| 重启决策 | — | 读取 manifest → 判断状态 → 决定复用/重启/失败 |

`marivo-mcp` 只监督启动流程，不内嵌第二套应用启动栈。

## 不变量

1. **读取 manifest 优先于启动**：`marivo-mcp` 必须先检查是否有可复用的运行时，再决定是否启动新 daemon。不允许跳过检查直接启动。

2. **一次重启限制**：每个 `marivo-mcp` 进程实例对同一 workspace 仅允许一次受控重启。第二次失败直接报错。

3. **无效 manifest 不触发自动启动**：`runtime_manifest_invalid` 不触发 `serve-local`。用户必须干预。

4. **远程不回退**：`remote` 模式下远程不可达不触发本地启动。`auto` 模式下 `MARIVO_BASE_URL` 已设置时也不触发。

5. **manifest 写入仅在健康检查通过后**：`serve-local` 退出码 0 时 `runtime.json` 必须存在且反映当前运行状态。

6. **`marivo-mcp` 退出不影响 daemon**：daemon 生命周期独立于 `marivo-mcp` 进程。

7. **PID 校验 + `/health` 校验联合**：单独的 PID 存活不能证明 daemon 健康。必须两者都通过才可复用。

8. **`start_timeout_ms` > `healthcheck_timeout_ms`**：总启动超时必须大于单次健康检查超时。违反此约束为配置错误。

## 与其他契约的关系

| 契约 | 本文与其关系 |
|------|-------------|
| [`agent-runtime-target-resolution-runtime-manifest-schema.zh.md`](./agent-runtime-target-resolution-runtime-manifest-schema.zh.md) | manifest schema 契约定义了 `runtime.json` 的校验链和字段规则；本文定义何时读取、如何判定状态、何时复用或重启。本文引用其校验链和错误映射 |
| [`agent-runtime-target-resolution-cli-contract.zh.md`](./agent-runtime-target-resolution-cli-contract.zh.md) | CLI 命令契约定义了 `serve-local` 的退出码和 stdout JSON；本文将这些退出码映射到 `TargetResolutionError`。`runtime stop` 的清理语义来自 CLI 契约的副作用定义 |
| [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md) | 错误 taxonomy 定义了 `runtime_manifest_invalid`、`local_runtime_start_failed`、`workspace_root_required` 的结构；本文定义触发这些错误的具体条件和 `detail` 字段填充规则 |
| [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md) | 配置契约定义了 `start_timeout_ms`、`healthcheck_timeout_ms`、`local_host`、`local_port`；本文定义这些超时值在启动流程中的使用语义 |
| [`agent-runtime-target-resolution-workspace-layout.zh.md`](./agent-runtime-target-resolution-workspace-layout.zh.md) | 工作区布局定义了 `runtime.json` 和 `marivo.pid` 的文件生命周期；本文定义这些文件在运行时发现和生命周期管理中的角色 |
