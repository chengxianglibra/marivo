# Agent-Facing Auditability V1

本文档定义 Marivo 在 agent 数据分析场景中的一期可审计能力。

状态：draft design。本文只定义一期最小可落地范围，不替代 Evidence Engine
长期目标态，也不引入 UI 设计。

## 目的

Marivo 需要让 agent 和用户在一次分析完成后能够回答：

- agent 做了哪些分析步骤
- 每一步调用了什么 intent，产出了什么 artifact，已有 provenance 能说明哪些输入摘要
- 哪些 artifact / finding / proposition / assessment 支撑最终结论
- 哪些 gap、caveat 或未验证假设仍然存在
- 用户如何复盘这个结论是怎么得出的

一期目标不是构建完整审计基础设施，而是提供稳定、结构化、低工程量的读取面，
让 agent 在最终回答前可以拿到足够证据，避免纯自然语言“脑补式总结”。

## 设计原则

### 1. 复用现有对象

一期不新增完整 `AnalysisTrace` 核心域模型，也不改写 Evidence Engine 主链。

复用现有链路：

```text
session -> step -> artifact -> finding -> proposition -> assessment
```

新增读取面只负责补齐缺失的读取角度，不重新定义 evidence semantics。

### 2. 读取面优先，不改变写入路径

一期不要求重构 intent runner、队列、retry、publish switch 或 artifact extraction。
优先暴露当前已经存在或可低成本推导的信息：

- session root
- step history
- artifact refs
- state surface
- proposition context surface
- runtime status surface 中已经存在的粗粒度状态

### 3. Agent-facing 与 operator-facing 分离

`SessionTraceView` 面向 agent 的最终分析复盘，补齐“做过哪些步骤”的缺口。
已有 `SessionStateView` 和 `PropositionContextView` 继续作为 evidence truth。

已有 runtime status surface 仍然面向 operator 排障，例如“卡在哪个 stage”、
“是否 backlog”、“是否 failed”。一期不会把 claim、lease、attempt 这类运行时事实
混入 canonical evidence surfaces。

### 4. 一期可审计不是完整可复现

一期要做到“可追溯”和“可解释”，但不承诺完整 replay。

不强制保存：

- 全量 SQL 执行计划
- 每次查询的完整中间数据
- 每个算法内部状态
- 完整 queue / lease / retry 轨迹
- 不可变合规级审计日志

这些属于后续增强。

## Non-goals

一期明确不做：

- UI audit view
- 完整 replay engine
- 新的通用 graph query language
- claim / lease / attempt / retry 系统
- 全量 provenance event taxonomy
- 最终自然语言报告生成器
- 新的 evidence summary HTTP/MCP 合同
- 每个 intent runner 的全字段 provenance 一次性补齐
- 把 runtime scheduling truth 放进 canonical state/context surface

## 现有基础

Marivo 当前已经具备以下基础：

- `AnalysisSession` / `SessionEvent`：session root 和生命周期事件
- `Step`：intent execution step 的存储模型
- typed intent response：包含 `step_ref`、`artifact_id`、`provenance`
- artifact store：保存 intent 产物
- Evidence Engine：`artifact -> finding -> proposition -> assessment`
- `SessionStateView`：session 级 canonical decision surface
- `PropositionContextView`：单 proposition 的局部 evidence closure
- runtime status endpoints：session / artifact / proposition 粗粒度运行状态

一期设计应该围绕这些对象补齐可读性，而不是新增平行对象体系。

## 接口职责边界

一期 auditability 不再新增一组与现有 session 接口平行的 summary API。只新增
`SessionTraceView`，并用 agent workflow contract 约束 agent 如何组合已有 canonical
surfaces。

