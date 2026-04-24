# Agent Runtime Target Resolution v1 用户心智模型与范围冻结说明

本文是 agent runtime target resolution v1 的正式产品边界说明。它回答"用户看到几种接入结果、哪些 mode 对用户可见、哪些仅作为运行时内部概念存在"，作为后续命令、文档、错误文案和 UI 语言的权威参考。

配置语义与解析规则见 [`agent-runtime-target-resolution-config-contract.zh.md`](./agent-runtime-target-resolution-config-contract.zh.md)。

## v1 范围内

### 用户可见的两种接入结果

v1 只向最终用户暴露两种接入结果：

1. **本地自动托管**：用户不填写远程地址，系统自动管理工作区本地 Marivo daemon。用户不需要手动启动服务、选择端口或管理进程生命周期。
2. **远程显式连接**：用户填写远程 `base_url`，系统显式连接远程 Marivo。若远程不可达，明确报错，不回退本地。

用户只应理解以下事实：

- agent 只连接 `marivo-mcp`
- `marivo-mcp` 再通过 HTTP 连接 Marivo
- 不填远程地址时默认走本地自动托管
- 填了远程地址时必须走远程，失败时明确报错，不回退本地

### 内部运行时 mode

`auto|remote|local` 是运行时内部概念，不是用户可见的产品模式：

- `auto`：默认体验。提供 `base_url` → 远程，否则 → 本地。用户不应看到这个词。
- `remote`：等价于"用户填了远程地址"这一结果。仅在错误诊断和运维日志中出现。
- `local`：仅作为高级调试与运维入口，不作为与"本地自动托管"和"远程显式连接"并列的第三用户模式。

### `marivo-mcp init` 作为统一初始化入口

用户安装并注册 `marivo-mcp` 的最短路径应收敛为一个命令：

```bash
marivo-mcp init                     # 默认：不提供 base_url → 本地自动托管
marivo-mcp init --base-url http://… # 提供地址 → 远程显式连接
```

初始化命令的输出文案应直接说明最终结果（"已注册为本地自动模式"或"已注册为远程模式"），而不是让用户理解 `auto|remote|local` 的语义差异。

### 用户不应被迫配置的内部细节

以下内容属于运行时内部机制，用户不应在默认路径中被迫显式配置：

- `MARIVO_LOCAL_HOST`、`MARIVO_LOCAL_PORT`
- `MARIVO_START_TIMEOUT_MS`、`MARIVO_HEALTHCHECK_TIMEOUT_MS`
- `.marivo/runtime.json` 路径与复用逻辑
- 本地 daemon 的启动、健康检查与回收细节

这些参数仅在高级模式或运维诊断场景中出现。

## v1 范围外

### 不向用户暴露三选一

v1 不允许命令、文档、错误文案或 UI 将 `auto|remote|local` 作为三个等价用户模式呈现。用户的心智模型是"填地址 = 远程，不填 = 本地"，而不是理解三个 mode 的语义差异。

### 不允许 `local` 作为并列用户模式

`local` mode 的存在理由是给 operator 提供受控调试入口。它不是与"本地自动托管"和"远程显式连接"并列的第三用户选项。任何面向用户的文案不得将 `local` 描写为"你也可以选择 local 模式"之类的等价选项。

### 不支持静默回退

远程不可达时，系统必须明确失败，绝不静默回退到本地。不允许任何形式的降级路径：包括自动重试本地启动、在超时后切换到本地 daemon、或以 warning 替代 error 继续运行。

### 不支持脱离工作区的全局本地 daemon

本地自动托管必须以工作区为作用域。不存在"用户不提供 workspace root 也能自动启动本地 Marivo"的场景。没有工作区上下文时，本地模式必须明确失败。

### 不支持 HTTP MCP 默认本地自动托管

HTTP MCP 默认支持远程显式连接。本地自动托管仅在服务端可稳定解析唯一 workspace 且具备本地文件系统访问能力时才允许。不允许将 HTTP MCP 文档或行为写成"默认也能自动本地托管"的模糊承诺。

## v1 边界原理

一句话总结：

> 用户应回答"我有没有远程地址"，而不是回答"我要选哪个 mode"。

v1 约束集是故意最小化的。`auto|remote|local` 的存在是为了让运行时实现有清晰的分支逻辑，而不是为了让用户多一个决策点。如果后续需要暴露更细粒度的用户选项（例如"仅发现但不自动启动"、"使用已有本地实例但拒绝自动重启"），应通过正式的契约版本升级引入，而不是在 `MARIVO_MODE` 上追加新值。

核心不变量：

- 远程配置是显式且权威的：一旦用户提供 `base_url`，系统必须使用远程目标或明确失败
- 本地是零摩擦的默认路径：不填远程地址时，系统自动管理工作区本地运行时
- 两者之间不存在静默切换

## 与其他对象的职责边界

| 职责 | 归属 | 说明 |
|------|------|------|
| 目标解析 | `marivo-mcp` | 读取配置，解析 mode，决定使用远程还是本地目标 |
| 本地运行时管理 | `marivo core` CLI | `serve-local`、`init-local`、`doctor`、`runtime status`、`runtime stop` |
| 工作区状态发现 | `marivo-mcp` | 读取 `.marivo/runtime.json`，校验与复用本地 daemon |
| 本地 daemon 启动 | `marivo core` CLI | `marivo-mcp` 调用 `marivo serve-local`，不内嵌启动逻辑 |
| 错误文案与日志 | 各组件 | 遵循本 scope note 的用户模式语言，不向用户暴露内部 mode |
| 初始化与注册 | `marivo-mcp init` | 统一入口，输出面向用户的最终结果描述 |
| 调用守则 | `marivo skill` | 负责"应不应该这样调用"，不负责目标解析或运行时管理 |
