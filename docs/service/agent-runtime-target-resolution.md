# Agent 运行时目标解析

本文档定义仅面向 agent 使用场景下的 Factum 目标解析与本地运行时设计。它是一份服务/运行时设计说明，不是对外 HTTP API 契约。

Factum 仍然保持 HTTP-only。Agent 通过 `factum-mcp` 与 Factum 交互，而 `factum-mcp` 再通过规范 HTTP API 与 Factum 交互。本地与远程使用方式的差异，只体现在目标 Factum 服务的解析方式上。

## 目标

- 为本地和远程 Factum 使用场景保留一条统一的 agent 集成路径
- 保持 HTTP 作为唯一的规范执行边界
- 在默认场景下，无需用户手动启动本地 Factum 服务
- 让远程配置显式且可预测
- 避免任何可能掩盖 agent 实际在使用本地还是远程状态的静默回退

## 非目标

- 定义第二套 CLI 原生分析契约
- 允许 MCP 或 agent 技能直接写入 metadata SQLite
- 暴露一个绕过 HTTP 路由的独立本地执行引擎
- 针对人工驱动的分析工作流做优化

## 发布目标

本文档描述的是目标发布形态，而不是当前实现状态。发布目标应覆盖三个层次：

### 1. Factum core 发布目标

- 提供一个始终以 HTTP 为执行边界的 Factum 服务
- 提供显式的本地运行时管理命令，而不是要求用户手工拼接启动流程
- 提供远程部署所需的稳定服务入口与健康检查

目标命令面应至少包括：

- `factum serve`
- `factum serve-local`
- `factum init-local`
- `factum doctor`
- `factum runtime status`
- `factum runtime stop`

### 2. `factum-mcp` 发布目标

- 提供一个可独立安装的 MCP 适配器包
- 提供 `stdio` 与 HTTP 两种 MCP 传输形态
- 提供统一的目标解析逻辑：`auto|remote|local`
- 提供一个面向最终用户的初始化命令，用来最小化安装、注册、配置步骤

目标命令面应至少包括：

- `factum-mcp`
- `factum-mcp-http`
- `factum-mcp init`
- `factum-mcp doctor`

### 3. 最终用户接入目标

- 本地默认场景下，用户安装并注册 `factum-mcp` 后即可开始使用，不需要手工启动本地 Factum
- 远程场景下，用户只需要额外提供远程 `base_url`，必要时再提供 `api_token`
- agent 始终只注册一个 MCP server，例如 `factum`
- 用户不需要理解 `runtime.json`、本地端口选择、daemon 生命周期等内部机制

## 面向用户的最小心智模型

对最终用户，推荐只暴露两种模式：

- 本地模式：不填写远程地址，系统自动管理工作区本地 Factum
- 远程模式：填写远程 `base_url`，系统显式连接远程 Factum

用户只应理解以下事实：

- agent 只连接 `factum-mcp`
- `factum-mcp` 再去连接 HTTP Factum
- 不填远程地址时默认走本地自动托管
- 填了远程地址时必须走远程，失败时明确报错，不回退本地

用户不应被迫显式配置以下内部细节，除非进入高级模式：

- `FACTUM_LOCAL_HOST`
- `FACTUM_LOCAL_PORT`
- `FACTUM_START_TIMEOUT_MS`
- `FACTUM_HEALTHCHECK_TIMEOUT_MS`
- `runtime.json` 路径与复用逻辑
- 本地 daemon 的启动与回收细节

## 规范交互模型

规范的 agent 交互流程如下：

```text
Agent -> factum-mcp -> target resolution -> HTTP Factum
```

目标解析会在以下两者中选择其一：

- 已配置的远程 Factum 服务
- 自动管理的本地 Factum daemon

从 agent 视角看，两种模式都使用同一套 MCP 工具，以及同样的 Factum HTTP 行为。

## 设计原则

### 1. 单一 agent 入口

Agent 始终连接到 `factum-mcp`。它们不需要在“本地 Factum 适配器”和“远程 Factum 适配器”之间做选择。

### 2. 单一协议边界

所有业务行为都必须保留在规范 HTTP 路由之后。MCP 是客户端侧适配器，不能演变成第二个执行面。

### 3. 目标解析与能力执行分离

本地还是远程 Factum 的选择，发生在任何工具调用发出之前。目标解析完成之后，typed intent、语义写入和规范读取都应保持一致。

### 4. 显式远程优先

如果调用方显式配置了远程 Factum 服务，`factum-mcp` 就必须使用该远程目标，或者明确失败。它不能静默回退到本地模式。

### 5. 零摩擦本地模式

如果未配置远程目标，`factum-mcp` 应自动创建或复用一个带有工作区作用域状态的本地 Factum 运行时。

## 配置模型

推荐的环境变量模型如下：