| 接口族 | 当前代表接口 | 职责 | 不负责 |
|--------|--------------|------|--------|
| Session root | `POST /sessions`、`GET /sessions/{session_id}`、`GET /sessions`、`POST /sessions/{session_id}/terminate` | 创建、读取、枚举、终止分析容器 | step 历史、artifact payload、canonical evidence 闭包 |
| Intent step execution | `POST /sessions/{session_id}/intents/{intent}` | 执行 typed intent，并返回 `step_ref`、`artifact_id`、typed result | session 级复盘、跨 proposition 证据汇总 |
| Artifact payload | `GET /sessions/{session_id}/artifacts/{artifact_id}` | 按 handle 读取已提交 artifact payload | evidence 判断、step 列表、运行时状态 |
| Session state | `GET /sessions/{session_id}/state`、MCP `get_session_state` | session 级 canonical decision surface：当前 proposition、backing findings、blocking gaps、artifact refs | 步骤时间线、artifact 明细、runtime queue/attempt |
| Proposition context | `GET /sessions/{session_id}/propositions/{proposition_id}/context`、MCP `get_proposition_context` | 单 proposition 的 canonical evidence closure：latest assessment、findings、gaps、inference records、artifact refs | session 全局摘要、批量遍历、runtime 状态 |
| Runtime status | `GET /sessions/{session_id}/runtime-status`、artifact/proposition runtime-status | operator-facing 排障：是否 idle/running、stage、失败或阻塞原因 | canonical evidence truth、最终结论依据 |
| Session trace | `GET /sessions/{session_id}/trace`、MCP `get_session_trace` | agent-facing 步骤时间线：执行过哪些 step、每步产出哪个 artifact、已有 provenance 摘要 | proposition assessment、finding/gap 闭包、完整请求输入、运行时调度真相 |

因此，一期只新增 `SessionTraceView`。最终回答所需的“证据摘要”不是新的服务端
资源，而是 agent workflow 对以下已存在或新增读取面的受约束组合：

```text
SessionTraceView
  + SessionStateView
  + selected PropositionContextView(s)
  -> final-answer evidence outline
```

这样避免 `AnalysisEvidenceSummaryView` 复制 `SessionStateView` /
`PropositionContextView` 的字段语义，也避免出现两个都声称代表“当前证据”的读取面。
Workflow contract 约束的是 Marivo 官方 agent skill、MCP tool 描述和最终回答格式，
不是 HTTP 层的强制调用顺序。

## 新增读取面

### SessionTraceView

`SessionTraceView` 是 session 级步骤时间线。它回答“分析流程是什么样的”。

建议新增：

- HTTP: `GET /sessions/{session_id}/trace`
- MCP: `get_session_trace`

#### Schema

```ts
type SessionTraceView = {
  session_id: string;
  goal: string | null;
  lifecycle_status: string;
  created_at: string;
  updated_at: string;
  steps: SessionTraceStep[];
  artifact_ids: string[];
  schema_version: "session_trace.v1";
};

type SessionTraceStep = {
  step_id: string;
  step_type: string;
  created_at: string;
  summary: string | null;
  artifact_id: string | null;
  output_summary: Record<string, unknown> | null;
  provenance: Record<string, unknown> | null;
  semantic_metadata: Record<string, unknown> | null;
  warnings: SessionTraceWarning[];
};

type SessionTraceWarning = {
  code:
    | "artifact_id_unresolved"
    | "output_summary_unavailable"
    | "provenance_missing"
    | "semantic_metadata_unavailable";
  message: string;
  field: string | null;
};
```

#### 字段语义

- `steps` 按 `created_at ASC, step_id ASC` 稳定排序。
- `lifecycle_status` 来自 session root 的 lifecycle，不表达 runtime backlog。
- `artifact_id` 优先来自 step result / envelope 中的稳定字段；缺失时可以用
  `artifact_store.resolve_artifact_id_for_step(session_id, step_id)` 补齐。
- 如果 artifact fallback 失败，只把该 step 的 `artifact_id` 置为 `null` 并添加
  `artifact_id_unresolved` warning；不得让单个 step 的 artifact 缺失导致整个 trace 失败。
