# Agent Runtime Target Resolution 实施 Todo Task List

## 概述

本文将 [`docs/service/agent-runtime-target-resolution.md`](/Users/lichengxiang/source/oss/marivo/docs/service/agent-runtime-target-resolution.md) 拆解为一份可直接落地开发的实施清单，目标是在 **保持 Marivo HTTP-only、用户侧只暴露“本地自动托管 / 远程显式连接”两种接入结果、禁止任何静默本地回退** 的前提下，把当前“`marivo-mcp` 必须显式配置 `MARIVO_BASE_URL` 才能连接远程 HTTP 服务”的基线，演进到目标态统一接入模型：

- `marivo core` 提供稳定的本地运行时管理入口
- `marivo-mcp` 提供 `auto|remote|local` 目标解析
- 本地默认场景下，agent 注册 `marivo-mcp` 后无需手工启动 Marivo
- 远程场景下，用户只需要提供 `base_url`，必要时再补 `api_token`

一句话结论：

- v1 先做“**scope/失败面冻结 + `marivo core` 本地运行时管理命令 + `marivo-mcp` 目标解析与 init + 工作区作用域 runtime manifest + tests/docs/运维收尾**”。
- 不做第二套非 HTTP 执行协议，不做 MCP 直写 metadata SQLite，不做无工作区前提下的 HTTP MCP 本地自动托管，不做静默 fallback。
- 任务拆解围绕七个交付面推进：scope/contract、`marivo core` runtime contract、`marivo core` 实现、`marivo-mcp` 解析与 init、transport/用户接入、测试矩阵、文档与运维收尾。

## 文档依据

