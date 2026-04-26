# Agent 运行时目标解析

本文档描述 Marivo 在 agent 场景下的目标解析与本地运行时设计。它是一份服务/运行时设计说明，不是对外 HTTP API 契约。

v1 产品边界与用户心智模型已冻结，见 [`agent-runtime-target-resolution-v1-scope-note.zh.md`](./agent-runtime-target-resolution-v1-scope-note.zh.md)。v1 配置语义与解析规则已冻结，见 [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md)。v1 workspace root 解析优先级已冻结，见 [`agent-runtime-target-resolution-workspace-root.zh.md`](./agent-runtime-target-resolution-workspace-root.zh.md)。v1 失败面 taxonomy 已冻结，见 [`agent-runtime-target-resolution-error-taxonomy.zh.md`](./agent-runtime-target-resolution-error-taxonomy.zh.md)。v1 HTTP MCP 适用边界已冻结，见 [`agent-runtime-target-resolution-http-mcp-boundary.zh.md`](./agent-runtime-target-resolution-http-mcp-boundary.zh.md)。v1 `marivo core` CLI 命令面已冻结，见 [`agent-runtime-target-resolution-cli-contract.zh.md`](./agent-runtime-target-resolution-cli-contract.zh.md)。v1 工作区 `.marivo/` 布局已冻结，见 [`agent-runtime-target-resolution-workspace-layout.zh.md`](./agent-runtime-target-resolution-workspace-layout.zh.md)。v1 最小本地配置 bootstrap 已冻结，见 [`agent-runtime-target-resolution-bootstrap-config.zh.md`](./agent-runtime-target-resolution-bootstrap-config.zh.md)。

本文档同时覆盖两层内容：

- 用户接入视角：用户在 agent 中安装、注册、使用 `marivo-mcp` 时，应该看到什么最小心智模型
- 运行时设计视角：`marivo-mcp` 如何在本地和远程 Marivo 之间做目标解析，并保持统一 HTTP 边界

本文档描述当前 v1 已实现的 agent 运行时目标解析模型。命令、环境变量和失败面应与当前代码保持一致；面向用户的安装和 smoke 操作细节以 [`marivo-mcp/README.md`](../../marivo-mcp/README.md) 与 [`marivo-mcp/docs/release-checklist.md`](../../marivo-mcp/docs/release-checklist.md) 为准。

## 目标

- 为本地和远程 Marivo 使用场景保留一条统一的 agent 集成路径
- 保持 HTTP 作为唯一的规范执行边界
- 保持 routing/compile 只通过 `authority_locator` + ready mapping 解析执行目标
- 在默认场景下，无需用户手动启动本地 Marivo 服务
- 让远程配置显式且可预测
- 避免任何可能掩盖 agent 实际在使用本地还是远程状态的静默回退

## 非目标

- 定义第二套 CLI 原生分析契约
- 允许 MCP 或 agent 技能直接写入 metadata SQLite
- 暴露一个绕过 HTTP 路由的独立本地执行引擎
- 针对人工驱动的分析工作流做优化

## 数据平面边界

Marivo 仍然保持 HTTP-only。Agent 通过 `marivo-mcp` 与 Marivo 交互，`marivo-mcp` 再通过规范 HTTP API 与 Marivo 交互。本地与远程的差异，只体现在目标 Marivo 服务的解析方式上。

Agent 解析分析目标时必须保持数据平面边界不变：

- source object identity 来自 `source_object.authority_locator`
- source-to-engine projection 来自 ready mapping
- 不得从 engine `default_namespace`、source connection 默认值或历史 source-engine binding 推断 execution-side locator

## 面向用户的最小接入模型

### 用户只需要理解两种接入结果

对最终用户，推荐只暴露两种结果：

- 本地自动托管：不填写远程地址，由系统管理当前工作区的本地 Marivo
- 远程显式连接：填写远程 `base_url`，系统只连接该远程 Marivo，失败时直接报错

`local` 可以作为运行时内部或高级调试模式存在，但不应作为默认用户路径的第三种常规心智模型。默认用户文案应收敛为“本地自动托管”与“远程显式连接”。

### 用户只需要知道的事实