- `output_summary` 是轻量结果摘要，避免把大型 result 原样塞入 trace。一期只能包含
  确定性白名单字段：`intent_type`、`step_type`、`artifact_id`、`status`、`result_type`、
  `artifact_type`、`artifact_schema_version`，以及明确命名的 count 字段，例如
  `row_count`、`candidate_count`、`finding_count`、`driver_count`。不得内联 artifact rows、
  AOI artifacts、driver rows 或任意大型 result payload。
- `provenance` 只暴露当前系统已经记录的信息，不要求一期强制补齐全部 runner。
  一期不新增完整 request input 存储；输入摘要只能来自已记录的 provenance。
- `semantic_metadata` 只在 step store 已经能读取时透出；server-mode 当前不应为了 trace
  强行 join 或重建语义快照。
- `warnings` 是机器可读的 per-step 降级说明，供 agent 区分“合法为空”和“读取面无法解析”。
- `artifact_ids` 是 session 内 trace 可见 artifact id 的去重列表，便于 agent 后续读取。

#### 授权边界

`GET /sessions/{session_id}/trace` 和 MCP `get_session_trace` 必须继承 session root
读取的授权/ownership 语义。能读取 `GET /sessions/{session_id}` 的调用方才可以读取
该 session 的 trace。Trace 暴露 step history、provenance 和 artifact handles，必须被视为
session-private 数据，而不是公开调试信息。

#### 一期数据来源

一期实现可以由 runtime 聚合：

- `session_store.load_events(session_id)` 或 `runtime.get_session(...)`
- `step_store.list_steps(session_id)`
- 如可用，`artifact_store.list_artifacts(session_id)`

如果某些 store 暂时缺字段，应返回 `null` 或空数组，而不是发明假数据。

实现上应把 trace transformation 拆成小的纯 helper，避免把特殊分支堆进
`get_session_trace` orchestration。建议 helper 边界：

- `_artifact_id_for_step(step, artifact_store)`：从 result/envelope 提取 artifact id，
  必要时 fallback 到 artifact store。
- `_output_summary_for_step(step)`：应用确定性字段白名单，生成小型 summary。
- `_warnings_for_step(step, artifact_id, output_summary)`：生成 per-step warning codes。

## Agent Workflow Contract

Agent Workflow Contract 是一期的主要行为约束。它不在 HTTP 层强制调用顺序，而是通过
官方 skill、MCP tool 描述、用户文档和最终回答格式约束 Marivo agent。

### 约束载体

1. `marivo-analysis` skill 必须写明最终回答前的读取顺序。
2. MCP tool docstring 必须写明各读取面的用途边界。
3. `docs/user/marivo-mcp-tools-reference.md` 必须说明 trace/state/context 的组合规则。
4. 最终回答格式必须要求 refs，让未被证据支撑的内容显式降级为假设或待验证方向。

### 最终回答前读取顺序

1. 读取 `get_session_trace(session_id)`，得到关键 step 和 artifact id。
2. 读取 `get_session_state(session_id)`，得到当前 externally visible propositions、
   backing findings、blocking gaps、artifact refs。
3. 对需要写入最终结论的 proposition 调用 `get_proposition_context(session_id, proposition_id)`。
4. 用 trace 解释“做了什么”，用 state/context 解释“凭什么得出结论”。

### 结论引用规则

- 只有 `SessionStateView` 或 `PropositionContextView` 支撑的内容才能写成结论。
- `SessionTraceView` 只能支撑“执行流程”描述，不能单独支撑 proposition 是否成立。
- 最终回答中引用的 proposition 必须有 `proposition_id`；确定性结论必须有
  `latest_assessment` 或明确说明尚未 assessment。
- finding、gap、artifact 的引用应来自 state/context；trace 中的 artifact id 只用于把
  step 与 artifact handle 串起来。
- 没有 context 支撑的推断只能写成“假设”“候选方向”或“待验证”。

### Proposition 展开策略