- [`docs/service/agent-runtime-target-resolution.md`](/Users/lichengxiang/source/oss/marivo/docs/service/agent-runtime-target-resolution.md)
- [`docs/service/source-execution-mapping-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/source-execution-mapping-contract.md)
- [`docs/service/execution-auth-contract.md`](/Users/lichengxiang/source/oss/marivo/docs/service/execution-auth-contract.md)
- [`docs/service/marivo-skill.md`](/Users/lichengxiang/source/oss/marivo/docs/service/marivo-skill.md)
- [`marivo-mcp/README.md`](/Users/lichengxiang/source/oss/marivo/marivo-mcp/README.md)
- [`docs/agent-guide.md`](/Users/lichengxiang/source/oss/marivo/docs/agent-guide.md)

## 当前实现对照

当前仓库基线的关键事实：

- `marivo-mcp` 已是独立子项目，支持 `stdio` 与 Streamable HTTP transport，但 [`marivo-mcp/src/marivo_mcp/config.py`](/Users/lichengxiang/source/oss/marivo/marivo-mcp/src/marivo_mcp/config.py) 目前仍要求 `MARIVO_BASE_URL` 必填。
- [`tests/test_marivo_mcp_config.py`](/Users/lichengxiang/source/oss/marivo/tests/test_marivo_mcp_config.py) 当前也以“缺少 `MARIVO_BASE_URL` 必须启动失败”为基线。
- [`marivo-mcp/README.md`](/Users/lichengxiang/source/oss/marivo/marivo-mcp/README.md) 当前只文档化了远程 HTTP base URL 驱动的接入方式，尚未覆盖 `auto/local`、`init`、workspace root、runtime manifest。
- 根项目 [`pyproject.toml`](/Users/lichengxiang/source/oss/marivo/pyproject.toml) 当前没有 `marivo serve-local`、`marivo init-local`、`marivo runtime status`、`marivo runtime stop` 之类 CLI entrypoint。
- [`app/config.py`](/Users/lichengxiang/source/oss/marivo/app/config.py) 当前只支持 `MARIVO_CONFIG -> marivo.yaml` 的运行时配置解析，不承载工作区作用域 `.marivo/runtime.json` 或受管本地 daemon 语义。
- [`app/api/health.py`](/Users/lichengxiang/source/oss/marivo/app/api/health.py) 已有 `/health`，可以作为本地 runtime supervision 的基础健康检查面。

因此，下一阶段重点不是补更多远程-only MCP 工具，而是把 `marivo core + marivo-mcp` 组合成一条真正可落地的目标解析主链路，并补齐用户接入、工作区状态、失败面和排障能力。

## 实施范围

### 本次必须覆盖

- 冻结“本地自动托管 / 远程显式连接”的用户心智模型与失败面。
- 为 `marivo core` 增加本地运行时管理命令：`serve-local/init-local/doctor/runtime status/runtime stop`。
- 为 `marivo-mcp` 增加 `auto|remote|local` 目标解析、workspace root 解析、runtime manifest 复用与受控重启。
- 增加 `marivo-mcp init` 统一初始化入口与最小客户端配置输出。
- 让 stdio MCP 支持本地自动托管；让 HTTP MCP 至少稳定支持远程显式连接，并对本地自动托管设置清晰边界。
- 补齐测试矩阵、README/服务文档/运维说明。

### 本次明确不做

- 新的非 HTTP 执行边界
- MCP 或 agent 直接写入 metadata SQLite
- 脱离工作区上下文的“全局本地 daemon”默认模式
- 静默从远程失败回退到本地
- 为未来未支持 transport / client 预留一套抽象壳层
- 把 `marivo-mcp` 变成第二个 planner、runtime 或 operator 控制面

## 交付原则

- 粒度以“单个 owner 可独立完成并验收”为准，避免大而化之的“支持本地模式”。
- 边界以 `marivo core`、`marivo-mcp`、客户端注册、workspace 状态、文档/运维五层职责分开，不让 CLI、HTTP、MCP、runtime manifest 相互渗透。
- 每个任务都必须有明确交付物和验收标准，避免“代码写了但无法判断是否形成闭环”。
- 先冻结用户模型、mode 语义、workspace 解析与失败面，再补 core runtime 命令，再打通 `marivo-mcp`，最后收测试与文档。
- HTTP MCP 默认按远程显式连接建模；若没有稳定 workspace root，不得假装支持本地自动托管。

## 建议实施顺序

1. T1 冻结 scope、模式语义、workspace 解析与失败面
2. T2 冻结 `marivo core` 本地 runtime 管理 contract
3. T3 落 `marivo core` CLI / runtime manifest / local bootstrap
4. T4 落 `marivo-mcp` 目标解析、config 与 init
5. T5 收 transport、客户端注册与 HTTP MCP 边界
6. T6 补测试矩阵与 smoke path
7. T7 更新文档、README、release checklist 与排障说明

说明：

- T2/T3 是 T4 的前置，因为 `marivo-mcp` 本地自动托管必须依赖稳定的 `marivo serve-local` 行为，而不是内嵌启动细节。
- T4 与 T5 可部分并行，但都必须遵守同一份 workspace 解析优先级与不回退规则。
- T6 不只是补单测，还要覆盖 stale manifest、无 workspace、远程不可达、HTTP MCP 场景下的 guard。

## Todo Task List

## 一、Scope / Mode / Failure Contract 冻结

- [ ] 任务 1.1：冻结默认用户心智模型
  - 交付物：scope note / decision record
  - 关键内容：默认用户只看到“本地自动托管 / 远程显式连接”两种结果；`local` 仅作为运行时内部或高级调试模式存在
  - 验收标准：后续命令、文档、错误文案都不再把 `auto/local/remote` 三个内部 mode 直接暴露成等价用户模式

- [ ] 任务 1.2：冻结 mode 语义与配置优先级
  - 交付物：config contract note
  - 最小内容：`MARIVO_MODE=auto|remote|local`、`MARIVO_BASE_URL`、`MARIVO_WORKSPACE_ROOT`、`MARIVO_API_TOKEN`
  - 验收标准：`auto/remote/local` 的解析行为、忽略规则和冲突处理可直接编码实现，不依赖调用点二次猜测

- [ ] 任务 1.3：冻结 workspace root 解析优先级
  - 交付物：resolution note
  - 顺序：显式 `MARIVO_WORKSPACE_ROOT` > agent/client 传入 workspace 元数据 > MCP 进程启动 cwd > 否则失败
  - 验收标准：仓库内所有实现和文档对“拿不到 workspace 时必须失败”这一点无歧义

- [ ] 任务 1.4：冻结 v1 失败面 taxonomy
  - 交付物：error taxonomy
  - 范围：`remote_target_unreachable`、`workspace_root_required`、`runtime_manifest_invalid`、`local_runtime_start_failed`、`mcp_init_client_unsupported`
  - 验收标准：实现阶段可以返回结构化错误，不依赖自由文本拼凑

- [ ] 任务 1.5：冻结 HTTP MCP 适用边界
  - 交付物：boundary note
  - 关键内容：HTTP MCP 默认支持远程显式连接；仅在服务端可稳定解析唯一 workspace 且具备本地文件系统访问能力时，才允许本地自动托管
  - 验收标准：不会把 HTTP MCP 写成“默认也能自动本地托管”的模糊承诺

## 二、`marivo core` 本地 Runtime 管理 Contract

- [ ] 任务 2.1：冻结 `marivo core` CLI 命令面
  - 交付物：CLI contract
  - 最小命令：`marivo serve`、`marivo serve-local`、`marivo init-local`、`marivo doctor`、`marivo runtime status`、`marivo runtime stop`
  - 验收标准：`marivo-mcp` 有稳定可调用入口，不需要内嵌应用内部启动细节

- [ ] 任务 2.2：冻结工作区 `.marivo/` 布局
  - 交付物：workspace layout note
  - 最小内容：`.marivo/marivo.yaml`、`.marivo/metadata.sqlite`、`.marivo/runtime.json`、`logs/`、`run/`
  - 验收标准：用户、operator、`marivo-mcp`、测试夹具对本地状态文件位置无歧义

- [ ] 任务 2.3：冻结 `runtime.json` schema
  - 交付物：runtime manifest schema
  - 最小字段：`version`、`workspace_root`、`mode`、`base_url`、`host`、`port`、`pid`、`started_at`、`config_path`、`metadata_path`
  - 验收标准：`marivo-mcp` 可仅靠该 manifest 完成复用、校验和重启判断

- [ ] 任务 2.4：冻结本地启动与复用 contract
  - 交付物：runtime lifecycle note
  - 关键内容：先读 manifest，再校验 pid 和 `/health`；无效时允许一次受控重启；成功后覆盖写入 manifest
  - 验收标准：启动、复用、重启、失败的判定条件足够明确，可直接落到实现和测试

- [ ] 任务 2.5：冻结最小本地配置 bootstrap 内容
  - 交付物：bootstrap config note
  - 最小内容：`metadata.engine=sqlite`、`metadata.path=.marivo/metadata.sqlite`、最小 governance / observability 默认值
  - 验收标准：首次本地启动无需用户手工编写 `marivo.yaml`，且不把 source/engine/mapping inventory 塞回 runtime config

## 三、`marivo core` CLI / Runtime 实现

- [ ] 任务 3.1：为根项目补 CLI entrypoints 与命令模块
  - 交付物：`pyproject.toml` entrypoints + CLI 模块
  - 范围：提供 `marivo` 命令及子命令骨架；支持显式 `--config` / `--host` / `--port`
  - 验收标准：本地和远程启动都不再依赖手工写 `uvicorn ...` 的隐式操作

- [ ] 任务 3.2：实现 `marivo init-local`
  - 交付物：workspace bootstrap 逻辑
  - 范围：创建 `.marivo/`、写入最小 `marivo.yaml`、初始化 metadata SQLite 所需目录
  - 验收标准：在空工作区执行后，可直接为后续 `serve-local` 提供稳定输入

- [ ] 任务 3.3：实现 `marivo serve-local`
  - 交付物：local runtime 启动逻辑
  - 范围：解析 workspace root、确保本地配置存在、选择 host/port、启动规范 HTTP 服务、写入/刷新 `runtime.json`
  - 验收标准：`marivo-mcp` 可以把它当黑盒命令调用，而无需依赖 app 内部私有启动细节

- [ ] 任务 3.4：实现 `marivo runtime status`
  - 交付物：runtime inspect 命令
  - 范围：读取 `.marivo/runtime.json`、校验 pid 和 `/health`、输出当前 endpoint / config / metadata 路径
  - 验收标准：operator 不读代码也能知道本地 runtime 是否存活、实际用了哪个地址

- [ ] 任务 3.5：实现 `marivo runtime stop`
  - 交付物：runtime shutdown 命令
  - 范围：按 pid/manifest 受控停止本地 runtime，清理或刷新运行状态
  - 验收标准：本地 runtime 可被显式关闭，不需要用户手工找 pid 杀进程

- [ ] 任务 3.6：实现 `marivo doctor`
  - 交付物：诊断命令
  - 范围：检查 config path、metadata path、runtime manifest、`/health`、常见权限和路径错误
  - 验收标准：本地自动托管故障能通过一条命令暴露关键信息，而不是只返回模糊启动失败

## 四、`marivo-mcp` 目标解析 / Config / Init 主链路

- [ ] 任务 4.1：扩展 `marivo-mcp` 配置模型
  - 交付物：`marivo_mcp.config` 改造
  - 最小字段：`mode`、`base_url`、`api_token`、`workspace_root`、`start_timeout_ms`、`healthcheck_timeout_ms`
  - 验收标准：`marivo-mcp` 不再以“必须提供 `MARIVO_BASE_URL`”作为唯一启动路径

- [ ] 任务 4.2：实现 target resolver
  - 交付物：resolver 模块
  - 顺序：读 mode -> 解析 remote/local -> 决定 endpoint -> 返回结构化 resolution result
  - 验收标准：远程显式配置时绝不本地回退；本地模式时无 workspace 必须失败

- [ ] 任务 4.3：实现工作区 runtime manifest 发现与复用
  - 交付物：runtime supervisor helper
  - 范围：读取 `.marivo/runtime.json`、校验 pid 和 `/health`、决定复用/重启/失败
  - 验收标准：本地自动托管优先复用健康 runtime，不会每次启动都重复拉起新服务

- [ ] 任务 4.4：实现受控本地启动
  - 交付物：`serve-local` 调用封装
  - 范围：在 manifest 缺失或不健康时调用 `marivo serve-local`，轮询 `/health` 到超时或成功
  - 验收标准：`marivo-mcp` 只监督启动流程，不内嵌第二套应用启动栈

- [ ] 任务 4.5：实现 `marivo-mcp init`
  - 交付物：init 命令
  - 最小能力：推断本地自动托管/远程显式连接、生成最小 MCP 配置片段、可选 `--print-config` / `--write`
  - 验收标准：用户不再需要手工拼 `MARIVO_MODE/MARIVO_BASE_URL/MARIVO_WORKSPACE_ROOT` 环境变量

- [ ] 任务 4.6：补结构化日志与错误输出
  - 交付物：diagnostics / logging
  - 最小日志：resolved target、workspace root、manifest reuse、local startup timeout、remote unreachable
  - 验收标准：用户和 operator 能明确知道当前使用的是本地还是远程，以及失败发生在哪一层

## 五、Transport / 客户端注册 / 用户接入收敛

- [ ] 任务 5.1：收敛 stdio MCP 的目标态接入路径
  - 交付物：stdio registration contract
  - 范围：输出或写入本地子进程 MCP 所需最小配置；默认 server name 为 `marivo`
  - 验收标准：本地 agent 注册 `marivo-mcp` 后可直接走本地自动托管，不必额外手写环境变量

- [ ] 任务 5.2：补客户端配置写入策略
  - 交付物：client writer / config emitter
  - 范围：至少支持 `generic` 与 `codex` 的配置输出；不支持自动写入时回退到 `--print-config`
  - 验收标准：用户始终能得到一份可直接复制或写入的配置片段，而不是停留在命令说明层

- [ ] 任务 5.3：收敛 Streamable HTTP MCP 远程接入路径
  - 交付物：HTTP transport contract
  - 范围：在 HTTP 模式下稳定支持远程显式连接；统一 host/port/path 相关 env
  - 验收标准：HTTP MCP 文档、配置解析和运行行为一致，不再暗示默认本地自动托管

- [ ] 任务 5.4：为 HTTP MCP 增加 workspace guard
  - 交付物：transport guard / validator
  - 场景：若配置为 `auto/local` 但服务端拿不到稳定 workspace root，则启动失败并给出明确错误
  - 验收标准：不会在 HTTP MCP 模式下偷偷选一个 cwd 或用户目录继续跑本地 runtime

- [ ] 任务 5.5：更新 smoke / release checklist 的安装与启动路径
  - 交付物：smoke 脚本与发布检查项
  - 范围：覆盖 remote/stdio、remote/http、local auto-managed 三类最小场景
  - 验收标准：发布前能快速验证目标解析主链路，而不是只验证固定 `MARIVO_BASE_URL`

## 六、测试矩阵与回归覆盖

- [ ] 任务 6.1：补 `marivo core` CLI / bootstrap 测试
  - 交付物：CLI tests / config bootstrap tests
  - 场景：`init-local` 首次初始化、重复初始化幂等、相对路径 config 正确落地
  - 验收标准：本地 workspace bootstrap 行为稳定，不依赖人工检查文件结果

- [ ] 任务 6.2：补 local runtime manifest 测试
  - 交付物：unit / integration-ish tests
  - 场景：健康 manifest 复用、stale pid 重启、manifest 缺字段失败、`runtime stop` 后状态刷新
  - 验收标准：`runtime.json` 生命周期可稳定重放，不出现静默脏状态

- [ ] 任务 6.3：补 `marivo-mcp` config / resolver 测试
  - 交付物：config tests / resolver tests
  - 场景：`auto` 命中远程、`auto` 命中本地、`remote` 缺 `base_url` 失败、`local` 缺 workspace 失败
  - 验收标准：mode 解析与失败面完全受测试约束

- [ ] 任务 6.4：补 init / client config 生成测试
  - 交付物：CLI tests
  - 场景：`--print-config` 输出、`--client codex --write`、`--base-url` 远程注册、默认本地注册
  - 验收标准：生成出的配置片段与目标文档一致，可直接驱动客户端接入

- [ ] 任务 6.5：补 transport guard 测试
  - 交付物：HTTP transport tests
  - 场景：HTTP MCP 远程显式连接成功、HTTP MCP 本地模式无 workspace 失败、远程不可达不回退
  - 验收标准：HTTP transport 的边界被测试锁住，不会在后续迭代中回退成模糊行为

- [ ] 任务 6.6：补端到端 smoke path
  - 交付物：smoke tests / scripted checks
  - 场景：本地工作区首次 `init + start + connect`、复用已有 runtime、远程 base_url 连接、错误文案回归
  - 验收标准：三条主路径都能被自动化验证，不只靠 README 手工演练

## 七、文档 / README / 运维收尾

- [ ] 任务 7.1：更新 `docs/service/agent-runtime-target-resolution.md`
  - 交付物：文档 PR
  - 范围：将目标态命令、实际 env 名称、workspace guard、HTTP MCP 限制与实现保持一致
  - 验收标准：文档不再与真实命令面或失败面漂移

- [ ] 任务 7.2：更新 `marivo-mcp/README.md`
  - 交付物：README PR
  - 范围：安装位置、`init` 用法、local auto-managed / remote explicit / HTTP transport 三条路径、环境变量说明
  - 验收标准：README 不再把 `MARIVO_BASE_URL required` 描述成唯一目标态路径

- [ ] 任务 7.3：更新 `marivo-mcp/docs/release-checklist.md`
  - 交付物：release checklist
  - 范围：补 local auto-managed smoke、workspace root 校验、remote unreachable fail-closed 检查项
  - 验收标准：发布检查表能覆盖目标解析主链路的关键回归点

- [ ] 任务 7.4：更新共享指南与 skill 边界文档
  - 交付物：[`docs/agent-guide.md`](/Users/lichengxiang/source/oss/marivo/docs/agent-guide.md) 与 [`docs/service/marivo-skill.md`](/Users/lichengxiang/source/oss/marivo/docs/service/marivo-skill.md)
  - 范围：强调 `marivo-mcp` 负责连接与运行时监督，skill 只负责调用守则；agent guide 仅保留 repo-wide 边界
  - 验收标准：后续 agent 不会把 skill、MCP、core runtime management 三者职责混淆

- [ ] 任务 7.5：补 operator / 用户排障说明
  - 交付物：operator note / troubleshooting guide
  - 最小内容：本地模式如何看 `runtime.json`、如何执行 `marivo doctor`、远程不可达会报什么错、HTTP MCP 为什么默认不做本地自动托管
  - 验收标准：用户无需读源码即可判断自己当前处于本地还是远程模式，并完成最小排障

## 验收标准

- `marivo core` 已提供 `serve-local/init-local/doctor/runtime status/runtime stop` 等最小运行时管理命令。
- `marivo-mcp` 已支持 `auto|remote|local` 目标解析，不再以 `MARIVO_BASE_URL required` 作为唯一启动模型。
- stdio MCP 在有 workspace root 的情况下可自动托管本地 Marivo；远程模式失败时绝不回退本地。
- HTTP MCP 至少稳定支持远程显式连接；在拿不到 workspace root 时会明确拒绝本地自动托管。
- `.marivo/runtime.json` 成为稳定可复用的本地 runtime 发现契约，复用/重启/失败逻辑有自动化测试覆盖。
- `marivo-mcp init` 能生成或写出最小客户端配置，不再要求用户手工拼装环境变量。
- README、服务文档、release checklist、排障说明与实际实现保持一致。

## 建议 PR 切分

1. PR1：冻结 contract，并补 `marivo core` CLI 命令面与 workspace runtime manifest schema
2. PR2：实现 `marivo init-local/serve-local/runtime status/runtime stop/doctor`
3. PR3：实现 `marivo-mcp` config / resolver / local runtime supervision
4. PR4：实现 `marivo-mcp init`、client config 输出与 HTTP transport guard
5. PR5：补测试矩阵、README、release checklist 与 operator 文档
