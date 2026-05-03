# Marivo Semantic Layer Metric + Binding Revision Todo Task List

## 背景

Marivo semantic layer 当前采用严格的 published object 不可变模型。这个模型保证分析链路可审核、可回溯，但在 agent 维护 metric 定义时暴露出明显成本：一次描述或单位标注修正，需要 deprecate 旧 metric、创建新 metric、迁移 binding、重新 validate / activate，并且如果不能复用原 `metric_ref`，下游引用会被迫改成 `metric.*_v2`。

本计划针对中期产品能力：让 `metric.*` 继续作为稳定业务语义 ref，通过 revision 表达定义修订；让 `binding.*` 支持低成本 revision / rebind，避免复制完整 grounding JSON。短期先修复同 ref create 返回裸 500 的诊断问题，再逐步落地 revision 能力。

本文只规划 Metric + Binding 先行，不一次性把 entity / dimension / time / process / predicate 全部 revision 化。其他 object family 只沉淀可复用原则和后续扩展边界。

## 当前问题

### 1. 同 ref create 命中唯一约束时返回裸 500

`metric_ref` 当前是全局唯一 ref。deprecated metric 仍占有 ref，因此同 ref create 应返回明确冲突，而不是 `Internal Server Error`。

正确语义应为：

- `deprecated` 不释放 ref。
- 同 ref create 返回 `409 semantic_ref_conflict`。
- 响应包含 existing object、status、revision、remediation 和推荐下一步。

### 2. Published metric 缺少同 ref revision 路径

当前 published metric 只能废弃重建。对于 spelling、description、unit label、non-breaking semantics 修正，理想路径应是：

1. 基于当前 latest active metric 创建同 ref 新 revision。
2. validate 新 revision。
3. activate 新 revision。
4. 默认 resolver 指向新 revision。
5. 历史 artifact 继续指向旧 revision。

### 3. Binding 迁移需要手动复制完整结构

metric 修订后，依赖 binding 需要重复创建，并手动复制 `carrier_bindings`、`field_bindings`、`time_bindings`、`imports` 等结构。这既容易出错，也让 agent 把服务端已有的 grounding 结构搬到客户端侧维护。

### 4. Readiness 无法区分兼容修订与 breaking 修订

不是所有 metric 修订都应导致下游 stale。description / display name / unit label 修正通常不改变 binding 和 runtime 编译；metric family、required input slot、observed entity、time grain 等变化才可能需要 binding rebind 或 capability profile 重新认证。

### 5. 文档与 skill 仍容易引导 agent 走 `metric_v2`

现有 skill 和 schema 文档强调 activated 后冻结，但缺少“优先 revision、deprecate 用于语义退出”的维护路径。agent 遇到小修正时容易创建 `metric.xxx_v2`，破坏稳定 semantic ref。

## 目标

1. 保留 `metric_ref` 作为稳定 semantic identity。
2. 支持同 ref metric revision 的创建、读取、validate、activate。
3. 默认 semantic resolution 使用 latest active revision；历史回溯可显式读取旧 revision。
4. 支持 binding 从旧版本派生新 revision，并最小化修改 `bound_object_ref` / metric revision target。
5. 让兼容修订不强制下游 stale，breaking 修订暴露明确 blocker 和 migration guidance。
6. 统一 semantic create 冲突错误，避免唯一约束或状态冲突泄漏为 500。
7. 同步 `docs/semantic` 和外部 `marivo-skill`，让 agent 默认选择 revision 而不是 `metric.*_v2`。

## 非目标

- 不在本计划内实现真正的 unit conversion / output transform。
- 不一次性支持 entity / dimension / time / process / predicate 的 revision API。
- 不改变 Marivo HTTP-only 产品边界；MCP 只能作为客户端 adapter，不作为语义能力来源。
- 不把 binding contract 扩展成 SQL DSL。
- 不要求对旧 metadata DB 做复杂在线迁移；如需要 schema migration，单独拆分兼容策略。

## 设计原则

- **稳定 ref 不等于单行对象**：`metric.*` 表示业务语义 identity，revision 表示该 identity 在某个时间点的冻结定义。
- **默认读最新，审计读精确版本**：运行时默认解析 latest active revision，artifact / step metadata 必须冻结具体 revision。
- **deprecate 表示语义退出**：deprecated 仍保留 ref 所有权，不用于普通 spelling / description / unit label 修正。
- **binding grounding 可复用**：metric revision 不应强迫 agent 手动复制 carrier / field / time / import 结构。
- **兼容性显式化**：service 需要区分 compatible revision 与 breaking revision，并在 readiness / blocker 中解释原因。
- **错误先结构化**：短期没有 revision 能力时，也必须用结构化 409 告诉 agent 当前正确路径。