- agent 只注册一个 MCP server，例如 `marivo`
- agent 连接的是 `marivo-mcp`，不是 Marivo 本身
- `marivo-mcp` 再去连接 HTTP Marivo
- 不填远程地址时默认走本地自动托管
- 填了远程地址时必须走远程，失败时明确报错，不回退本地

### 用户不应该被迫理解的内部细节

除非进入高级调试场景，用户不应被迫显式配置以下细节：

- `MARIVO_LOCAL_HOST`
- `MARIVO_LOCAL_PORT`
- `MARIVO_START_TIMEOUT_MS`
- `MARIVO_HEALTHCHECK_TIMEOUT_MS`
- `runtime.json` 路径与复用逻辑
- 本地 daemon 的启动与回收细节

### 用户真正获得的能力边界

“注册即用”只表示运行时接通，不表示数据面已经 ready。即使本地或远程 Marivo 可以被成功连接，用户仍然需要显式完成 source、engine、mapping 的配置，分析链路才真正可用。

因此，用户侧文案应避免把“安装成功”表达成“已经可以直接分析任何数据”。更准确的说法应是：

- 已完成 agent 到 Marivo 的连接
- 如需实际分析，仍需先配置 source、engine、mapping，并使其 ready

## 用户安装与注册边界

### 安装发生在哪个环境

`marivo-mcp` 安装在 MCP server 的宿主环境，而不是抽象地“安装在 agent 里”。

用户接入时至少要能回答两个问题：

- 谁来执行 `marivo-mcp`
- agent 通过什么方式找到这个 `marivo-mcp`

因此，文档必须明确区分：

- 本地子进程 MCP：agent 通过本地命令启动 `marivo-mcp`
- HTTP MCP：agent 通过 URL 连接一个已运行的 `marivo-mcp` HTTP 服务

### 本地子进程 MCP

如果 agent 支持注册本地命令，则用户在 MCP server 宿主环境中安装 `marivo-mcp`，再把该命令注册到 agent。

当前版本提供统一入口：

```bash
marivo-mcp init
```

负责生成或写入最终的 MCP 配置，而不是要求用户手工拼接环境变量。

### HTTP MCP

如果 agent 不能直接拉起本地子进程 MCP，则需要先部署一个 `marivo-mcp` HTTP 端点，再让 agent 注册该 URL。

这里需要显式说明一个边界：

- HTTP MCP 适合远程显式连接场景
- HTTP MCP 只有在服务端能够确定工作区上下文、且具备本地文件系统访问能力时，才适合做本地自动托管

因此，“HTTP MCP + 本地自动拉起工作区 Marivo”不是默认假设。若服务端无法稳定获得工作区根目录，本地自动托管必须明确失败，而不是猜测某个目录继续运行。

## 规范交互模型

规范的 agent 交互流程如下：

```text
Agent -> marivo-mcp -> target resolution -> HTTP Marivo
```

目标解析会在以下两者中选择其一：

- 已配置的远程 Marivo 服务
- 自动管理的工作区本地 Marivo daemon

从 agent 视角看，两种模式都使用同一套 MCP 工具，以及同样的 Marivo HTTP 行为。

## 设计原则

### 1. 单一 agent 入口

Agent 始终连接到 `marivo-mcp`。不需要在“本地 Marivo 适配器”和“远程 Marivo 适配器”之间做选择。

### 2. 单一协议边界

所有业务行为都必须保留在规范 HTTP 路由之后。MCP 是客户端侧适配器，不能演变成第二个执行面。

### 3. 目标解析与能力执行分离

本地还是远程 Marivo 的选择，发生在任何工具调用发出之前。目标解析完成之后，typed intent、语义写入和规范读取都应保持一致。

### 4. 显式远程优先

如果调用方显式配置了远程 Marivo 服务，`marivo-mcp` 就必须使用该远程目标，或者明确失败。它不能静默回退到本地模式。

### 5. 零摩擦本地模式

如果未配置远程目标，`marivo-mcp` 应自动创建或复用一个带有工作区作用域状态的本地 Marivo 运行时。

## 用户可见配置模型