默认选择范围：

- 对所有会写进最终结论的 `active_propositions` 读取 context。
- 如果 `active_propositions` 已分页或超过 token 预算，优先读取已 assessment、
  有 blocking gaps、或被回答明确引用的 proposition，并在最终回答中说明未展开范围。
- 不从 runtime status 推导结论；runtime status 只能解释“为什么没有更新结果”。

### Caveat 规则

- `trace.steps = []` 是合法状态，表示 session 已创建但尚无执行步骤，不等于失败。
- trace 中部分 step 带 `artifact_id_unresolved` warning 时，最终回答必须说明部分步骤缺少
  artifact handle。
- trace 中部分 step 带 `provenance_missing` warning 时，最终回答不能声称完整输入可审计。
- `SessionStateView.active_propositions` 为空时，最终回答不能声称已有证据化结论；只能说明
  已执行步骤和当前尚无 externally visible proposition。
- `PropositionContextView.latest_assessment = null` 时，不能写确定性结论；如需要解释原因，
  可以读取 proposition runtime status，但 runtime status 仍不构成证据依据。

## Intent Response Baseline

一期不要求重构所有 intent response，但需要稳定以下基线：

- 会产生 artifact 的 intent 必须返回 `artifact_id`。
- 每个 intent response 必须返回 `step_ref`。
- `step_ref` 至少包含 `session_id`、`step_id`、`step_type`。
- `provenance` 可以渐进增强，但不能伪造未知信息。
- 对下游 chaining 有用的 artifact id 必须位于稳定字段，而不是只藏在自然语言 summary 中。

推荐的 `provenance` 一期最低字段：

```ts
type IntentProvenanceV1 = {
  metric?: string;
  time_scope?: unknown;
  filter?: unknown;
  dimensions?: string[];
  granularity?: string;
  semantic_refs?: string[];
  source_refs?: string[];
  query_hash?: string;
  result_hash?: string;
};
```

这些字段是 best effort。某个 runner 无法提供时可以省略。

## MCP 增强

一期只新增一个 MCP auditability 工具：

| Tool | 目的 |
|------|------|
| `get_session_trace` | 读取 session 的步骤时间线 |

已有 MCP `get_session_state` 和 `get_proposition_context` 继续承担 evidence 读取职责。
不新增 `get_analysis_evidence_summary`，避免把 state/context 再包成第二套
canonical evidence projection。

MCP tool 描述必须包含以下边界提示：

- `get_session_trace`：用于解释 step 时间线和 artifact handles，不用于判断结论是否成立。
- `get_session_state`：用于读取 session 级 externally visible decision surface，是最终回答的
  session 级 evidence baseline。
- `get_proposition_context`：用于读取被最终回答引用的 proposition 的 canonical evidence
  closure，是 proposition-level 结论依据。

runtime status 的 MCP 暴露可以作为单独 operator-facing 事项推进，不属于本设计的一期
agent auditability 范围；即使后续补齐，也只能用于排障和解释“为什么还没有结果”，
不能作为最终 evidence 判断依据。

## Skill 增强

`marivo-analysis` skill 应增加以下流程约束：

1. 创建或继续 session 后，按现有规则进行 semantic preflight。
2. 每个 meaningful branch 后读取 `get_session_state`，用于判断当前 evidence state。
3. 最终回答前必须读取 `get_session_trace`，用于生成步骤复盘。
4. 对最终回答中引用的 proposition，读取 `get_proposition_context`。
5. 最终回答必须包含：
   - 分析步骤摘要
   - 主要结论
   - 支撑证据 refs
   - 未解决 gap / caveat
6. 未被 state/context 支撑的内容只能表述为假设或待验证方向。
7. session 结束时继续显式调用 `terminate_session`。

## Agent 最终回答约束

使用 Marivo 完成分析后，agent 的最终回答应至少包含：

