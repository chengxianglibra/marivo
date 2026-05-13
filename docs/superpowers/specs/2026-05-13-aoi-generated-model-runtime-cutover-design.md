---
status: draft
created: 2026-05-13
updated: 2026-05-13
supersedes: 2026-05-11-aoi-runtime-boundary-design.md
---

# AOI Generated Model 运行时切换设计

**日期：** 2026-05-13
**状态：** Draft
**范围：** Marivo atomic intent 的 HTTP/MCP 入站契约、runtime intent 边界、artifact 提交边界、derived compatibility intent 的取舍规则、测试改造原则

## 1. 决策

采用 **方案 B：AOI generated model 作为 runtime 分析语义边界，Marivo ExecutionEnvelope 承载平台元数据**。

核心决策如下：

1. HTTP atomic intent API 直接切到 AOI request/response shape。
2. MCP 继续保留 agent-friendly DTO，但 DTO 只作为 adapter，进入 runtime 前必须转换为 AOI generated request model。
3. Runtime atomic intent 不再以 `dict[str, Any]` 作为主入参，而是接收 AOI generated request model 或包含该 model 的 Marivo runtime envelope。
4. Artifact 写入前必须构造并校验 AOI generated artifact model。
5. HTTP/MCP 的最终返回外层统一是 Marivo `ExecutionEnvelope`；其中 `result` 承载 AOI generated artifact，`session_id`、`step_ref`、`provenance`、`product_metadata` 等平台字段只放在 envelope 外层。
6. 当前 Marivo 的非核心能力如果 AOI generated model 无法表达，且也不能通过 ExecutionEnvelope 包装清晰表达，需要列入删除候选并由用户确认后在开发中移除。
7. 测试按新 AOI 边界调整，不以旧实现测试全部继续通过作为设计约束。
8. `artifact_id` 继续作为 AOI artifact 内的标准化可移植引用目标；Marivo 运行时只负责按 `session_id + artifact_id` 解析和转发，不自造新的引用语义。
9. `to_legacy_dict()` 不属于目标状态契约；如为迁移期保留，不能出现在 HTTP atomic intent、MCP atomic tool、runtime commit 的默认路径中。

## 2. 目标

本设计的目标不是让 AOI generated model 出现在所有代码层，而是让它约束 Marivo 分析操作的规范性。

具体目标：

- **单一结构契约**：atomic intent 的请求和 artifact 结构以 `aoi-spec/schema/aoi.schema.json` 生成的 Pydantic model 为准。
- **入口归一**：HTTP 和 MCP 最终都归约到 AOI generated request model 后再进入 runtime。
- **运行时清晰**：runtime 可以用内部 normalization、`AnalysisStepIR`、compiler IR 做执行优化，但不能重新定义一套 atomic intent 结构契约。
- **提交前强校验**：所有 atomic artifact 在写入 artifact store 前通过 AOI generated artifact model 校验。
- **元数据隔离**：AOI artifact 只表达分析产物；Marivo 平台元数据由 ExecutionEnvelope 表达。
- **可编排边界**：runtime 通过 AOI operation registry 选择 request variant、result family 和 runner，不在分散的 handler 里重复编码 intent 分派逻辑。

## 3. 非目标

- 不把 MCP 工具签名直接暴露为完整 AOI 原始对象。
- 不要求 compiler 或 query executor 直接消费 AOI generated model。
- 不把 artifact store 改成存 Pydantic object；存储层仍可存 JSON dict。
- 不为了保留旧 derived intent 行为而扩展 AOI spec。
- 不在实现阶段修改 AOI spec 或手工编辑 generated model。
- 不以旧测试语义作为新实现必须兼容的边界。
- 不在本次切换中增加 batch artifact resolver 或 session-level artifact cache；除非实现阶段出现已证实的 N+1 性能问题。

## 4. 目标调用链

```text
HTTP atomic AOI request / MCP tool DTO
        |
        | HTTP: request body already is AOI generated request model
        | MCP: DTO converts into AOI generated request model
        v
Marivo RuntimeIntentEnvelope
  - session_id
  - actor / execution context
  - request: AOI generated request model
        v
Atomic intent runner
        v
Normalization / semantic validation
        v
AnalysisStepIR / internal compiler IR
        v
Compiler / executor
        v
AOI generated artifact model
        v
Marivo ExecutionEnvelope
  - step_ref
  - artifact_id
  - result: AOI generated artifact payload
  - provenance
  - product_metadata
        v
Artifact store / HTTP response / MCP response
```

## 5. 分层职责