虽然运行时支持更完整的环境变量集合，但普通用户侧应收敛为最少配置项：

- `base_url`
- `api_token`
- `workspace_root`

推荐映射关系：

- 若提供 `base_url`，则进入远程显式连接
- 若未提供 `base_url`，则进入本地自动托管
- `workspace_root` 应优先由初始化命令或 agent 集成层显式写入
- `api_token` 仅在远程服务要求鉴权时出现

推荐的用户可见配置形态可以抽象为：

```yaml
marivo:
  base_url: null
  api_token: null
  workspace_root: /abs/path/to/workspace
```

底层再由 `marivo-mcp` 将其展开为实际环境变量。

## 运行时配置模型

运行时内部推荐支持如下环境变量：

```text
MARIVO_MODE=auto|remote|local
MARIVO_BASE_URL=
MARIVO_API_TOKEN=
MARIVO_CONFIG=
MARIVO_WORKSPACE_ROOT=
MARIVO_LOCAL_HOST=127.0.0.1
MARIVO_LOCAL_PORT=0
MARIVO_START_TIMEOUT_MS=15000
MARIVO_HEALTHCHECK_TIMEOUT_MS=2000
MARIVO_MCP_TRANSPORT=stdio|streamable-http
MARIVO_MCP_HOST=127.0.0.1
MARIVO_MCP_PORT=8000
MARIVO_MCP_STREAMABLE_HTTP_PATH=/mcp
```

语义：

- `MARIVO_MODE=auto`
  - 如果设置了 `MARIVO_BASE_URL`，则使用远程模式
  - 否则使用本地模式
- `MARIVO_MODE=remote`
  - 必须提供 `MARIVO_BASE_URL`
  - 如果远程服务不可用则失败
- `MARIVO_MODE=local`
  - 忽略 `MARIVO_BASE_URL`
  - 强制执行本地运行时发现或启动

这里的 `local` 属于运行时内部和高级调试能力，不应成为默认用户文案里的第三种常规模式。

因此，`MARIVO_BASE_URL` 是目标提示，不是第二种传输模式。

## 工作区解析规则

本地自动托管成立的前提，是 `marivo-mcp` 能稳定解析到唯一工作区根目录。推荐优先级如下：

```text
1. 显式的 MARIVO_WORKSPACE_ROOT
2. agent 集成层传入的 workspace 元数据
3. MCP 进程启动时的当前工作目录
4. 若以上都不存在，则明确失败
```

必须满足以下约束：

- 不允许在拿不到工作区根目录时猜测某个目录继续运行
- 不允许在多个候选工作区之间静默挑选一个
- HTTP MCP 若无法从请求或部署配置中确定工作区根目录，则不能进入本地自动托管

## 目标解析规则

目标解析算法应如下：

```text
1. 读取 MARIVO_MODE，默认值为 auto。
2. 如果 mode=remote:
   - 要求存在 MARIVO_BASE_URL
   - 连接到该远程目标
   - 若不可达则失败
3. 如果 mode=local:
   - 要求能解析到 workspace_root
   - 解析或启动本地运行时
4. 如果 mode=auto:
   - 若存在 MARIVO_BASE_URL，则按远程处理
   - 否则按本地处理
```

### 必须满足的不变量

- 已配置的远程目标绝不能静默回退到本地
- 本地解析绝不能静默切换到某个任意不同的 endpoint
- 无法确定工作区根目录时绝不能隐式进入本地模式
- MCP 启动时应清晰记录解析后的目标

## 工作区作用域的本地运行时

本地模式默认应使用工作区作用域的状态，而不是单一的用户级 daemon。这样可以让 metadata、session 和日志与当前项目保持一致，避免不同项目之间的状态被意外混用。

当前工作区布局如下：

```text
<workspace>/.marivo/
  marivo.yaml
  metadata.sqlite
  runtime.json
  logs/
    marivo.log
  run/
    marivo.pid
```

推荐含义：

- `marivo.yaml`：本地运行时配置
- `metadata.sqlite`：本地 metadata 存储
- `runtime.json`：`marivo-mcp` 用于运行时发现的锚点文件
- `logs/`：本地运行时诊断日志
- `run/`：可选的 pid 和锁文件目录

