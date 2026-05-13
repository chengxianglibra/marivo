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
5. Marivo 的 `session_id`、`step_ref`、`artifact_id`、`provenance`、`product_metadata` 不写进 AOI artifact，本地放在 Marivo ExecutionEnvelope。
6. 当前 Marivo 的非核心能力如果 AOI generated model 无法表达，且也不能通过 ExecutionEnvelope 包装清晰表达，需要列入删除候选并由用户确认后在开发中移除。
7. 测试按新 AOI 边界调整，不以旧实现测试全部继续通过作为设计约束。

## 2. 目标

本设计的目标不是让 AOI generated model 出现在所有代码层，而是让它约束 Marivo 分析操作的规范性。

具体目标：

- **单一结构契约**：atomic intent 的请求和 artifact 结构以 `aoi-spec/schema/aoi.schema.json` 生成的 Pydantic model 为准。
- **入口归一**：HTTP 和 MCP 最终都归约到 AOI generated request model 后再进入 runtime。
- **运行时清晰**：runtime 可以用内部 normalization、`AnalysisStepIR`、compiler IR 做执行优化，但不能重新定义一套 atomic intent 结构契约。
- **提交前强校验**：所有 atomic artifact 在写入 artifact store 前通过 AOI generated artifact model 校验。
- **元数据隔离**：AOI artifact 只表达分析产物；Marivo 平台元数据由 ExecutionEnvelope 表达。

## 3. 非目标

- 不把 MCP 工具签名直接暴露为完整 AOI 原始对象。
- 不要求 compiler 或 query executor 直接消费 AOI generated model。
- 不把 artifact store 改成存 Pydantic object；存储层仍可存 JSON dict。
- 不为了保留旧 derived intent 行为而扩展 AOI spec。
- 不在实现阶段修改 AOI spec 或手工编辑 generated model。
- 不以旧测试语义作为新实现必须兼容的边界。

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
| Intent runner | 从 AOI request lowering 到内部执行输入，产出 AOI artifact model | 自定义 parallel artifact contract |
| Compiler/executor | 消费 runtime 归一化后的内部 IR | 绑定 AOI wire model |
| Artifact commit boundary | commit 前校验 AOI artifact model | 存储层自定义 artifact 结构 |
| ExecutionEnvelope | 包装 AOI artifact 与 Marivo 元数据 | 把 Marivo 元数据塞进 AOI artifact |

## 6. HTTP 边界

HTTP atomic intent API 直接切到 AOI shape。

目标状态：

- `POST /sessions/{session_id}/intents/observe` 的 body 使用 AOI generated observe request shape。
- `compare`、`decompose`、`correlate`、`detect`、`test`、`forecast` 同理。
- HTTP response 的分析结果部分使用 AOI generated artifact shape。
- `session_id`、`step_ref`、`artifact_id` 等 Marivo 元数据在 ExecutionEnvelope 外层返回。

当前 hand-written HTTP intent request/response model 可以作为迁移期参考，但不能继续作为 atomic intent 的主契约。切换完成后，atomic HTTP 路由不应再依赖旧的 `intent_request_models.py` 结构语义。

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

## 10. Derived Intent 处理

`attribute`、`diagnose`、`validate` 不进入 AOI core。

处理规则：

- `attribute`：作为 Marivo compatibility operation，尽量编排 `observe`、`compare`、`decompose`。
- `diagnose`：作为 Marivo compatibility operation，尽量编排 `detect`，必要时补充 `compare`、`decompose`。
- `validate`：收敛到 AOI `test`，额外产品语义放入 ExecutionEnvelope 的 `product_metadata`。

如果某个 derived 能力无法由 AOI atomic intent 表达：

1. 先判断能否通过 ExecutionEnvelope 的 `product_metadata` 表达。
2. 如果仍不能表达，将其列入“删除候选能力”清单。
3. 删除前必须由用户确认。
4. 用户确认删除后，开发中同步删除对应 runtime、HTTP/MCP surface、docs 和旧测试。

## 11. 不可表达能力的确认机制

实现计划必须包含一个 inventory 步骤，逐项检查当前 runtime intent 与 AOI v0.1 的覆盖关系。

每项能力给出四类结论之一：

- **direct**：AOI generated model 直接支持。
- **envelope**：AOI artifact 支持核心分析产物，Marivo 额外信息放入 ExecutionEnvelope。
- **orchestrated**：通过多个 AOI atomic intent 编排实现。
- **unsupported**：AOI generated model 与 ExecutionEnvelope 都不能清晰表达。

`unsupported` 项不能在代码中静默保留。实现前必须提交给用户确认：删除、缩窄，或暂停该项。

## 12. 测试原则

测试跟随目标实现，而不是保护旧行为。

保留和新增测试应覆盖：

- HTTP atomic API 使用 AOI request/response shape。
- MCP DTO 能转换为 AOI generated request model。
- Runtime atomic intent 不再接受未校验 dict 主路径。
- Artifact commit 前经过 AOI generated artifact model 校验。
- ExecutionEnvelope 中 Marivo metadata 与 AOI result 分离。
- Derived intent 只做 orchestration 或 envelope metadata，不重新定义 AOI 外的分析产物。
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
10. 重写测试并运行仓库入口验证。

## 14. 验收标准

完成后应满足：

- 删除或破坏 `marivo/contracts/generated/aoi.py` 会让 HTTP atomic API、runtime atomic intent、artifact 校验路径失败。
- HTTP atomic API 的 schema 来自 AOI generated model，而不是旧 hand-written atomic model。
- MCP tool schema 仍然 agent-friendly，但所有 atomic MCP calls 进入 runtime 前都完成 AOI request model 构造。
- Runtime atomic intent 主路径没有 `params: dict[str, Any]` 作为结构契约。
- 每个 committed atomic artifact 都可由 AOI generated artifact model 重新 validate。
- ExecutionEnvelope 不把 Marivo platform metadata 混入 AOI artifact body。
- 测试套件反映 AOI 新边界；旧行为测试已重写或删除。