## 建议实施顺序

1. Phase 0：先修 semantic create 冲突诊断，避免裸 500。
2. Phase 1：冻结 Metric revision contract 和 schema/docs 语义。
3. Phase 2：实现 metric revision 存储、API、resolver/runtime。
4. Phase 3：实现 binding revision / rebind。
5. Phase 4：同步 `docs/semantic` 与 `marivo-skill`。
6. Phase 5：用 `metric.avg_blocked_time` 单位标注修正做端到端验收。

## Todo Task List

### 0. 冲突错误与诊断止血

- [ ] 任务 0.1：补重复 `metric_ref` create 回归测试
  - 范围：`POST /semantic/metrics` 创建已存在 `metric_ref`。
  - 覆盖：现存对象为 `draft`、`published/active`、`deprecated` 三种状态。
  - 验收标准：测试断言响应不是 500，而是结构化冲突错误。

- [ ] 任务 0.2：统一 semantic ref conflict 错误模型
  - 交付物：service-level conflict error 或现有 `SemanticServiceError` 扩展。
  - 错误码：`semantic_ref_conflict`。
  - HTTP status：409。
  - 响应字段：`message`、`code`、`category`、`field_path`、`remediation`、`examples`。
  - 验收标准：同 ref metric create 返回 existing object id、existing lifecycle status、existing revision 和推荐操作。

- [ ] 任务 0.3：捕获唯一约束冲突并转译为 409
  - 范围：metric create 先行；公共 helper 预留给其他 semantic family 复用。
  - 边界：不释放 deprecated ref，不允许用 create 覆盖旧对象。
  - 验收标准：SQLite unique constraint 不再透出为 `Internal Server Error`。

- [ ] 任务 0.4：统一 create / update / lifecycle route 的结构化错误返回
  - 范围：semantic typed object routes 中 create、update、validate、activate、deprecate 的业务错误。
  - 边界：Pydantic schema validation 仍走现有 guided 422。
  - 验收标准：service 层可预期错误都包含 code 和 remediation，不返回纯文本 detail。

- [ ] 任务 0.5：补 API / skill 错误恢复说明
  - 交付物：`docs/api/semantic.md` 或相邻 API guidance、`marivo-skill/references/http-contracts.md`。
  - 内容：`semantic_ref_conflict` 表示 ref 已被治理对象占有；普通修订应走 revision，不应创建 `metric.*_v2`。
  - 验收标准：agent 能从错误响应判断下一步是 create revision、clone with new ref，还是 inspect existing object。

### 1. Metric revision contract

- [x] 任务 1.1：定义 metric stable identity 与 revision 语义
  - 交付物：`docs/semantic/overview.md`、`docs/semantic/metric-v2-schema.zh.md`。
  - 内容：`metric_ref` 是稳定 semantic identity；`revision` 是该 identity 的冻结版本；object id 是内部实例 id。
  - 验收标准：文档明确 default resolution 与 historical resolution 的区别。

- [x] 任务 1.2：定义 metric revision lifecycle
  - 推荐状态模型：同一 `metric_ref` 可存在多个 revision；最多一个 latest active revision；旧 active revision 在新 revision 激活后进入 `superseded` 或等价的非默认 active 状态。
  - 边界：`deprecated` 表示该 `metric_ref` 整体退出新引用，不是 revision 替换的常规状态。
  - 验收标准：文档说明 `draft revision`、`latest active revision`、`superseded revision`、`deprecated identity` 的关系。

- [x] 任务 1.3：定义 revision API 形态
  - 最小 API：
    - `POST /semantic/metrics/{metric_id_or_ref}/revisions`
    - `GET /semantic/metrics/{metric_ref}/revisions`
    - `GET /semantic/metrics/{metric_ref}/revisions/{revision}`
    - `POST /semantic/metrics/{metric_id_or_ref}/revisions/{revision}/validate`
    - `POST /semantic/metrics/{metric_id_or_ref}/revisions/{revision}/activate`
  - 默认 `GET /semantic/metrics/{metric_ref}` 返回 latest active revision。
  - 验收标准：API 文档能说明何时用 object id、metric ref、revision number。