## 运行时清单

`runtime.json` 是 `marivo-mcp` 与受管本地 Marivo daemon 之间的本地发现契约。

当前结构：

```json
{
  "version": "0.1.0",
  "workspace_root": "/abs/path/to/workspace",
  "mode": "local",
  "base_url": "http://127.0.0.1:48231",
  "host": "127.0.0.1",
  "port": 48231,
  "pid": 12345,
  "started_at": "2026-04-21T10:20:30Z",
  "config_path": "/abs/path/to/workspace/.marivo/marivo.yaml",
  "metadata_path": "/abs/path/to/workspace/.marivo/metadata.sqlite"
}
```

要求：

- `base_url` 必须是 MCP 在本地解析完成后实际使用的精确 endpoint
- `pid` 仅作提示用途，复用前必须重新校验
- `version` 应与生成该清单的本地 Marivo 运行时版本一致
- 对于过期或无效的清单，应在成功重启后覆盖写入

## 本地运行时启动流程

当选择本地模式时，`marivo-mcp` 会：

```text
1. 解析工作区根目录。
2. 确保 .marivo/ 存在。
3. 如果存在，则读取 .marivo/runtime.json。
4. 如果 runtime.json 存在：
   - 如果存在 pid，则校验该 pid
   - 对 base_url 调用 GET /health
   - 若健康则直接复用
5. 否则启动本地 Marivo daemon。
6. 轮询 GET /health，直到成功或启动超时。
7. 写入或刷新 runtime.json。
8. 后续 MCP 工具调用复用该 base_url。
```

MCP 层应作为本地运行时发现与启动的轻量级监督器，而不是第二个分析引擎。

## 本地 daemon 启动契约

为了保持运行时管理稳定，Marivo 暴露一个显式的本地启动入口：

```text
marivo serve-local
```

当前行为：

- 创建或校验工作区作用域的 `.marivo/marivo.yaml`
- 创建 `.marivo/logs/` 与 `.marivo/run/`
- 确保 metadata SQLite 存在于配置的本地路径
- 选择绑定的 host 和 port
- 启动规范 HTTP 服务
- 等待 `/health` 成功后写入 `runtime.json` 和 `run/marivo.pid`

`marivo-mcp` 应调用这个入口，而不是内嵌 Marivo 内部启动流程的深层细节。

## 默认本地配置

本地模式在常见路径上应做到零配置。首次启动时，Marivo 应落地一份最小的工作区本地配置。最小配置已冻结，见 [`agent-runtime-target-resolution-bootstrap-config.zh.md`](./agent-runtime-target-resolution-bootstrap-config.zh.md)。

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

数据源与执行引擎注册仍然需要显式完成，但运行时启动路径不应要求用户在 agent 连接之前手工编写本地文件。

## 用户接入流程

以下内容描述当前 v1 中，用户如何在自己的 agent 运行环境中安装并注册 `marivo-mcp`。

### 1. 默认接入路径

用户优先通过一个统一入口完成初始化：

```bash
marivo-mcp init
```

当前语义：

- 如果初始化参数中提供了 `--base-url`，则注册远程显式连接
- 如果未提供 `--base-url`，则注册本地自动托管
- 输出给用户的文案应直接解释最终结果，而不是只打印环境变量

### 2. 本地自动托管路径

如果 agent 支持本地子进程 MCP，用户可在 MCP server 宿主环境中安装 `marivo-mcp`，再执行：

```bash
marivo-mcp init
```

该命令的职责为：

- 检测当前工作区或要求用户显式提供 `workspace_root`
- 生成或更新最小 MCP 注册配置
- 默认把 MCP server 名称注册为 `marivo`
- 生成本地自动托管所需的最小环境变量
- 不要求用户手工创建 `.marivo/`
- 不要求用户手工启动本地 Marivo

如果用户的 agent 支持直接读取 MCP 配置文件，初始化命令能输出或写入类似配置：

```json
{
  "mcpServers": {
    "marivo": {
      "command": "marivo-mcp",
      "env": {
        "MARIVO_MODE": "auto",
        "MARIVO_WORKSPACE_ROOT": "/abs/path/to/workspace"
      }
    }
  }
}
```