| 层 | 使用 AOI generated model 的方式 | 不负责什么 |
|---|---|---|
| HTTP atomic API | 直接以 AOI request/response shape 作为公开契约 | 重新定义 hand-written atomic request/response |
| MCP tool | 使用轻量 DTO，转换成 AOI request model | 暴露完整 AOI 原始对象或承载业务语义 |
| Runtime intent boundary | 接收 AOI request model 或含 AOI request 的 runtime envelope | 接收未校验 dict 作为主路径 |
| AOI operation registry | 将 intent -> request variant -> result family -> runner 统一登记 | 让 handler 自己猜测支持哪些请求形态 |
| Intent runner | 从 AOI request lowering 到内部执行输入，产出 AOI artifact model | 自定义 parallel artifact contract |
| Compiler/executor | 消费 runtime 归一化后的内部 IR | 绑定 AOI wire model |
| Artifact commit boundary | commit 前校验 AOI artifact model，并支持按 session + artifact_id 回查 | 存储层自定义 artifact 结构 |
| ExecutionEnvelope | 包装 AOI artifact 与 Marivo 元数据 | 把 Marivo 元数据塞进 AOI artifact |

## 6. HTTP 边界

HTTP atomic intent API 直接切到 AOI shape。

目标状态：

- `POST /sessions/{session_id}/intents/observe` 的 body 使用 AOI generated observe request shape。
- `compare`、`decompose`、`correlate`、`detect`、`test`、`forecast` 同理。
- HTTP response 的外层使用 `ExecutionEnvelope`。
- `ExecutionEnvelope.result` 使用 AOI generated artifact shape。
- `session_id`、`step_ref`、`provenance`、`product_metadata` 等 Marivo 元数据在 `ExecutionEnvelope` 外层字段返回。
- `artifact_id` 必须同时满足两条约束：AOI artifact 内保留标准化 handle；`ExecutionEnvelope` 外层也可以携带该 handle，便于 Marivo transport、store、UI 和日志直接索引。

当前 hand-written HTTP intent request/response model 可以作为迁移期参考，但不能继续作为 atomic intent 的主契约。切换完成后，atomic HTTP 路由不应再依赖旧的 `intent_request_models.py` 结构语义。

`ExecutionEnvelope.to_legacy_dict()` 不得作为 HTTP atomic intent 的目标 response path。若短期保留该方法，只允许用于显式标记的迁移适配或测试辅助；新增目标态测试不能断言旧 flat response shape。

Implementation note:

- Atomic HTTP examples must use AOI artifact id references:
  - `compare.left_artifact_id`
  - `compare.right_artifact_id`
  - `decompose.compare_artifact_id`
  - `correlate.left_artifact_id`
  - `correlate.right_artifact_id`
  - `forecast.source_artifact_id`
- Step refs remain Marivo execution metadata and are not valid AOI atomic request fields.

## 7. MCP 边界

MCP 不直接暴露完整 AOI 原始对象。

原因：

- MCP 面向 agent 调用，需要更短、更稳定、更易填写的工具参数。
- MCP 工具可以保留参数默认值、局部简化、清晰错误文案。
- MCP adapter 不应成为第二套业务契约。

目标状态：

- MCP DTO 只做调用体验适配。
- 每个 MCP atomic tool 都必须有明确的 `to_aoi_request()` 或等价转换路径。
- 转换后由 AOI generated request model 执行结构校验。
- MCP 不直接调用接收 dict 的 runtime 方法。

## 8. Runtime Intent 边界

Runtime atomic intent 主入口改为 AOI typed boundary。

建议引入一个窄的 runtime envelope：

```python
class RuntimeIntentEnvelope(BaseModel):
    session_id: str
    actor: str | None = None
    request: AoiAtomicRequest
```

`AoiAtomicRequest` 可以是当前 generated request union 或 Marivo 手写的 union type alias，但 union 成员必须来自 `marivo.contracts.generated.aoi`。

Runtime atomic intent runner 的职责：

1. 接收已通过 AOI 结构校验的 request。
2. 做 Marivo 语义校验，例如 metric 是否存在、artifact ref 是否可解析、engine 是否 ready。
3. 将 AOI request lowering 到内部 normalized input 或 `AnalysisStepIR`。
4. 调用 compiler/executor。
5. 构造 AOI generated artifact model。
6. 交给 commit boundary 写入并返回 ExecutionEnvelope。

## 9. Artifact 与 ExecutionEnvelope

AOI artifact 与 Marivo 元数据分离。

AOI artifact 表达：

- observation / delta / decomposition / correlation / anomaly candidates / hypothesis test / forecast 等分析产物。
- AOI spec 中定义的 artifact id 字段。
- AOI spec 中定义的 result payload 字段。

ExecutionEnvelope 表达：

- `session_id`
- `step_ref`
- `artifact_id`
- `intent_type`
- `provenance`
- `product_metadata`

写入规则：