- [x] 任务 1.4：定义 revision 创建 payload
  - 输入：基于 current latest active 的 patch，或显式 full replacement payload。
  - 推荐 v1：支持 full replacement，并允许 `base_revision` 做乐观并发校验。
  - 必填：`base_revision`、`change_summary`、`compatibility`。
  - `compatibility` 枚举：`compatible`、`breaking`。
  - 验收标准：agent 不需要先 deprecate 旧对象即可提交新 draft revision。

- [x] 任务 1.5：定义 artifact / step metadata 冻结策略
  - 范围：observe / compare / decompose 等 typed intent 的 semantic snapshot。
  - 要求：写入 resolved metric revision，而不只写 `metric_ref`。
  - 验收标准：旧 artifact 可回溯旧 revision，新 intent 默认使用新 revision。

#### Phase 1 冻结结果

- `metric_ref` 是 stable semantic identity；`revision` 是同一 identity 下的冻结定义版本；object id 只作为服务端内部实例定位符。
- 默认 `GET /semantic/metrics/{metric_ref}` 和 runtime/catalog resolution 使用 latest active revision；历史回溯必须显式携带 `metric_ref + revision`。
- revision lifecycle 使用 `draft revision`、`latest active revision`、`superseded revision`、`deprecated identity` 四类语义；`deprecated` 表示 identity 退出，不表示释放 ref 或普通替换。
- revision API 最小形态冻结为 create/list/read/validate/activate 五类 HTTP endpoint；本阶段只冻结 contract，不实现 handler。
- revision create v1 采用 full replacement payload，必填 `base_revision`、`change_summary`、`compatibility`；`base_revision` 用于乐观并发，`compatibility` 取值为 `compatible` 或 `breaking`。
- artifact / step metadata 必须冻结 resolved metric revision；新 intent 默认使用新 latest active revision，旧 artifact 继续用已记录 revision 回溯旧定义。

### 2. Metric revision service / runtime

- [x] 任务 2.1：调整 metric storage 支持同 ref 多 revision
  - 方案：解除单列 `metric_ref UNIQUE`，改为 `(metric_ref, revision)` 唯一，并增加 latest/default 标识或 active selector。
  - 边界：旧 metadata DB 的迁移策略单独记录；fresh-init 必须创建新约束。
  - 验收标准：同一 `metric_ref` 可拥有 revision 1 / 2 两条记录。

- [x] 任务 2.2：实现 create metric revision
  - 行为：从指定 base revision 复制当前 contract，应用 replacement payload，生成 draft revision。
  - 校验：`base_revision` 必须仍是当前 latest active，除非请求显式允许基于历史 revision fork。
  - 验收标准：published metric 可创建 revision 2，revision 1 仍可读。

- [x] 任务 2.3：实现 validate / activate metric revision
  - 行为：validate 不改变 default resolution；activate 成功后 revision 2 成为 latest active。
  - 边界：activate 失败不得影响 revision 1 的默认可用性。
  - 验收标准：激活新 revision 是原子切换；失败时旧 revision 仍可 runtime 使用。

- [x] 任务 2.4：更新 semantic resolver / catalog search
  - 默认：`metric.ref` 解析到 latest active + ready revision。
  - 显式：支持按 revision 解析历史定义。
  - 搜索：默认只展示 latest active；detail 模式可展示 revision summary。
  - 验收标准：`resolve(metric.avg_blocked_time)` 返回 revision 2；`resolve(metric.avg_blocked_time, revision=1)` 返回 revision 1。

- [x] 任务 2.5：更新 readiness dependency snapshot
  - 要求：readiness evaluator 能看到依赖对象的 resolved revision。
  - 兼容修订：不因 description / display_name / unit label 等非运行时字段变化标记 binding stale。
  - breaking 修订：暴露 `METRIC_REVISION_BREAKING_CHANGE` 或同类 blocker。
  - 验收标准：同一 metric ref 的 compatible revision 激活后，下游 binding 不自动变为 stale。

#### Phase 2 实施结果

- fresh-init `semantic_metric_contracts` 已支持 `(metric_ref, revision)` 唯一约束，并新增 `base_revision`、`change_summary`、`revision_compatibility`、`is_latest_active`。
- `POST /semantic/metrics` 仍只创建新 stable identity；任意同 ref 历史/当前 revision 存在时继续返回结构化 `409 semantic_ref_conflict`。
- 已实现 metric revision create/list/read/validate/activate HTTP 路由；create 使用 full replacement + `base_revision` 乐观并发。
- 默认 metric ref read、runtime resolver、readiness dependency snapshot 都解析 latest active revision；显式历史读取走 `/revisions/{revision}`。
- compiler metadata 与 step `typed_semantic_snapshot` 已冻结 `resolved_metric_revision` 和 `resolved_metric_object_id`。
- 本阶段未实现 binding rebind；compatible/breaking 的自动 diff 与迁移 guidance 留给 Phase 3。