用户完成注册并重启 agent 后，`marivo-mcp` 应自动：

- 解析 `<workspace>/.marivo/`
- 发现并复用已有本地运行时
- 在无可用运行时时调用 `marivo serve-local`
- 完成健康检查并写入 `runtime.json`

### 3. 远程显式连接路径

团队用户可以在只提供远程地址的前提下完成接入。

统一入口支持：

```bash
marivo-mcp init --base-url http://team-marivo:8000
```

若远程服务需要鉴权，则应支持：

```bash
marivo-mcp init \
  --base-url http://team-marivo:8000 \
  --api-token $MARIVO_API_TOKEN
```

生成配置类似：

```json
{
  "mcpServers": {
    "marivo": {
      "command": "marivo-mcp",
      "env": {
        "MARIVO_MODE": "remote",
        "MARIVO_BASE_URL": "http://team-marivo:8000",
        "MARIVO_API_TOKEN": "<token>"
      }
    }
  }
}
```

在该模式下：

- `marivo-mcp` 必须连接该远程服务
- 若远程不可达，必须明确失败
- 绝不能静默回退到本地模式

### 4. HTTP MCP 路径

对于不能直接拉起本地子进程 MCP 的 agent，用户可以先部署 `marivo-mcp` HTTP 端点，再在 agent 中注册 URL。

远程显式连接是 Streamable HTTP MCP 的默认发布路径：

```bash
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://team-marivo:8000 \
MARIVO_API_TOKEN=$MARIVO_API_TOKEN \
marivo-mcp-http
```

对应的 agent 注册配置应类似：

