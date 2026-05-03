# Marivo Skill 设计说明

本文档定义 Marivo agent skill 的目标、边界、内容范围与发布形态。它是一份 agent 使用策略设计说明，不是 HTTP API 契约，也不是 MCP 工具清单。

Marivo 仍然保持 HTTP-only。`marivo-mcp` 是对规范 HTTP API 的客户端适配层；`marivo skill` 是 agent 侧的使用守则与决策辅助层。两者配合使用，但职责必须严格分离。

## 背景

仅有 `marivo-mcp` 时，agent 已经可以调用 Marivo 的能力，但仍然容易出现以下问题：

- 选错 Marivo surface，例如把 state surface 当作 action surface
- 在不合适的阶段使用不合适的 typed intent
- 把 MCP 返回包装、操作面信息或 agent 自己的总结误当成规范证据
- 忽略 lifecycle/readiness 差异，过早假设语义对象可用
- 在 session 已经足够回答问题后忘记显式终止 investigation 生命周期

这些问题不是协议映射问题，而是 agent 使用策略问题。因此需要单独的 `marivo skill` 来约束 agent 如何正确使用 Marivo。

## 目标

- 为 agent 提供 Marivo 的正确使用策略，而不是再次定义协议
- 帮助 agent 在 action、state、context、semantic、infrastructure surfaces 之间做正确路由
- 为常见分析任务提供稳定的默认操作 loop
- 明确高频错误与反模式，降低错误调用率
- 在不复制 HTTP/MCP 契约的前提下，提高 agent 使用 Marivo 的成功率和一致性

## 非目标

- 定义新的公共分析契约
- 替代 `marivo-mcp`
- 复制完整 HTTP 字段级 schema 或 MCP inventory
- 管理本地 runtime、目标解析或健康检查
- 让 agent 通过 skill 绕过 HTTP 或 MCP 边界
- 提供 ad hoc SQL 或 text-to-SQL 作为公开分析接口

## 为什么需要 Skill

`marivo-mcp` 解决的是“如何正确连接和调用 Marivo”；`marivo skill` 解决的是“什么时候该调用什么，以及哪些行为是错误的”。

如果没有 skill：

- agent 仍能调用工具，但更容易采取不稳定的调用路径
- 相同任务在不同 agent 或不同 prompt 下会产生明显分歧
- 规范 evidence-first 工作流更容易退化为“调用几个工具然后自由总结”

因此，推荐的产品定位应是：

- `marivo-mcp`：默认必装，提供执行能力
- `marivo skill`：默认推荐启用，提供使用策略

## 规范关系模型

Marivo agent 使用的目标关系应为：

```text
User request
  -> Agent
    -> marivo skill (decide how to use Marivo)
    -> marivo-mcp (execute MCP tools/resources)
    -> HTTP Marivo (canonical execution boundary)
```

其中：

- `marivo skill` 不直接产生证据
- `marivo-mcp` 不决定调查策略
- HTTP Marivo 仍然是唯一的规范执行边界

## Skill 与 MCP 的职责边界

### `marivo-mcp` 负责

- MCP transport 与 server 形态
- `auto|remote|local` 目标解析
- 环境变量与运行时配置加载
- 本地 runtime 发现、启动、健康检查与错误报告
- MCP tool 与 resource 暴露
- MCP 参数到 HTTP 请求的规范映射
- 认证、超时、日志与统一错误 envelope

### `marivo skill` 负责

- 判断当前任务是否应该使用 Marivo
- 在不同 Marivo surface 之间做任务路由
- 推荐默认的 typed intent 起手式和 follow-up 顺序
- 强化 session、state、context、semantic layer 的边界意识
- 约束 agent 不把非规范 surface 当作 canonical evidence
- 提示 lifecycle/readiness、session terminate、时间窗口等高频 guardrails

### Skill 明确不负责

- 发布或映射 MCP tool schema
- 提供完整 payload 字段表
- 发明 MCP-only 业务抽象
- 直接读写 metadata SQLite
- 管理 `runtime.json`、daemon 或端口绑定
- 替代 README、OpenAPI、inventory、HTTP 契约文档

一句话划分：

- `marivo-mcp` 负责“能不能正确调用”
- `marivo skill` 负责“应不应该这样调用”

## 设计原则

### 1. Skill 只做策略，不做协议

Skill 应告诉 agent 下一步用哪类 surface、以什么顺序推进，而不是重复定义 HTTP/MCP 的字段契约。

### 2. HTTP 契约始终是唯一真相

当 skill 与 HTTP/MCP 文档出现不一致时，应以 HTTP 契约和 `marivo-mcp` 可执行接口为准。Skill 只能做路由和守则，不能成为第二份协议真相。