```text
FACTUM_MODE=auto|remote|local
FACTUM_BASE_URL=
FACTUM_CONFIG=
FACTUM_WORKSPACE_ROOT=
FACTUM_LOCAL_HOST=127.0.0.1
FACTUM_LOCAL_PORT=0
FACTUM_START_TIMEOUT_MS=15000
FACTUM_HEALTHCHECK_TIMEOUT_MS=2000
```

推荐语义：

- `FACTUM_MODE=auto`
  - 如果设置了 `FACTUM_BASE_URL`，则使用远程模式
  - 否则使用本地模式
- `FACTUM_MODE=remote`
  - 必须提供 `FACTUM_BASE_URL`
  - 如果远程服务不可用则失败
- `FACTUM_MODE=local`
  - 忽略 `FACTUM_BASE_URL`
  - 强制执行本地运行时发现或启动

因此，`FACTUM_BASE_URL` 是一个目标提示，而不是第二种传输模式。

## 面向用户的最小配置模型

虽然运行时支持更完整的环境变量集合，但普通用户侧应收敛为最少配置项：

- `mode`
- `base_url`
- `api_token`
- `workspace_root`

推荐映射关系：

- 若提供 `base_url`，默认推断为远程模式
- 若未提供 `base_url`，默认推断为本地自动模式
- `workspace_root` 默认取 agent 当前工作区
- `api_token` 仅在远程服务要求鉴权时出现

推荐的用户可见配置形态可以抽象为：

```yaml
factum:
  mode: auto
  base_url: null
  api_token: null
  workspace_root: /abs/path/to/workspace
```

底层再由 `factum-mcp` 将其展开为实际环境变量。

## 目标解析规则

目标解析算法应如下：

```text
1. 读取 FACTUM_MODE，默认值为 auto。
2. 如果 mode=remote:
   - 要求存在 FACTUM_BASE_URL
   - 连接到该远程目标
   - 若不可达则失败
3. 如果 mode=local:
   - 解析或启动本地运行时
4. 如果 mode=auto:
   - 若存在 FACTUM_BASE_URL，则按远程处理
   - 否则按本地处理
```

### 必须满足的不变量

- 已配置的远程目标绝不能静默回退到本地
- 本地解析绝不能静默切换到某个任意不同的 endpoint
- MCP 启动时应清晰记录解析后的目标

## 工作区作用域的本地运行时

本地模式默认应使用工作区作用域的状态，而不是单一的用户级 daemon。这样可以让 metadata、session 和日志与当前项目保持一致，避免不同项目之间的状态被意外混用。

推荐的工作区布局如下：

```text
<workspace>/.factum/
  factum.yaml
  metadata.sqlite
  runtime.json
  logs/
    factum.log
  run/
    factum.pid
```

推荐含义：

- `factum.yaml`：本地运行时配置
- `metadata.sqlite`：本地 metadata 存储
- `runtime.json`：`factum-mcp` 用于运行时发现的锚点文件
- `logs/`：本地运行时诊断日志
- `run/`：可选的 pid 和锁文件目录

## 运行时清单

`runtime.json` 是 `factum-mcp` 与受管本地 Factum daemon 之间的本地发现契约。

推荐结构：

```json
{
  "version": "0.2.0",
  "workspace_root": "/abs/path/to/workspace",
  "mode": "local",
  "base_url": "http://127.0.0.1:48231",
  "host": "127.0.0.1",
  "port": 48231,
  "pid": 12345,
  "started_at": "2026-04-21T10:20:30Z",
  "config_path": "/abs/path/to/workspace/.factum/factum.yaml",
  "metadata_path": "/abs/path/to/workspace/.factum/metadata.sqlite"
}
```

推荐要求：

- `base_url` 必须是 MCP 在本地解析完成后实际使用的精确 endpoint
- `pid` 仅作提示用途，复用前必须重新校验
- `version` 应与生成该清单的本地 Factum 运行时版本一致
- 对于过期或无效的清单，应在成功重启后覆盖写入

## 本地运行时启动流程

当选择本地模式时，`factum-mcp` 应：

```text
1. 解析工作区根目录。
2. 确保 .factum/ 存在。
3. 如果存在，则读取 .factum/runtime.json。
4. 如果 runtime.json 存在：
   - 如果存在 pid，则校验该 pid
   - 对 base_url 调用 GET /health
   - 若健康则直接复用
5. 否则启动本地 Factum daemon。
6. 轮询 GET /health，直到成功或启动超时。
7. 写入或刷新 runtime.json。
8. 后续 MCP 工具调用复用该 base_url。
```

MCP 层应作为本地运行时发现与启动的轻量级监督器，而不是第二个分析引擎。

## 本地 daemon 启动契约

为了保持运行时管理稳定，Factum 应暴露一个显式的本地启动入口，例如：