```json
{
  "mcpServers": {
    "marivo": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

这里的核心仍然不变：agent 只注册 `marivo-mcp`，不直接注册 Marivo 本身。

“HTTP MCP + 本地自动托管”必须额外满足：

- HTTP MCP 服务端可以稳定获得唯一工作区根目录
- 服务端对该工作区有本地文件系统访问能力
- 服务端能够安全隔离不同工作区的本地运行时状态

当前实现要求 HTTP MCP 本地自动托管显式设置 `MARIVO_WORKSPACE_ROOT`。如果缺少稳定 workspace root，启动必须以 `workspace_root_required` 失败，而不是使用任意 cwd 或回退到远程/本地其他目标。

## 初始化命令职责

为了尽量简化用户安装、注册、配置逻辑，`marivo-mcp init` 是统一入口。其职责为：

- 检测 MCP server 宿主环境是否可执行 `marivo-mcp`
- 检测是否位于工作区根目录，或要求用户显式提供工作区根目录
- 推断远程显式连接或本地自动托管
- 生成最小 MCP server 配置
- 输出可直接粘贴的 JSON 片段，或直接写入支持的 agent 配置文件
- 在本地模式下补齐 `MARIVO_WORKSPACE_ROOT`
- 在远程模式下校验 `MARIVO_BASE_URL` 是否存在
- 向用户明确说明初始化结果，例如“已注册为本地自动托管”或“已注册为远程显式连接”

命令形态：

```bash
marivo-mcp init
marivo-mcp init --base-url http://team-marivo:8000
marivo-mcp init --base-url http://team-marivo:8000 --api-token $MARIVO_API_TOKEN
```

对于支持已知客户端配置写入的场景，还应支持：

```bash
marivo-mcp init --client codex --write
```

`generic` 客户端只定义可复制的 JSON 输出，不做自动写入；`codex` 客户端写入
Codex TOML 配置，默认只更新当前工作目录下 `.codex/config.toml` 的
`[mcp_servers.marivo]` block，保留其他配置。

对于不支持自动写入的场景，应至少支持：

```bash
marivo-mcp init --print-config
marivo-mcp init --client generic --print-config
marivo-mcp init --client codex --print-config
```

以输出最终 MCP 配置片段。

## Marivo 服务部署命令

为了支撑上述用户路径，Marivo 本身提供清晰的本地与远程部署命令。

### 1. 本地托管命令

`marivo-mcp` 在本地模式下应调用：

```bash
marivo serve-local
```

当需要显式初始化工作区本地配置时，应支持：

```bash
marivo init-local
```

用于诊断与运行时管理的命令应包括：

```bash
marivo doctor
marivo runtime status
marivo runtime stop
```

### 2. 远程服务部署命令

团队或平台侧可以用一个明确命令启动远程 HTTP Marivo：

```bash
marivo serve --host 0.0.0.0 --port 8000 --config /etc/marivo/marivo.yaml
```

如果采用环境变量方式部署，也应支持等价形式：

```bash
MARIVO_CONFIG=/etc/marivo/marivo.yaml \
marivo serve --host 0.0.0.0 --port 8000
```

远程部署的成功标准是：

- `GET /health` 正常返回
- `marivo-mcp` 可以通过 `MARIVO_BASE_URL` 稳定连接
- 远程行为与本地行为共享同一套 HTTP 契约

## MCP 监督器职责

MCP 侧应负责：

- 目标解析
- 工作区 `.marivo/` 初始化
- 运行时清单读写
- 本地 daemon 启动
- 健康检查
- 过期进程检测
- 针对本地与远程解析的清晰错误报告

MCP 侧不应负责：

- 直接写入 metadata SQLite
- 在 Marivo 服务 API 之外迁移语义对象
- 在规范 HTTP 路由之外执行 intent
- 与远程行为发生分歧的本地专有行为

## 错误策略

策略：

- 已配置远程目标但不可达
  - 明确失败
  - 不回退到本地
- 本地运行时清单存在但目标不健康
  - 尝试一次受控重启
  - 若重启后仍未恢复，则明确失败
- 本地运行时清单缺失或 pid 已死亡
  - 通过 `marivo serve-local` bootstrap
- 本地配置无效
  - 在报错中包含精确配置路径
- 无法确定工作区根目录
  - 明确失败
  - 不猜测默认目录继续执行

该策略可以保持本地与远程行为可预测，并防止环境之间发生静默数据漂移。

## 日志建议

日志中需要能看到解析后的目标。示例如下：

```text
Marivo target resolved: remote http://team-marivo:8000
Marivo target resolved: local auto-start at http://127.0.0.1:48231
Marivo local runtime reused from /path/.marivo/runtime.json
Marivo local startup failed: health check timeout after 15000ms
Remote Marivo configured but unreachable: http://team-marivo:8000
Marivo workspace root is required for local mode but was not resolved
```

## 最小运维命令

这些命令用于 MCP 运行时管理与诊断，不是第二套分析接口：

- `marivo serve-local`
- `marivo doctor`
- `marivo runtime status`
- `marivo runtime stop`
- `marivo init-local`

它们应保持为同一个 HTTP-first 系统之上的轻量运行时管理辅助命令。

用户与 operator 的最小排障路径见 [`agent-runtime-target-resolution-troubleshooting.zh.md`](./agent-runtime-target-resolution-troubleshooting.zh.md)。

## 发布检查入口

运行时目标解析发布前应检查三条主链路：

- local auto-managed `stdio` MCP：`MARIVO_MODE=local` + `MARIVO_WORKSPACE_ROOT`
- remote explicit `stdio` MCP：`MARIVO_MODE=remote` + `MARIVO_BASE_URL`
- remote explicit Streamable HTTP MCP：`marivo-mcp-http` + client URL

详细命令与 release smoke 项见 [`marivo-mcp/docs/release-checklist.md`](../../marivo-mcp/docs/release-checklist.md)。

## 总结

期望的稳态如下：

- agent 始终连接到 `marivo-mcp`
- `marivo-mcp` 始终连接到 HTTP Marivo
- 远程配置是显式且权威的
- 未配置远程目标时，本地自动托管启用
- 本地服务管理对用户隐藏，但不会在日志或错误报告中被隐藏
- 用户侧的安装与注册应收敛为一个最小路径，优先通过 `marivo-mcp init` 完成
- 默认用户心智模型只保留“本地自动托管”和“远程显式连接”
- 用户完成的是运行时接通，不是数据面 ready

这样可以在个人使用和远程使用之间保留单一执行模型，而不会把 Marivo 拆分为彼此独立的本地产品和远程产品。
