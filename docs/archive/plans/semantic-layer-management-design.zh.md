# Marivo Semantic Layer 分层治理与 Agent 使用流程设计

本文定义 Marivo semantic layer 在企业与个人场景下的分层管理、权限控制、Agent 使用流程与 promotion 生命周期。

本文是产品与架构设计说明，不是当前 HTTP wire spec，也不是一次性实现计划。具体接口字段、存储表结构和迁移步骤应在后续 task list 中拆分。

## 背景

Marivo 的核心体验是让用户通过 Agent 使用类型化语义对象完成数据分析，而不是把自然语言直接翻译成临时 SQL。随着场景从个人探索扩展到企业协作，semantic layer 需要同时满足两类诉求：

- 分析人员和 Agent 在语义层不完整时仍能推进探索，不被事中审批阻塞。
- 企业可以控制数据权限、指标口径、语义对象发布和跨团队复用，避免临时对象污染正式语义层。

因此，Marivo 需要把 semantic layer 设计成一个可分层解析、可追溯、可升级的语义契约体系。

## 目标

- 支持个人、团队、企业三类使用形态，但不维护三套不同的语义对象模型。
- 支持 Agent 在分析过程中辅助生成临时 semantic objects，并在有复用价值时沉淀到 workspace。
- 支持 workspace 或 session 中的语义对象通过 promotion 流程升级为 official semantic layer。
- 在执行分析前做确定性的 semantic resolution、readiness、compatibility 和 governance preflight。
- 将身份认证、Marivo 操作授权、数据访问授权分层，避免把权限逻辑塞进 semantic object 本体。
- 保留每个分析 step/artifact 使用的 resolved semantic snapshot，保证审计、复现和后续 promotion 有据可依。

## 非目标

- 不让 Marivo 判断某个分析结果是否可以写入正式报告；这由用户、Agent 和组织流程判断。
- 不把 `official` / `workspace` / `session` 作为用户必须理解的结果等级标签。
- 不把 semantic layer 做成最终权限源或完整 IAM 系统。
- 不允许 Agent 通过 raw SQL 绕过 typed intents、governance policy 或底层数据平台 ACL。
- 不在 semantic object 主 contract 中加入用户 ACL、审批状态、组织角色等控制面字段。
- 不把 `lifecycle_status` 扩展成 `official`、`draft_workspace`、`session_only` 等复杂状态枚举；分层归属属于 catalog/provenance/resolution 元数据。

## 核心分层

Marivo semantic layer 使用同一组 typed semantic object contract，但对象可以位于不同管理上下文中：

```text
Official Semantic Layer
  组织认可、默认可发现、跨团队复用

Workspace Semantic Layer
  用户、团队或项目的持续探索与协作区

Session Semantic Layer
  单次分析会话中的临时语义上下文
```

这三层不是结果等级，也不是三套 schema，而是 semantic object 的管理归属与解析上下文。

### Official

Official semantic layer 是组织认可的默认语义资产层。它应按业务域管理，而不是按个人、底层库表或 Agent 任务管理。

示例：

```text
official://commerce
official://growth
official://content
official://ads
official://finance
official://shared
```

每个 official domain 应具备明确 owner：

- business owner：确认 metric、dimension、process object 的业务口径。
- data owner：确认 binding、source object、time surface、数据质量和运行稳定性。
- governance owner：确认敏感标签、数据访问策略和共享范围。
- domain publisher：执行最终 activate / publish 动作。

Official 对象的特点：

- 默认被 catalog search、planner context 和 Agent discovery 使用。
- 默认只暴露 `active + ready` 对象给普通分析流程。
- 同一 stable ref 的变更走 revision，不通过创建 `metric.xxx_v2` 表达普通修订。
- 历史 step/artifact 必须记录 resolved `ref + object_id + revision`，避免 latest active revision 漂移影响审计回放。
- 跨 domain 引用必须显式记录 dependency，不能隐式拼装。

`official://shared` 可承载跨业务域共享对象，例如 `entity.user`、`dimension.region`、`time.event_date`。

### Workspace

Workspace 是持续存在的工作区语义层，用于个人探索、团队协作和项目沉淀。一个用户可以拥有或参与多个 workspace。

推荐 workspace 类型：

```text
workspace://alice/sandbox
workspace://growth-team/q2-retention
workspace://commerce/gmv-diagnosis-2026
```

Workspace 适合保存：

- 分析人员正在验证的新 metric、dimension、process object 或 predicate。
- 对 official semantic object 的候选 revision。
- 尚未完成 owner 审核的 binding 或 compiler profile。
- Agent 从多个 session 中归纳出的可复用 semantic draft。
- 准备提交到 official domain 的 promotion candidate。

Workspace 应具备：