```text
factum serve-local
```

推荐行为：

- 创建或校验工作区作用域的 `.factum/factum.yaml`
- 确保 metadata SQLite 存在于配置的本地路径
- 选择绑定的 host 和 port
- 启动规范 HTTP 服务
- 写入 `runtime.json`

`factum-mcp` 应调用这个入口，而不是内嵌 Factum 内部启动流程的深层细节。

## 默认本地配置

本地模式在常见路径上应做到零配置。首次启动时，Factum 应落地一份最小的工作区本地配置，例如：

```yaml
metadata:
  engine: sqlite
  path: .factum/metadata.sqlite

ui:
  enabled: false

governance:
  enabled: true
```

数据源与执行引擎注册仍然需要显式完成，但运行时启动路径不应要求用户在 agent 连接之前手工编写本地文件。

## 用户安装、注册与配置路径

以下内容描述目标发布形态下，用户应如何在自己的 agent 运行环境中安装并注册 `factum-mcp`。这些步骤是产品化目标，不代表当前仓库已全部实现。

### 1. 本地默认路径

目标是让个人用户在本地工作区中“注册即用”。

规划中的最短路径应为：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install factum factum-mcp
factum-mcp init --mode local
```

`factum-mcp init --mode local` 的目标行为应为：

- 检测当前工作区
- 生成或更新最小 MCP 注册配置
- 默认把 MCP server 名称注册为 `factum`
- 生成本地模式所需的最小环境变量
- 不要求用户手工创建 `.factum/`
- 不要求用户手工启动本地 Factum

如果用户的 agent 支持直接读取 MCP 配置文件，初始化命令应能输出或写入类似配置：

```json
{
  "mcpServers": {
    "factum": {
      "command": "factum-mcp",
      "env": {
        "FACTUM_MODE": "auto",
        "FACTUM_WORKSPACE_ROOT": "/abs/path/to/workspace"
      }
    }
  }
}
```

用户完成注册并重启 agent 后，`factum-mcp` 应自动：

- 解析 `<workspace>/.factum/`
- 发现并复用已有本地运行时
- 在无可用运行时时调用 `factum serve-local`
- 完成健康检查并写入 `runtime.json`

### 2. 远程服务路径

目标是让团队用户在只提供远程地址的前提下完成接入。

规划中的最短路径应为：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install factum factum-mcp
factum-mcp init --mode remote --base-url http://team-factum:8000
```

若远程服务需要鉴权，则应支持：

```bash
factum-mcp init \
  --mode remote \
  --base-url http://team-factum:8000 \
  --api-token $FACTUM_API_TOKEN
```

目标生成配置应类似：

```json
{
  "mcpServers": {
    "factum": {
      "command": "factum-mcp",
      "env": {
        "FACTUM_MODE": "remote",
        "FACTUM_BASE_URL": "http://team-factum:8000",
        "FACTUM_API_TOKEN": "<token>"
      }
    }
  }
}
```

在该模式下：

- `factum-mcp` 必须连接该远程服务
- 若远程不可达，必须明确失败
- 绝不能静默回退到本地模式

### 3. 自动模式路径

自动模式应作为默认用户体验存在，让用户不需要显式理解 `local` 与 `remote`。