### 3. 最小但高价值

Skill 内容应集中在真正影响 agent 决策质量的部分，例如 surface 选择、默认操作 loop、高频 guardrails。不要把它扩展成一份复制版产品文档。

### 4. 强边界，弱耦合

Skill 应引用而不是复制下层契约。这样可以在 HTTP/MCP 迭代时，减少三份文档同时漂移的风险。

### 5. 默认面向真实分析任务

Skill 应围绕“真实用户如何发起调查、推进调查、收尾调查”来组织，而不是围绕内部实现模块来组织。

## 发布目标

本文档描述的是目标发布形态，而不是当前实现状态。`marivo skill` 的发布目标分为三个层次。

### 1. 最小可发布目标

- 提供一个可被 agent 触发的 `marivo` skill
- 明确说明 Marivo 是 HTTP-only，MCP 是适配层
- 提供 surface 路由规则
- 提供一个默认操作 loop
- 提供常见错误与 guardrails
- 提供最小的“读下一份参考资料”索引

### 2. 推荐发布目标

- 补充 semantic layer heuristics
- 补充 readiness/lifecycle 使用守则
- 补充基础 infrastructure troubleshooting 路由
- 补充高频 typed intent 的最小 payload 指导

### 3. 成熟发布目标

- 形成稳定的 references 目录分层
- 对不同任务类型提供一致的“从请求到收尾”的决策路径
- 让不同 agent 在同类任务上表现出相似的正确操作顺序

## Skill 应包含的最小内容集

一个最小可发布的 `marivo skill` 应至少包括以下模块。

### 1. Marivo 是什么

需要明确：

- Marivo 是 HTTP-only agentic analytics system
- MCP 是客户端适配层，不是第二执行面
- skill 是 agent 使用守则，不是协议层

### 2. 何时使用 Marivo

应说明触发条件，例如：

- 用户要求基于证据的结构化分析
- 用户提到 metric、entity、dimension、binding、session、context
- 用户需要 typed intent 调查，而不是 ad hoc SQL
- 用户需要使用 Marivo 的 semantic layer 或 investigation surfaces

### 3. 先选 surface

这是 skill 的核心内容，至少应覆盖：

- action surface：推进分析
- state surface：看 session 决策面
- context surface：看 proposition 局部闭包
- semantic layer：建模与治理
- infrastructure surfaces：health、source、routing、sync 等

### 4. 默认操作 loop

最小默认 loop 应类似：

1. 确认可达性或必要 discovery
2. 发现或解析需要的 semantic object
3. 创建 session
4. 从一个有边界的 typed intent 开始，通常是 `observe` 或 `detect`
5. 读取 session state
6. 必要时读取 proposition context
7. 决定 follow-up intent 或停止
8. 在 investigation 写入结束后显式 terminate session

### 5. 高价值 heuristics

最少应覆盖：

- 什么时候先用 `detect`
- 什么时候先用 `observe`
- 何时应建 reusable semantic object，而不是做一次性 session work
- 时间窗口必须使用结构化对象，且 end 为排他边界
- `lifecycle_status=active` 不等于 `readiness_status=ready`

### 6. 常见错误

最少应覆盖：

- 把 narration 或 MCP summary 当 evidence
- 把 runtime/status/jobs 当 canonical evidence
- 猜 payload 结构
- 使用旧的 step-style public contract
- 把 derived intents 当成无限规划器
- 调查结束后忘记 terminate session

### 7. Read Next 索引

Skill 应提供最小的“下一步读什么”索引，例如：

- steps / typed-intent guardrails
- semantic layer
- readiness
- infrastructure
- governance
- payload cheatsheet

这些内容应指向更细文档，而不是在 skill 本体里完整展开。

## Skill 不应包含的内容

为了避免职责漂移，以下内容不应进入 `marivo skill` 本体：

- 所有 HTTP path 的逐字段文档
- 所有 MCP tool 的逐条清单
- 完整错误码表
- 所有 semantic object family 的完整 schema
- 所有客户端的 MCP 安装与部署细节
- 本地 runtime 管理实现细节
- 任意与 canonical HTTP 契约不一致的示例

## 推荐目录结构

推荐采用以下结构：

```text
skills/marivo/
  SKILL.md
  references/
    steps.md
    semantic-layer.md
    semantic-readiness.md
    http-contracts.md
    planning.md
    infrastructure.md
    governance.md
    payload-cheatsheet.md
```

推荐职责：

- `SKILL.md`：最小决策入口，优先回答“该走哪条路径”
- `references/*.md`：只在需要时按主题展开

## 当前落地映射

当前建议把设计说明与实际 skill 文档保持如下映射关系：