- `session_id`
- 执行过的关键步骤和 artifact id
- 主要结论及对应 proposition / assessment refs
- 支撑 findings 的简要描述
- 仍未解决的 gaps 或 caveats

最终回答不要求固定自然语言模板，但必须能被用户复核到稳定 refs。推荐结构：

```text
结论：
- ...

证据：
- proposition prop_... / assessment asmt_...
- finding find_... from artifact art_...

分析流程：
- step_... observe -> art_...
- step_... compare -> art_...

限制：
- ...
```

## Implementation Plan

### Phase 1A: Trace Read Surface

- 新增 `SessionTraceView` Pydantic model。
- 在 runtime 增加 `get_session_trace(session_id)`。
- 增加 HTTP `GET /sessions/{session_id}/trace`。
- 增加 MCP `get_session_trace` tool。
- 增加 tests：
  - session 不存在返回 404 / MCP error
  - 空 session 返回空 `steps`
  - 多 step 按 `created_at ASC, step_id ASC` 排序
  - artifact ids 去重
  - step result 缺 artifact id 时尝试通过 artifact store 补齐
  - artifact fallback 失败时只影响单个 step，并生成 `artifact_id_unresolved` warning
  - `output_summary` 只包含白名单字段，不内联 artifact rows / AOI artifacts / driver rows
  - 缺 provenance / semantic metadata 不失败
  - 缺 provenance 生成 `provenance_missing` warning
  - trace HTTP/MCP 继承 session root 读取授权边界

### Phase 1B: Workflow Contract Docs

- 更新 `marivo-skill/marivo-analysis/SKILL.md`。
- 更新 `marivo-skill/marivo-analysis/references/workflow.md`。
- 更新 `docs/user/marivo-mcp-tools-reference.md`：
  - 新增 `get_session_trace`
  - 明确 `get_session_trace` / `get_session_state` / `get_proposition_context` 的组合顺序
  - 明确 trace 不支撑结论成立，state/context 才是 evidence basis
- 如 HTTP contract 对外稳定，补充 `docs/api/session-trace.md` 并从
  `docs/api/README.md` 链接。

### Phase 1C: Trace Contract Audit

- 检查会产生 artifact 的 intent response 是否已经返回稳定 `step_ref` 和 `artifact_id`。
- 检查 trace 所需的已有 `provenance` 是否能安全透出；缺失时用 warning 暴露，不做跨 runner
  字段归一重构。
- 只修正会导致 `SessionTraceView` 无法正确生成的 contract gap。
- 不在一期做全 runner provenance 字段归一，不强制补齐 source refs、query hash、
  result hash。

## Acceptance Criteria

一期完成后，应满足：

- agent 可以通过一个 MCP tool 读取 session 时间线。
- agent 使用现有 `get_session_state` / `get_proposition_context` 读取最终 evidence basis，
  不需要新的 summary endpoint。
- final answer 不需要 agent 遍历所有低层对象；只需要 trace、state 和被引用 proposition
  的 context。
- 官方 skill 和 MCP 文档明确要求：trace 解释执行过程，state/context 支撑结论。
- trace 对 partial degradation 输出 per-step warning codes，不静默丢失 artifact/provenance 缺口。
- `output_summary` 有确定性白名单，不会退化为 artifact payload projection。
- trace 读取继承 session root authorization / ownership boundary。
- 缺失 provenance 或未完成 assessment 会以 caveat 暴露，而不是静默隐藏。
- 不引入 UI、queue、retry、完整 replay 或第二套 compact evidence projection。

## Future Extensions

后续版本可以在一期基础上扩展：

- `AnalysisTrace` 作为一等 append-only domain object
- immutable audit log with event taxonomy
- full artifact lineage graph
- replay package export
- server-side compact answer bundle / evidence summary helper, if agent workflow proves
  repeated composition is too expensive
- query plan / SQL / scan statistics 强归档
- user-facing audit report rendering
- policy-aware final answer gate
- evidence coverage score

这些扩展不应阻塞一期读取面落地。