- owner、member、viewer 等最小成员模型。
- 默认业务域或关联 official domains。
- 可引用的 official domain 白名单。
- 与 session 分离的持久生命周期。
- 对 workspace 内同名 refs 的唯一性约束。

Workspace 不应默认污染 official catalog，也不应被所有用户的默认 discovery 搜到。只有当前 session 的 semantic resolution context 显式包含某个 workspace 时，它的对象才参与分析解析。

### Session

Session semantic layer 是一次分析会话中的临时语义上下文。

Session 对象适合以下场景：

- 用户临时定义分析口径，例如“活跃用户先按过去 7 天有行为来算”。
- Official / workspace 中缺少某个拆解维度，但底层 source metadata 暴露了可验证字段。
- Agent 需要为当前问题生成一次性 cohort、scope、predicate 或 derived dimension。
- 分析还处在探索假设阶段，对象口径尚未稳定。

Session 对象默认：

- 只对当前 session 可见。
- 不进入普通 catalog search。
- 不被其他 session 默认复用。
- 可以参与当前 session 的 typed intent 执行，但必须经过权限、readiness-lite、compatibility 和 governance preflight。
- 在有复用价值时，可以由用户确认后保存为 workspace 对象。

简单判断规则：

```text
只服务当前问题 -> session
未来几天或多人会继续使用 -> workspace
组织长期复用且需要统一口径 -> official
```

## Resolution Context

用户不需要理解每个对象来自哪一层，但 Marivo 和 Agent 必须使用确定性的 semantic resolution context。

每个 analysis session 应冻结一个 resolution context：

```json
{
  "official_domains": ["commerce", "shared"],
  "workspace_refs": ["workspace://growth-team/q2-gmv-drop"],
  "session_overlay": true,
  "resolution_order": ["session", "workspace", "official"]
}
```

解析规则：

- 裸 semantic ref 在执行前必须解析为唯一的 `object_id + revision + semantic_context`。
- 如果多个上下文中存在同名对象，resolver 按 session 冻结的 `resolution_order` 解析。
- 如果同一优先级内仍存在冲突，返回 `ambiguous_ref`，由 Agent 引导用户选择，而不是随机选择。
- 允许跨层混用，但必须经过 compatibility preflight。
- Step snapshot 必须记录最终解析到的对象，而不是只保存裸 ref。

典型允许组合：

- workspace metric 引用 official dimension。
- session predicate 作用于 official metric。
- session metric 使用 official binding，但必须验证 binding target 和治理策略。
- workspace candidate revision 依赖 official active metric 的 base revision。

典型需要限制：

- official metric 默认不应隐式引用 workspace 或 session 对象。
- 不同 workspace 对象默认不应混用，除非 session context 显式包含多个 workspace 且 resolver 可唯一解析。
- Agent 不应通过同名 ref 猜测对象来源。

## Semantic Object 生命周期

单个 semantic object 仍沿用已有公共语义：

- `lifecycle_status`: `draft`、`active`、`deprecated`
- `readiness_status`: `not_ready`、`ready`、`stale`
- revision: 同一 stable ref 下的冻结定义版本

分层归属不应扩展 public lifecycle 枚举。`official`、`workspace`、`session` 是 catalog/provenance/resolution 元数据。

### Session 到 Workspace

当 session object 出现复用价值时，Agent 可以建议保存到 workspace。

触发条件：

- 用户明确表示该口径后续还要继续使用。
- 同一 session object 被多次用于分析步骤。
- 多个 session 中出现相似临时对象。
- Agent 判断它是 official semantic gap，而不是一次性分组。
- 需要多人协作补充 binding、验证数据或准备 promotion。

保存动作需要用户确认，避免临时对象大量沉淀为长期资产。

### Workspace 到 Official

Workspace 对象通过 promotion request 升级到 official domain。

Promotion request 不是简单复制对象，而是一个可审查的发布包。

应包含：

- 要新增或修订的 semantic objects。
- 来源 session/workspace 和原始分析问题。
- 业务定义与使用场景。
- 已执行的 profiling、observe、smoke checks 或验证 intent。
- 数据覆盖、空值率、枚举分布、样本量等证据。
- 依赖的 source objects、bindings、official refs 和 compiler profiles。
- 与已有 official objects 的重复、冲突或替代关系。
- revision 兼容性分类、diff summary、required actions。
- Agent 生成的风险提示和待确认问题。

Promotion 可由分析人员、workspace owner 或 Agent 辅助触发，但 Agent 触发正式提交前应获得用户确认。

审批发生在 official domain 的治理面，而不是阻塞分析 session。分析 session 可以继续基于 session/workspace 对象探索，promotion 异步推进。

## 权限模型

权限分为三层：