1. runner 可以临时组装 dict。
2. commit 前必须通过 AOI generated artifact model validate。
3. artifact store 存储 `artifact.model_dump(exclude_none=True)` 的 JSON 形态。
4. HTTP/MCP 返回 ExecutionEnvelope；其中 `result` 是 AOI artifact payload。
5. `ArtifactStore` port 必须正式定义 `resolve_artifact_by_id(session_id, artifact_id)`，而不是只在某个 adapter 或 repository helper 中临时实现。
6. artifact store / repository / downstream resolver 必须通过该 port 方法完成 lookup，确保 cross-step 组合继续使用标准化 artifact handle。
7. `resolve_artifact_by_id` 必须是 session-scoped lookup：同名或同 id artifact 在其他 session 中存在时，不能被当前 session 解析成功。

## 10. Derived Intent 处理

`attribute`、`diagnose`、`validate` 不进入 AOI core。

处理规则：

- `attribute`：作为 Marivo compatibility operation，尽量编排 `observe`、`compare`、`decompose`；其输出可以是 `attribute_bundle` 这类 derived bundle artifact，但 bundle 内的内容必须由 AOI generated artifact model 组装。
- `diagnose`：作为 Marivo compatibility operation，尽量编排 `detect`，必要时补充 `compare`、`decompose`；输出可以是 `diagnosis_bundle`，但仍以 AOI generated artifact model 为组装基础。
- `validate`：收敛到 AOI `test`，额外产品语义放入 ExecutionEnvelope 的 `product_metadata`；输出可以是 `validation_bundle`，但 bundle 只能作为 derived envelope 表达，不能回退成旧手写 bundle contract。

如果某个 derived 能力无法由 AOI atomic intent 表达：

1. 先判断能否通过 ExecutionEnvelope 的 `product_metadata` 表达。
2. 如果仍不能表达，将其列入“删除候选能力”清单。
3. 删除前必须由用户确认。
4. 用户确认删除后，开发中同步删除对应 runtime、HTTP/MCP surface、docs 和旧测试。

## 11. 不可表达能力的确认机制

实现计划必须包含一个 inventory 步骤，逐项检查当前 runtime intent 与 AOI v0.1 的覆盖关系。

每项能力给出四类结论之一：

- **direct**：AOI generated model 直接支持。
- **envelope**：AOI artifact 支持核心分析产物，Marivo 额外信息放入 ExecutionEnvelope，必要时结果是 derived bundle artifact。
- **orchestrated**：通过多个 AOI atomic intent 编排实现。
- **unsupported**：AOI generated model 与 ExecutionEnvelope 都不能清晰表达。

`unsupported` 项不能在代码中静默保留。实现前必须提交给用户确认：删除、缩窄，或暂停该项。

## 12. 测试原则

测试跟随目标实现，而不是保护旧行为。

保留和新增测试应覆盖：

- HTTP atomic API 使用 AOI request/response shape。
- HTTP atomic API response 使用 `ExecutionEnvelope` 外层，`result` 内为 AOI generated artifact。
- HTTP atomic API 不能通过 `to_legacy_dict()` 泄漏旧 flat response shape。
- MCP DTO 能转换为 AOI generated request model。
- Runtime atomic intent 不再接受未校验 dict 主路径。
- AOI operation registry 能拒绝 intent、request variant、result family 不匹配的 registry mismatch。
- Artifact commit 前经过 AOI generated artifact model 校验。
- ExecutionEnvelope 中 Marivo metadata 与 AOI result 分离。
- downstream AOI 引用路径必须覆盖通过 `artifact_id` 引用上游 artifact 的回归测试。
- `resolve_artifact_by_id(session_id, artifact_id)` 必须覆盖同 session 成功、缺失 artifact 失败、cross-session artifact lookup 失败。
- Derived intent 只做 orchestration 或 envelope metadata，结果若是 bundle 也必须由 AOI generated artifact model 组装，不重新定义 AOI 外的分析产物。
- derived compatibility intent 的编排失败必须映射成 envelope-level failure，而不是伪装成 AOI core artifact 成功。
- 被确认删除的能力没有残留 HTTP/MCP/runtime/docs/test surface。

可以删除或重写的测试：

- 依赖旧 hand-written HTTP request/response shape 的 atomic intent 测试。
- 要求 runtime 接收任意 dict 的测试。
- 要求旧 derived intent 高阶输出结构继续存在的测试。
- 将旧行为当成 AOI 必须兼容能力的测试。

## 13. 实施顺序建议