### 3. Binding revision / rebind

- [ ] 任务 3.1：定义 binding revision contract
  - 交付物：`docs/semantic/typed-binding-contract.zh.md`。
  - 内容：binding ref 是稳定 grounding identity；revision 表达 grounding 或 bound metric target 的修订。
  - 验收标准：文档明确 binding revision 与 metric revision 的关系。

- [ ] 任务 3.2：定义 binding rebind API
  - 最小 API：
    - `POST /semantic/bindings/{binding_id_or_ref}/revisions`
    - `POST /semantic/bindings/{binding_id_or_ref}/rebind`
  - 行为：默认从当前 binding 复制 carrier / field / time / imports，只替换指定字段。
  - 输入：`base_revision`、`target_metric_ref`、`target_metric_revision`、`change_summary`。
  - 验收标准：agent 不需要提交完整 `interface_contract` 即可把 binding 指向新 metric revision。

- [ ] 任务 3.3：实现 binding revision 派生
  - 行为：复制旧 binding 的 grounding 结构，生成 draft revision。
  - 校验：新 bound metric revision 必须存在；binding scope 必须仍与 metric family / required targets 兼容。
  - 验收标准：旧 binding revision 仍可读，新 revision 可 validate / activate。

- [ ] 任务 3.4：实现 rebind 的 coverage 复用校验
  - compatible metric revision：复用原 coverage，通过 validate。
  - breaking metric revision：如果 required metric input、primary time、subject 等发生变化，返回 missing target / incompatible blocker。
  - 验收标准：平均耗时 metric 仅修正单位描述时，binding rebind 不要求复制 JSON；metric input slot 改变时返回明确 blocker。

- [ ] 任务 3.5：补 affected dependents / migration guidance
  - 范围：metric revision activate 或 breaking revision validate 时，返回受影响 binding / profile 列表。
  - 内容：affected ref、current readiness、recommended action。
  - 验收标准：agent 能从响应知道哪些 binding 需要 rebind，哪些无需处理。

### 4. Docs / semantic schema 更新

- [ ] 任务 4.1：更新 `docs/semantic/overview.md`
  - 增加 stable ref / object id / revision 三层身份解释。
  - 增加 latest active resolution 与 historical resolution 的规则。
  - 明确 semantic refs 仍是业务接口，不退化为 object row id。

- [ ] 任务 4.2：更新 `docs/semantic/metric-v2-schema.zh.md`
  - 增加 Metric revision lifecycle 章节。
  - 增加 compatible vs breaking revision 判定示例。
  - 增加 `metric.avg_blocked_time` 单位标注修正示例，明确不创建 `metric.avg_blocked_time_v2`。

- [ ] 任务 4.3：更新 `docs/semantic/typed-binding-contract.zh.md`
  - 增加 binding revision / rebind 章节。
  - 定义 binding 如何指向 metric latest active 或显式 metric revision。
  - 说明 compatible revision 下 grounding 复用规则。

- [ ] 任务 4.4：更新 `docs/semantic/compiler-compatibility-profile.zh.md`
  - 说明 profile 与 subject revision / metric revision 的绑定关系。
  - 定义 metric breaking revision 后 profile stale 条件。
  - 验收标准：profile revision mismatch 与 metric revision drift 的诊断路径清晰。

- [ ] 任务 4.5：更新 API 文档
  - 交付物：`docs/api/semantic.md`。
  - 内容：revision endpoints、409 conflict payload、rebind payload、历史 revision 读取示例。
  - 验收标准：HTTP API 文档不要求用户通过 MCP 或本地 DB 完成 revision 操作。

### 5. `/Users/lichengxiang/source/oss/marivo-skill/marivo` 更新

- [ ] 任务 5.1：更新 `references/semantic-layer.md`
  - 把 “Updates are draft-only. Once activated ... frozen” 改成：activated public revision frozen；普通修订优先 create revision；deprecate 用于语义退出。
  - 增加 agent 操作顺序：inspect latest revision -> create metric revision -> validate -> activate -> inspect affected bindings -> rebind if needed。
  - 验收标准：skill 不再默认引导 `deprecate + metric_v2`。