```text
身份认证 Authentication
  你是谁

Marivo 操作授权 Control-plane Authorization
  你能不能创建、修改、提交、审批 semantic objects

数据访问授权 Data-plane Authorization
  你能不能用某组 semantic objects 读取底层数据
```

Semantic layer 不作为最终权限源，但它必须为权限判断提供结构化上下文。

### 身份认证

企业场景中，身份认证应由企业 IdP、SSO、API Gateway 或上游平台完成。

Marivo 消费已认证身份，并在 session 中冻结最小执行身份：

```json
{
  "execution_identity": {
    "session_user": "alice",
    "actor_ref": "user.alice"
  }
}
```

更完整的企业上下文可包含：

```json
{
  "actor": {
    "user_id": "u_123",
    "username": "alice",
    "groups": ["growth_analyst"],
    "roles": ["analyst"],
    "tenant_id": "company_a"
  },
  "agent": {
    "agent_id": "agent.marivo",
    "acting_for": "u_123"
  }
}
```

`session_user` 不是身份认证本身，只是已认证用户映射到执行引擎侧 username 的输入。

Agent 默认应采用 on-behalf-of 模型：

```text
effective_permission = user_permission ∩ agent_allowed_actions
```

Agent 不应拥有超出用户的数据访问权限。

### Control-plane Authorization

Control-plane 权限控制 Marivo 管理面动作。

建议最小动作集：

```text
workspace:create
workspace:read
workspace:write
workspace:delete

session:create
session:write

semantic:read
semantic:create_draft
semantic:update_draft
semantic:validate
semantic:activate

promotion:create
promotion:review
promotion:approve
promotion:reject

official_domain:admin
official_domain:publish
```

授权范围可以是：

```text
official domain
workspace
session
semantic ref
source object
engine
```

典型规则：

- 分析人员可以创建 session semantic object。
- 分析人员可以在有权限的 workspace 中创建 draft。
- 分析人员可以提交 promotion request。
- Business owner 审核 metric、dimension、process object 口径。
- Data owner 审核 binding、source object、time surface 和 readiness。
- Governance owner 审核敏感标签、策略和可见范围。
- Domain publisher 执行 official activate。

### Data-plane Authorization

Data-plane 权限在 typed intent 执行前进行。它不只看 semantic ref，而是看 semantic resolution 后的完整执行上下文。

示例：

```json
{
  "actor_ref": "user.alice",
  "agent_ref": "agent.marivo",
  "session_id": "sess_123",
  "intent": "decompose",
  "metric_refs": ["metric.gmv"],
  "dimension_refs": ["dimension.channel"],
  "binding_refs": ["binding.gmv_primary"],
  "source_object_refs": ["source_object.orders"],
  "carrier_tables": ["iceberg.commerce.orders"],
  "engine_id": "eng_trino_prod",
  "result_shape": "aggregate",
  "requested_granularity": "day"
}
```

Governance policy 可以基于该上下文执行：

- deny
- aggregate-only
- min group size
- row filter
- field mask
- max rows
- sample / drill-down 限制
- 敏感标签检查

底层 Trino、Snowflake、Databricks 或其他数据平台 ACL 仍是最后一道防线。Marivo 的 governance preflight 不应替代底层数据平台授权。

## 权限校验点

Marivo 应在以下步骤进行权限校验：

1. 创建 session
   - 用户是否已认证。
   - 是否允许创建 analysis session。
   - 是否允许使用指定 official domains、workspaces 和 execution identity。

2. Discover / search semantic objects
   - 用户是否能看到目标 official domain 或 workspace。
   - 对敏感对象是否过滤、降级展示或隐藏详情。

3. Resolve semantic refs
   - 当前 resolution context 是否允许引用该对象。
   - 对象是否对 actor 可见。
   - 是否存在同名冲突或跨 workspace 混用限制。

4. 创建 session semantic object
   - 是否允许在当前 session 创建临时对象。
   - 引用的 source、official object、workspace object 是否可见。
   - 是否允许对相关数据做 profiling 或 sample。

5. 创建或修改 workspace semantic object
   - 是否有 workspace write 权限。
   - 是否允许引用目标 official domain、source、mapping 或 engine。
   - 涉及敏感字段时是否需要 governance owner review。

6. 执行 typed intent
   - semantic resolution。
   - readiness / compatibility。
   - governance policy check。
   - engine execution auth resolution。
   - 底层 engine ACL。

7. 写入或读取 artifact
   - 是否能写入当前 session。
   - Artifact 是否包含敏感维度值或样本。
   - 读取 artifact 时 viewer 是否仍有对应权限。

8. 保存 session object 到 workspace
   - 是否有 workspace write 权限。
   - 是否允许持久化引用的对象和 profiling metadata。
   - 是否包含不应进入 workspace 的用户私有信息或敏感样本。