1. 建立 AOI request/artifact mapping inventory。
2. 定义 RuntimeIntentEnvelope 与 ExecutionEnvelope 的最终形态。
3. 先切一个 atomic intent 作为样板，建议 `observe`。
4. 将 HTTP `observe` route 切到 AOI request/response shape。
5. 将 MCP `observe` DTO 转换到 AOI request model。
6. 将 `observe` runner 改为 AOI request 入参、AOI artifact 出参、commit 前校验。
7. 按同一模式迁移 `compare`、`decompose`、`correlate`、`detect`、`test`、`forecast`。
8. 处理 derived compatibility intent。
9. 删除旧 hand-written atomic intent contract。
10. 删除或隔离 `to_legacy_dict()` 目标路径依赖，确保新 transport/runtime 测试不再使用旧 flat response。
11. 重写测试并运行仓库入口验证。

## 14. 性能范围

本次切换优先保证 AOI 契约边界正确，不预先引入批量 resolver 或 session artifact cache。

性能处理规则：

1. `resolve_artifact_by_id(session_id, artifact_id)` 先作为单 artifact lookup port 落地。
2. downstream compare / decompose / correlate / forecast 可逐个解析上游 artifact。
3. 只有在测试或 profiling 证明出现明显 N+1 查询问题后，才增加 batch resolver 或 session-scoped cache。
4. 如果后续增加 batch resolver，其行为必须等价于多次调用 `resolve_artifact_by_id`，不能绕过 session 隔离和 artifact id 校验。

## 15. 验收标准

完成后应满足：

- 删除或破坏 `marivo/contracts/generated/aoi.py` 会让 HTTP atomic API、runtime atomic intent、artifact 校验路径失败。
- HTTP atomic API 的 schema 来自 AOI generated model，而不是旧 hand-written atomic model。
- MCP tool schema 仍然 agent-friendly，但所有 atomic MCP calls 进入 runtime 前都完成 AOI request model 构造。
- Runtime atomic intent 主路径没有 `params: dict[str, Any]` 作为结构契约。
- 每个 committed atomic artifact 都可由 AOI generated artifact model 重新 validate。
- ExecutionEnvelope 不把 Marivo platform metadata 混入 AOI artifact body。
- HTTP/MCP response 的目标路径不依赖 `ExecutionEnvelope.to_legacy_dict()`。
- runtime 可以按 `session_id + artifact_id` 解析任意已提交 artifact，支持 compare / decompose / correlate / forecast 的上游引用组合。
- cross-session artifact id 解析被拒绝，且错误可观测。
- AOI operation registry mismatch、derived orchestration failure、artifact extraction / commit failure 都有独立错误映射和测试覆盖。
- 测试套件反映 AOI 新边界；旧行为测试已重写或删除。

## 16. Runtime 错误与恢复

runtime 需要显式区分 AOI 结构错误、操作语义错误、artifact 解析错误、编排错误、以及提交错误。

建议错误面：

- **AOI request validation failure**：请求不符合 generated model。HTTP 返回 4xx，MCP 返回可修复的参数错误。
- **AOI operation mismatch**：intent、request variant、result family 不匹配 registry。属于开发时配置或路由错误，应快速失败。
- **Artifact not found**：`session_id + artifact_id` 无法解析。HTTP/MCP 返回明确的引用失败，不要降级成空结果。
- **Cross-session lookup failure**：artifact 存在但不属于当前 session。必须拒绝，避免引用串线。
- **Derived envelope orchestration failure**：attribute、diagnose、validate 的组合步骤失败。应返回封装后的失败 envelope，并保留 product_metadata 中的中间状态或 issues。
- **Artifact extraction / commit failure**：AOI artifact 通过校验但写入或抽取失败。需要保留可观测错误码，并避免半提交成功的假象。

恢复原则：

1. 结构错误优先由调用方修正，不进入执行层。
2. 引用错误优先定位上游 `artifact_id` 或 `session_id` 绑定问题。
3. 编排失败不要伪装成 AOI core 失败。
4. 提交失败必须可观测、可重试、可区分是 extraction 还是 storage 问题。

## 17. GSTACK REVIEW REPORT

`plan-eng-review` 后确认的工程修订已并入本 spec：

- HTTP atomic intent 的最终 response contract 是 `ExecutionEnvelope` 外层，`result` 内承载 AOI generated artifact；不是旧 flat response，也不是裸 AOI artifact response。
- `to_legacy_dict()` 明确不属于目标状态，只能作为显式迁移辅助，不能进入默认 transport/runtime path。
- `resolve_artifact_by_id(session_id, artifact_id)` 正式归属 `ArtifactStore` port，并要求 session-scoped lookup。
- 测试矩阵补充 cross-session artifact lookup、registry mismatch、derived envelope failure mapping、artifact reference regression。
- 性能范围保持窄切口：暂不增加 batch resolver / session cache，等 N+1 问题被验证后再扩展。