- [ ] 任务 5.2：更新 `references/semantic-readiness.md`
  - 增加 revision drift / superseded / breaking revision 的 readiness 排查路径。
  - 明确 compatible metric revision 不应被当成 generic stale。
  - 验收标准：agent 能区分 `not_ready`、`stale`、`superseded`、`deprecated`。

- [ ] 任务 5.3：更新 `references/payload-cheatsheet.md`
  - 增加 metric revision full replacement 示例。
  - 增加 binding rebind 最小 payload 示例。
  - 增加 `semantic_ref_conflict` 后的恢复示例。
  - 验收标准：示例覆盖 spelling / description / unit label 修正，不出现 `metric.*_v2` 作为常规方案。

- [ ] 任务 5.4：更新 `references/http-contracts.md`
  - 增加 `409 semantic_ref_conflict`。
  - 增加 remediation 字段解释。
  - 明确 MCP 工具 schema 只是 adapter 暴露，HTTP API 是权威契约。

- [ ] 任务 5.5：更新 `evals/evals.json`
  - 增加评测：用户要求修正 published metric 的 description / unit label。
  - 期望输出：使用 metric revision，不建议 `metric_ref_v2`；若收到 409，读取 existing metric 并创建 revision。
  - 验收标准：skill eval 能防止 agent 回退到 deprecated + recreate 常规路径。

### 6. 端到端验收案例

- [ ] 任务 6.1：建立黄金案例数据
  - 场景：`metric.avg_blocked_time` 已 active，description / semantics 把 milliseconds 误写为 seconds。
  - 依赖：存在 active metric binding，且下游 observe 可使用该 metric。
  - 验收标准：修订前 observe 可用，修订后 metric ref 不变。

- [ ] 任务 6.2：执行 compatible metric revision
  - 操作：基于 latest active 创建 revision 2，只修正 description / semantics 文案。
  - 验收标准：revision 2 validate / activate 成功；revision 1 仍可显式读取。

- [ ] 任务 6.3：验证 default resolution
  - 操作：resolve / observe `metric.avg_blocked_time`。
  - 验收标准：默认使用 revision 2；历史 artifact 仍显示 revision 1。

- [ ] 任务 6.4：验证 binding 不断裂
  - 操作：读取原 binding readiness。
  - 验收标准：compatible revision 下 binding 不因 metric 修订变 stale，不需要复制完整 JSON。

- [ ] 任务 6.5：验证 breaking revision 路径
  - 操作：创建改变 metric family 或 required input slot 的 revision。
  - 验收标准：validate 或 affected dependents 返回明确 blocker，并提示 binding rebind / coverage 修复路径。

## 验证方案

### 单元与 API 测试

- `make test` 覆盖 semantic typed API、metric resolver、binding readiness、artifact semantic snapshot。
- 新增重复 `metric_ref` create 测试，断言 409 JSON error。
- 新增 metric revision create / validate / activate / readback 测试。
- 新增 latest active resolution 与 explicit revision resolution 测试。
- 新增 binding rebind / compatible revision / breaking revision 测试。

### Runtime 测试

- observe 默认消费 latest active metric revision。
- 历史 artifact / step metadata 冻结 resolved metric revision。
- breaking revision 不会静默替换 runtime grounding。

### 文档与 skill 验证

- `rg -n "metric.*_v2|deprecate.*recreate|只能废弃重建" docs/semantic /Users/lichengxiang/source/oss/marivo-skill/marivo` 检查是否仍把 `_v2` 作为常规修订方案。
- `rg -n "semantic_ref_conflict|revision|rebind" docs/semantic docs/api /Users/lichengxiang/source/oss/marivo-skill/marivo` 确认关键概念已覆盖。
- 运行 skill eval，确认“修正 metric 描述/单位标注”优先走 revision。

### 质量门禁

- `make typecheck`
- `make lint`
- `make test`

## 验收标准

- 同 ref metric create 冲突返回结构化 `409 semantic_ref_conflict`，不再出现裸 500。
- Published metric 可以创建同 ref 新 revision，并在 activate 后成为 latest active。
- 默认 semantic resolution 使用 latest active revision，显式 revision 可读取历史定义。
- Compatible metric revision 不导致 binding 手动重建或 readiness stale。
- Breaking metric revision 能返回 affected bindings、blocker 和 rebind guidance。
- `docs/semantic` 明确 stable ref / revision / latest active / historical resolution。
- `marivo-skill` 明确 agent 修订 published metric 时优先 revision，不把 `metric.*_v2` 作为常规路径。
- `metric.avg_blocked_time` 单位标注修正案例完成闭环：ref 不变、下游可用、历史可回溯。