9. 提交 promotion request
   - 是否能向目标 official domain 提交。
   - 所有依赖是否可见、可引用。
   - 是否满足最小 evidence package 要求。

10. 审批 promotion
    - Business owner、data owner、governance owner 是否分别具备 review / approve 权限。

11. Official activate
    - Required approvals 是否齐全。
    - Required actions 是否完成。
    - Readiness 是否 ready。
    - Compatibility classification 是否可接受。
    - 是否违反 domain naming/ref policy。

## Agent 使用流程

### 正常分析

```text
用户提出问题
  -> Agent 创建 session 并冻结 resolution context
  -> Discover official active + ready semantic objects
  -> 规划 typed intents
  -> Marivo resolve + preflight
  -> 执行 observe / compare / detect / decompose
  -> 产出 artifact / finding / proposition
  -> Agent 解释结果和下一步
```

用户不需要关心对象是否来自 official、workspace 或 session，但 Agent 可以在必要时解释 semantic lineage。

### Official 不完整时的探索

```text
用户提出问题
  -> Agent 发现 official semantic gap
  -> Agent 说明当前可回答部分和缺口
  -> 生成 session semantic object
  -> Marivo 做权限、readiness-lite、compatibility、governance preflight
  -> 执行探索性 typed intents
  -> Agent 基于真实结果判断对象是否有用
  -> 用户确认是否保存到 workspace
```

Marivo 不判断该结果是否可以写正式报告，但必须记录所有 step 使用的 resolved semantic snapshot。

### Workspace 沉淀与协作

```text
Session object 有复用价值
  -> 保存为 workspace draft
  -> Workspace 成员继续补充定义、binding、profiling evidence
  -> Agent 聚合多次 session 中的使用证据
  -> Workspace owner 决定是否提交 promotion
```

Workspace 是异步协作区，不阻塞当前分析。

### Promotion 到 Official

```text
Workspace draft / revision candidate
  -> 生成 promotion package
  -> 提交到 official domain
  -> Business owner 审业务口径
  -> Data owner 审 binding/readiness
  -> Governance owner 审权限与敏感策略
  -> Marivo 校验 required actions
  -> Domain publisher activate
  -> Agent 可选择用 official 对象 replay 原分析
```

Replay 不是强制要求，但当用户需要长期可复用报告或组织共享结论时，Agent 应建议 replay。

## Agent 与 Marivo 的职责边界

Marivo 负责确定性机制：

- Semantic resolution。
- Readiness、compatibility、required actions。
- Governance preflight。
- Revision 和 dependency tracking。
- Step semantic snapshot。
- Promotion package 的机器可校验部分。

Agent 负责协作体验：

- 把自然语言问题拆成 typed analysis plan。
- 解释 semantic gap。
- 辅助生成 session/workspace semantic draft。
- 引导用户选择冲突对象或保存长期对象。
- 汇总 promotion evidence。
- 在 official 发布后建议 replay。

Agent 不应：

- 绕过 typed intents 直接生成 raw SQL 作为主路径。
- 隐式修改 official semantic layer。
- 猜测同名对象或跨 workspace 对象是否可混用。
- 使用超出用户权限的 service identity 查询数据。

## 与商业产品实践的关系

Marivo 的 official semantic layer 可以借鉴 dbt Semantic Layer 的生产路径：语义定义经过版本化、校验和部署后进入 production semantic catalog；运行时 API 消费的是已发布的语义图，而不是分析时任意编辑的对象。

Marivo 与 dbt 的差异在于，Marivo 面向 Agent-driven analysis，需要更强的 session/workspace 探索能力。Session/workspace 对象用于推进分析和收集实证证据，official promotion 则用于组织级复用和治理。

Snowflake、Databricks、Cube、Looker 等产品的共同启发是：

- 语义层可以提供结构化查询入口。
- 生产对象应受权限、发布和审计控制。
- 数据访问权限最终仍要落到底层数据平台、策略或执行网关。
- 探索态与生产态应分离，避免临时定义污染组织资产。

## 设计原则总结

- 用户可以不感知分层，但系统必须确定性解析分层。
- Session 用于立即分析，workspace 用于持续协作，official 用于组织复用。
- 审批发生在 promotion 阶段，不阻塞探索分析。
- Semantic layer 不是权限源，但必须为权限判断提供 resolved semantic context。
- Marivo 不判断结果是否正式可用，但必须记录 lineage、revision 和执行上下文。
- Agent 可以辅助建模和提交 promotion，但不能绕过用户权限、governance policy 或 official owner。
- Public lifecycle 保持简单；分层归属、promotion 状态和权限信息进入 catalog/governance metadata，而不是污染 object contract。