- [`plan/marivo-skill.md`](./marivo-skill.md)：维护基线，定义目标形态、边界、验收标准与更新规则
- `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo/SKILL.md`：agent 的最小决策入口，只回答“是否启用 Marivo、先走哪个 surface、默认 loop 是什么”
- `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo/references/steps.md`：typed intent、state/context、session close-out
- `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo/references/semantic-layer.md`：语义建模、依赖顺序与 activation heuristics
- `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo/references/semantic-readiness.md`：`lifecycle_status` / `readiness_status` 与 blocker 排查
- `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo/references/http-contracts.md`：跨 surface 的共享 HTTP 约束
- 其他 `references/*.md`：只在对应主题需要时展开，不回到“完整协议手册”形态

稳态要求：

- 先更新 HTTP API 文档与 `marivo-mcp`，再判断 skill 是否需要同步
- `SKILL.md` 保持短小，避免变成第二份 README、inventory 或 schema 手册
- `references/*.md` 只拥有一个主题，不跨主题重复展开同一 guardrail

## 内容组织规则

Skill 应遵守以下写作规则：

- 优先写决策规则，不优先写背景介绍
- 优先写边界和反模式，不优先写宽泛概念
- 优先写“何时使用什么”，不优先写“所有东西是什么”
- 用最小示例支撑高价值 guardrails，而不是复制大段 schema

推荐写法：

- “先选 surface，再选 action”
- “若目标是 session 全局判断，优先读 state，不读 context”
- “若目标是单个 proposition 的证据闭包，读 context”

不推荐写法：

- “这里有 50 个工具，你自己选”
- “这里是完整 JSON schema，请记住”

额外约束：

- `SKILL.md` 只保留足以做决策的规则、反模式与 read-next 索引
- 高价值 guardrails 可以在 `SKILL.md` 提及，但详细展开应落到单一主题 reference
- 同一规则如果已经在 `http-contracts.md` 作为跨 surface 约束持有，就不应在 `steps.md` 或 `semantic-layer.md` 中再次写成字段手册
- payload 示例只保留“最小可用请求形状”，不扩展为逐字段 schema 文档

## 与其他文档的边界

### 与 HTTP API 文档的边界

- HTTP API 文档定义规范契约
- skill 不复制字段级文档

### 与 `marivo-mcp` README 的边界

- `marivo-mcp` README 定义安装、运行、MCP transport、工具暴露和环境变量
- skill 不承载运行时安装说明

### 与 service 设计文档的边界

- service 文档定义系统设计与运行时模型
- skill 文档定义 agent 使用策略

### 与具体 prompt 的边界

- prompt 决定当前任务如何表述
- skill 决定 Marivo 相关任务的稳定使用守则

## 维护规则

为避免 skill 漂移，维护时应遵守以下规则：

- HTTP 契约变化时，优先更新 API 文档与 `marivo-mcp`
- 只有当变化影响 agent 的使用决策时，才更新 skill
- 若只是字段名变化且不改变 agent 的路由/顺序/边界判断，不应扩大 skill 变更范围
- skill 中引用的 canonical ref、surface 名称与生命周期术语应与下层文档保持一致

常见更新判断：

- 如果变化影响“先读 state 还是 context”“何时 terminate”“何时选 `observe` vs `detect`”，应更新 skill
- 如果变化只是新增一个 HTTP 字段、错误码或 transport 细节，通常只更新 API/MCP 文档
- 如果变化会改变 agent 对 readiness、routing、governance 的排查顺序，应更新对应主题 reference，而不是把所有细节塞回 `SKILL.md`
- 如果某个 guardrail 同时出现在多个 references 中，应指定一个主文档持有，其他文档只保留短链接或一句边界提示

## 验收标准

一个合格的 `marivo skill` 至少应满足：

- agent 能判断何时启用 Marivo
- agent 能区分 action、state、context、semantic、infrastructure surfaces
- agent 能执行一个稳定的默认 investigation loop
- agent 不会把 MCP 包装或运维 surface 当作 canonical evidence
- agent 能在 session 写入结束后显式 terminate
- skill 本体没有演变为第二份 MCP/HTTP 协议手册

## 总结

`marivo skill` 是 Marivo 的 agent-side 使用策略层，不是执行层，也不是协议层。

期望的稳态是：

- `marivo-mcp` 提供唯一的可执行 MCP 接入面
- `marivo skill` 约束 agent 如何正确使用这套接入面
- HTTP Marivo 继续作为唯一的规范执行边界
- skill 与 MCP 之间保持清晰边界，不重复定义契约

这样既能保留 Marivo 的 HTTP-first 架构，也能显著降低 agent 在真实分析任务中的误用率。