规划中的最短路径应为：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install factum factum-mcp
factum-mcp init
```

推荐语义：

- 如果初始化时提供了 `--base-url`，则注册远程
- 如果未提供 `--base-url`，则注册本地自动模式
- 输出给用户的文案应直接解释最终结果，而不是只打印环境变量

### 4. HTTP 方式注册 MCP

对于不能直接拉起本地子进程 MCP 的 agent，用户应能够先部署 `factum-mcp` HTTP 端点，再在 agent 中注册 URL。

规划中的命令应为：

```bash
FACTUM_MODE=auto factum-mcp-http
```

远程模式示例：

```bash
FACTUM_MODE=remote \
FACTUM_BASE_URL=http://team-factum:8000 \
FACTUM_API_TOKEN=$FACTUM_API_TOKEN \
factum-mcp-http
```

对应的 agent 注册配置应类似：

```json
{
  "mcpServers": {
    "factum": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

这里的核心仍然不变：agent 只注册 `factum-mcp`，不直接注册 Factum 本身。

## Factum 服务部署命令

为了支撑上述用户路径，Factum 本身需要提供清晰的本地与远程部署命令。以下命令同样表示目标发布形态。

### 1. 本地托管命令

`factum-mcp` 在本地模式下应调用：

```bash
factum serve-local
```

当需要显式初始化工作区本地配置时，应支持：

```bash
factum init-local
```

用于诊断与运行时管理的命令应包括：

```bash
factum doctor
factum runtime status
factum runtime stop
```

### 2. 远程服务部署命令

团队或平台侧应能够用一个明确命令启动远程 HTTP Factum：

```bash
factum serve --host 0.0.0.0 --port 8000 --config /etc/factum/factum.yaml
```

如果采用环境变量方式部署，也应支持等价形式：

```bash
FACTUM_CONFIG=/etc/factum/factum.yaml \
factum serve --host 0.0.0.0 --port 8000
```

远程部署的成功标准是：

- `GET /health` 正常返回
- `factum-mcp` 可以通过 `FACTUM_BASE_URL` 稳定连接
- 远程行为与本地行为共享同一套 HTTP 契约

## 推荐的初始化命令职责

为了尽量简化用户安装、注册、配置逻辑，`factum-mcp init` 应成为统一入口。其目标职责应为：

- 检测是否位于工作区根目录
- 推断本地或远程模式
- 生成最小 MCP server 配置
- 输出可直接粘贴的 JSON 片段，或直接写入支持的 agent 配置文件
- 在本地模式下补齐 `FACTUM_WORKSPACE_ROOT`
- 在远程模式下校验 `FACTUM_BASE_URL` 是否存在
- 向用户明确说明初始化结果，例如“已注册为本地自动模式”或“已注册为远程模式”

推荐命令形态：

```bash
factum-mcp init
factum-mcp init --mode local
factum-mcp init --mode remote --base-url http://team-factum:8000
factum-mcp init --mode remote --base-url http://team-factum:8000 --api-token $FACTUM_API_TOKEN
```

对于支持已知客户端配置写入的场景，还应支持：

```bash
factum-mcp init --client generic --write
factum-mcp init --client codex --write
```

对于不支持自动写入的场景，应至少支持：

```bash
factum-mcp init --print-config
```

以输出最终 MCP 配置片段。

## MCP 监督器职责

MCP 侧应负责：

- 目标解析
- 工作区 `.factum/` 初始化
- 运行时清单读写
- 本地 daemon 启动
- 健康检查
- 过期进程检测
- 针对本地与远程解析的清晰错误报告

MCP 侧不应负责：

- 直接写入 metadata SQLite
- 在 Factum 服务 API 之外迁移语义对象
- 在规范 HTTP 路由之外执行 intent
- 与远程行为发生分歧的本地专有行为

## 错误策略

推荐策略：

- 已配置远程目标但不可达
  - 明确失败
  - 不回退到本地
- 本地运行时清单存在但目标不健康
  - 尝试一次受控重启
  - 若重启后仍未恢复，则明确失败
- 本地配置无效
  - 在报错中包含精确配置路径
- MCP 预期版本与本地运行时版本不匹配
  - 以显式兼容性消息失败，或执行一条受控迁移路径

该策略可以保持本地与远程行为可预测，并防止环境之间发生静默数据漂移。

## 日志建议

日志中应能看到解析后的目标。推荐示例如下：

```text
Factum target resolved: remote http://team-factum:8000
Factum target resolved: local auto-start at http://127.0.0.1:48231
Factum local runtime reused from /path/.factum/runtime.json
Factum local startup failed: health check timeout after 15000ms
Remote Factum configured but unreachable: http://team-factum:8000
```

## 推荐的最小运维命令

这些命令用于 MCP 运行时管理与诊断，不是第二套分析接口：

- `factum serve-local`
- `factum doctor`
- `factum runtime status`
- `factum runtime stop`
- `factum init-local`

它们应保持为同一个 HTTP-first 系统之上的轻量运行时管理辅助命令。

## 实现顺序

推荐的落地顺序：

1. 在 `factum-mcp` 中加入 `auto|remote|local` 目标解析支持
2. 加入对工作区本地 `.factum/runtime.json` 的发现与复用
3. 在 Factum core 中加入稳定的 `factum serve-local` 入口
4. 在 `factum-mcp` 中加入 `factum-mcp init` 统一初始化命令
5. 加入启动健康检查与超时处理
6. 加入自动初始化本地 `.factum/factum.yaml`
7. 在 `factum-mcp` 和共享 agent 指南中记录统一的 agent 运行时流程
8. 补齐面向用户的安装、注册、部署命令与示例配置

## 总结

期望的稳态如下：

- agent 始终连接到 `factum-mcp`
- `factum-mcp` 始终连接到 HTTP Factum
- 远程配置是显式且权威的
- 未配置远程目标时，本地模式自动启用
- 本地服务管理对用户隐藏，但不会在日志或错误报告中被隐藏
- 用户侧的安装与注册应收敛为一个最小路径，优先通过 `factum-mcp init` 完成
- 本地默认模式应做到“注册即用”，远程模式应做到“只填地址即可用”

这样可以在个人使用和远程使用之间保留单一执行模型，而不会把 Factum 拆分为彼此独立的本地产品和远程产品。
