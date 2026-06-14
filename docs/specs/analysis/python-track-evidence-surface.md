# Python 轨道 Evidence Access Surface 设计

状态：target design。本文定义 `marivo.analysis` 暴露给通用 agent（Claude Code、Codex 等）的 evidence 访问面、Python 轨道实现边界，以及它与 [Python Analysis 算子集](python-analysis-operator-design.md) 的衔接方式。

Python 轨道是 **Python-native independent implementation**：实现代码落在 `marivo.analysis` 内，只依赖 Python 轨道 frame、meta、lineage、异常和存储边界。

本文不维护已删除服务链路的一致性，也不要求双写、兼容读取或数据迁移。

## 目的

固定以下问题的统一答案：

- agent 看到一个 result 时，evidence 信息以什么字段挂在 result 上
- agent 想知道"我在这个 session 中已经判断到什么程度"时，调什么、拿到什么 typed 对象
- agent 需要 audit、回放或跨 session 引用时，evidence 链路对象本体如何暴露
- evidence emission 是 step 提交的副作用，还是 agent 显式 trigger
- runtime 在什么规则下生成 followup，agent 收到 followup 时应给予多大信任
- judgment 存储落在 project 本地的什么位置

## 非目标

本文不定义：

- 已删除服务链路的数据迁移、双写兼容或 wire parity
- 业务领域 / 战略性 followup（agent 自身职责，runtime 不冒充）
- semantic axis enumeration 类启发式建议（不暴露）
- `agent_authored` proposition 写入面
- 对外 wire contract
- UI projection 文案

## 主题位置

| 文档 | 职责 | 与本文关系 |
| --- | --- | --- |
| [`python-analysis-operator-design.md`](python-analysis-operator-design.md) | Python 轨道算子集、Frame 契约、Shape-aware DAG 邻接表、`FollowupAction` / `BlockingIssue` / `ConfidenceScope` 基 schema | 本文消费其类型；扩展 `FollowupAction` 加 `category` |
| 本文 Surface 1 / 2 / 3 章节 | evidence 读写、抽取、seeding、assessment 与 followup 规则 | Python 轨道的自包含规则来源 |

## 核心设计原则

### 原则 P1：evidence 是 step 提交的副作用

agent 提交 `session.compare(...)` 时，runtime 自动完成：finding 抽取 → proposition seeding → assessment recompute → result 上 typed 字段填充。agent 不需要显式 trigger evidence。

agent 必须能仅通过算子和 read method 完成完整工作流，不强制学习 evidence 内部分层。

### 原则 P2：三层 surface 分别服务三类场景

| Surface | 默认使用频率 | agent 需要识别的新类型 | 场景 |
| --- | --- | --- | --- |
| Surface 1 result-bound | 高（每步） | 4：`ConfidenceScope`、`FollowupAction`、`BlockingIssue`、`QualitySummary` | 刚提交完一步，看下一步去哪 |
| Surface 2 session-bound | 中（关键节点） | 4 默认入口（`SessionKnowledge`、`Fact`、`OpenItem`、`BlockedFollowup`）+ 5 细分 typed fact（按需） | 综合判断、跨 step 推理、session 恢复 |
| Surface 3 object-bound | 低（审计） | 3：`Finding`、`Proposition`、`Assessment`（`ActionProposal` 不暴露，预留未来） | 回放、UI 绑定 |

Surface 2 的默认 agent surface 压缩为 `knowledge.facts() / open_items() / next_steps()`——agent 在常规分析中只需识别 4 类。

### 原则 P3：面向语义命名，不暴露引擎对象

Surface 2 对象命名按"agent 听得懂的事实形态"组织（`ChangeFact / AttributedDriver / TestedHypothesis / ForecastSummary / AssociationSummary`、`OpenAnomaly`），**不直接暴露** `Proposition[change]`、`Assessment[change_assessment]` 等引擎命名。引擎命名只在 Surface 3 出现。

### 原则 P4：evidence 失败不阻塞分析推进

artifact + findings + confidence_scope + quality_summary 在 SAVEPOINT 之前落 SQLite；savepoint 之内的 seeding / assessment / followup / blocking_issues 若失败，savepoint 回滚但前段保留，外层 tx 仍 COMMIT，`evidence_status='partial'`。agent 总能拿到 result 并继续下一步。Surface 1 通过 `evidence_status` 表达降级；Surface 2 通过 `evidence_completeness` 表达降级。

### 原则 P5：`recommended_followups` 严格限定 C1 + C2

runtime 只暴露两类 followup：

- **C1 (`dag_continuation`)**：由 [DAG 邻接表](python-analysis-operator-design.md) 机械推导的合法下游 operator
- **C2 (`quality_remediation`)**：针对某个 `BlockingIssue` 的确定性补救动作

业务领域 / 战略性建议（C4）与 semantic axis enumeration 类启发式建议（C3）**不进入** `recommended_followups`，**由 agent 自身生成**。runtime 不冒充对业务知识或启发式裁剪的判断力。

宁可空，不要噪音：当无法按 C1 / C2 规则生成确定性 followup 时，字段保持空列表，让 agent 自行决策。

### 原则 P6：Python 轨道独立实现，旧代码只拷贝不依赖

`marivo.analysis.evidence` 是 Python 库唯一 evidence runtime。实现层不得绕过 Python-track isolation。

允许从旧 evidence 链路复制以下素材：

- canonical finding / item identity 规则
- family extractor 的 canonical item key 逻辑
- T1-T6 seed template registry 结构
- assessment precondition / quality / comparability gate 与 status resolution 的纯规则
- 测试 fixture 中表达的稳定 evidence 行为

复用这些素材时必须改成 `analysis` frame/meta/lineage 输入、`AnalysisError` exception taxonomy、session-local `judgment.db` repository，并纳入 import-linter 边界。服务端策略、wire projection 和旧 replay recovery 不进入 Python 轨道。

## Surface 1：Result-Bound

agent 提交 step 后拿到的 result 对象（`MetricFrame`、`DeltaFrame`、`AttributionFrame`、`CandidateSet`、`AssociationResult`、`HypothesisTestResult`、`ForecastFrame`、`ForecastEvaluationResult`、`QualityReport`、`DiagnosisResult`、`DriverScanResult`）必须**直接以扁平字段**携带 evidence 信息，不嵌套包装类，不要求 agent 再发起 read call。

### 扁平字段集

每个 result artifact 暴露以下字段（dataclass `frozen=True` 或等价 immutable）：

| 字段 | 类型 | 来源 |
| --- | --- | --- |
| `artifact_id` | `str` | step executor 在 commit 时生成 |
| `subject` | `Subject` | 由 artifact typed semantics 派生 |
| `source_refs` | `list[ArtifactRef]` | 上游 step artifact refs |
| `lineage` | `ResultLineage` | `AlignmentPolicy`、`MetricDefinitionCompatibility`、cleaning steps、promotion refs、`triggered_by_followup` |
| `confidence_scope` | `ConfidenceScope` | 见 [ConfidenceScope](#confidencescope-跨-step-兼容性) |
| `quality` | `QualitySummary \| None` | commit 时**直接由 artifact payload + lineage 元数据同步计算**的轻量摘要，**不是 `assess_quality` operator 的输出**，**不进 step DAG** |
| `blocking_issues` | `list[BlockingIssue]` | commit 时同步填充 |
| `recommended_followups` | `list[FollowupAction]` | commit 时同步填充；只含 C1 + C2 |
| `evidence_status` | `Literal["complete", "partial", "unavailable"]` | 见 [evidence_status per-field fallback](#evidence_status-per-field-fallback) |

字段挂在 result 的 `meta` 上：

```python
delta = session.compare(current, baseline)
delta.meta.recommended_followups   # 只含 C1 + C2
delta.meta.blocking_issues
delta.meta.quality
delta.meta.evidence_status
```

**不存在** `delta.evidence.*` 嵌套命名空间。所有 Surface 1 字段都通过 `frame.meta` 读取。

实现层字段声明位置：这些字段应收敛到 `BaseFrameMeta`，让 `MetricFrameMeta`、`DeltaFrameMeta`、`AttributionFrameMeta`、`CandidateSetMeta`、`AssociationResultMeta`、`HypothesisTestResultMeta`、`ForecastFrameMeta`、`ForecastEvaluationResultMeta`、`QualityReportMeta` 共享同一 result-bound evidence contract。

`artifact_id` 是 replay-stable canonical identity。现有 `ref` 仅作为 frame loading alias 保留；目标态中 `ref == artifact_id`，新算子不再生成随机 `frame_*` 作为规范身份。

`CandidateSet` 行级 `recommended_followups_json` 与 result-level `recommended_followups` 不同：

- `result.meta.recommended_followups` 是 artifact-level C1 / C2 followup，由 evidence pipeline 在 step commit 时生成。
- `candidate_item.recommended_followups` 是 candidate row payload，通过 `select(..., attribute="recommended_followups")` 读取。

两者复用同一个 `FollowupAction` 类型，但来源、去重和执行 lineage 语义不同。

### `FollowupAction` 扩展

在 [python-analysis-operator-design.md](python-analysis-operator-design.md) 的 base schema 上增加：

```python
class FollowupAction:
    # base schema (per python-analysis-operator-design.md)
    action_id: str
    kind: Literal["submit_step", "open_projection", "adjust_policy", "request_semantic_input"]
    operator: OperatorId | None
    input_refs: list[TypedRef]
    params: TypedParams
    preconditions: list[Condition]
    expected_output_family: FamilyId | None

    # category extension
    category: Literal["dag_continuation", "quality_remediation"]   # required
    source_issue_id: str | None                                     # required when category="quality_remediation"
```

`category` 是必填字段，封闭枚举 `{dag_continuation, quality_remediation}`。新增 category 必须经过 spec 修订，不允许 runtime 私自扩展。

### `result.meta.quality` 与 `session.assess_quality(...)` 的边界

| 项 | `result.meta.quality` | `session.assess_quality(result)` |
| --- | --- | --- |
| 触发 | commit 时自动 | agent 显式调 |
| 是否进 step DAG | 否 | **是**（[python-analysis-operator-design.md](python-analysis-operator-design.md) core operator） |
| 是否产 artifact | 否（embedded summary） | 是（产 canonical `QualityReport[shape]` artifact，可被 evidence chain 抽取成 finding） |
| 复杂度 | 轻量：coverage、null_rate、sample_size、`MetricDefinitionCompatibility` | 完整：参照 [python-analysis-operator-design.md](python-analysis-operator-design.md) `QualityReport[shape]` 全字段 |
| 适用场景 | 默认随每步暴露 | 需要正式 quality artifact 进入 lineage / evidence chain |
| 是否会重复跑 | 否；存于 SQLite | 显式调用每次创建新 step |

### Sampled fold coverage sidecar

Sampled folds produce a linked `CoverageFrame` accessible through `frame.coverage()`. The main MetricFrame stays limited to axis columns plus measure columns; time-slot coverage rows live in the sidecar frame and summarize into `result.meta.quality.sample_coverage_*`.

### `evidence_status` per-field fallback

| `evidence_status` | 失败阶段 | 仍然填充 | 可能为 `null` / 空 |
| --- | --- | --- | --- |
| `complete` | 无 | 所有字段 | 无 |
| `partial` | savepoint 内 seeding / assessment / followup / blocking_issues 写入失败 | `artifact_id`、`subject`、`source_refs`、`lineage`、`quality`、`confidence_scope`、`blocking_issues`（含一条 `kind=evidence_partial` issue） | `recommended_followups`（可能不完整或空） |
| `unavailable` | judgment store 启动期不可用 | `artifact_id`、`subject`、`source_refs`、`lineage`、`quality`、`confidence_scope`（皆由本步执行计算，不依赖 store）；`blocking_issues` 强制含一条 `kind=evidence_store_unavailable` issue | `recommended_followups` 空（无 store 可去重 / 持久化） |

`unavailable` 的语义补充：

- result 是 **in-memory only**，**不写 SQLite**；本进程内下游 operator 仍可直接消费该 result 作为输入（`artifact_id` / lineage 完整）
- 进程重启后 in-memory result 丢失，无法通过 `session.evidence.findings()` / `session.knowledge()` 找回
- `session.knowledge().evidence_completeness` 在 store 恢复前持续 `unavailable`

`partial` 时 `recommended_followups` 可能仍然填充 C1 部分（C1 生成不依赖 seeding），但 agent 应优先信任 `blocking_issues` 中 `kind=evidence_partial` 的提示决定是否重试。

### Result-bound 不承载的语义

下列内容**不进入** Surface 1：

- judgment 状态（`validated` / `refuted` / `inconclusive`）
- `proposition_id` / `assessment_id`
- 跨 step / 跨 session 累积事实
- support / oppose finding 聚合

这些属于 Surface 2 / Surface 3。

## Followup 生成规则

`recommended_followups` 由 runtime 在 commit 阶段同步生成。生成规则是一等契约——任何无法溯源到下面 C1 / C2 规则的 followup 都是 spec violation。

### C1：`dag_continuation`

**判定**：从 [python-analysis-operator-design.md Shape-aware DAG 邻接表](python-analysis-operator-design.md) 出发，对当前 result artifact 的 `(family, shape)` 查表，列出合法下游 operator。

**只在以下条件全部满足时**发出 C1 followup：

1. 下游 operator 可仅以当前 artifact + **默认参数** / **该 operator 自身的封闭枚举参数**执行——不需要 runtime 推断新的 ref（如 `decompose` 的 `axis: SemanticObject | SemanticRef`，runtime 不挑），不需要 runtime 推断 policy（如 `compare` 的另一侧 `metric_frame`，runtime 不配对）
2. 下游 operator 的所有 `input_refs` 在当前 session 中已可解析（要么是当前 artifact，要么是 session 内显式存在的另一 artifact）
3. 不引入未经裁剪的 enumeration（如"对所有 axis 各发一个 decompose followup"——禁止）

**C1 实际可发出的 followup（白名单）**：

| 源 artifact | 可发 C1 followup |
| --- | --- |
| `MetricFrame[scalar]` | `assess_quality` |
| `MetricFrame[time_series]` | `discover.point_anomalies`、`discover.interesting_windows`、`forecast(horizon=default)`、`assess_quality` |
| `MetricFrame[segmented]` | `discover.interesting_slices`、`discover.cross_sectional_outliers`、`assess_quality` |
| `MetricFrame[panel]` | `discover.point_anomalies`、`discover.cross_sectional_outliers`、`discover.interesting_windows`、`forecast(horizon=default)`、`assess_quality` |
| `DeltaFrame[*]` | `discover.driver_axes`、`discover.period_shifts`（time_series_delta / panel_delta）、`discover.interesting_slices`、`assess_quality` |
| `AttributionFrame` | `assess_quality` |
| `CandidateSet[*]` | `assess_quality` |
| `AssociationResult[*]` | `assess_quality` |
| `HypothesisTestResult` | `assess_quality` |
| `ForecastFrame` | `assess_quality` |
| `QualityReport` | （空——不再续接） |

**显式不发 C1 followup 的 operator**：

- `decompose`：需要 `axis: SemanticObject | SemanticRef`，属于 C3 enumeration
- `compare`：需要另一侧 `metric_frame`，runtime 不配对
- `correlate`：需要另一侧 frame，runtime 不挑；lag 固定 zero-lag
- `test`：需要 `hypothesis` + `SamplingPolicy`，runtime 不挑
- `transform`：除了 `assess_quality` 路径，其他 op 需要 predicate / window / 策略参数，属于 C3
- 任何 composite operator：不进入 C1

如果 agent 想做这些动作，agent 自己生成调用。runtime 不在 `recommended_followups` 中替 agent 做这个判断。

### C2：`quality_remediation`

**判定**：对当前 result 的每个 `BlockingIssue`，查 `BlockingIssue.kind → remediation` 映射表；如果存在确定性补救动作，发出 C2 followup 并设置 `source_issue_id`。

**只在以下条件全部满足时**发出 C2 followup：

1. `BlockingIssue.kind` 在 remediation 映射表中
2. 补救动作可仅以当前 artifact + BlockingIssue 提供的字段执行；不需要额外推断（不重叠 C3）
3. 补救动作是可执行的 typed operator call，不是自由文本

**Remediation 映射表**：

| `BlockingIssue.kind` | C2 followup |
| --- | --- |
| `null_rate_high` | `transform(op="impute_nulls", policy=default)`；`transform(op="filter", where=<issue.payload.non_null_predicate>)` |
| `sample_size_low` | （不发——"扩窗口多少"属于 C3 启发式；agent 自决） |
| `comparability_incompatible` | `compare(..., alignment=<issue.payload.suggested_alignment>)`（仅当 `issue.payload.suggested_alignment` 已由 lineage 解出） |
| `definition_drift_detected` | `transform(op="window", window=<issue.payload.definition_valid_range>)`（仅当 valid_range 在 issue payload） |
| `evidence_partial` | meta-followup：`retry_evidence_pipeline`（kind=`adjust_policy`，runtime 内部重跑 seeding / assessment） |
| `cross_session_window_mismatch` | （不发——跨 session 不在 scope） |
| `outlier_winsorize_recommended` | `transform(op="winsorize", policy=<issue.payload.suggested_policy>)`（仅当 suggested_policy 已由 issue 提供） |

凡是需要 runtime 自行推断"什么策略合适"的 issue（如 sample_size_low），**不发** C2——agent 看到 BlockingIssue 后自行决定。**不发比乱发好**。

### 生成规则 conformance

实现层必须满足：

1. **可溯源**：每条发出的 FollowupAction 必须能精确指向 C1 白名单某一行或 C2 remediation 表某一行
2. **不混类**：`category` 与 `source_issue_id` 满足以下不变量：
   - `category="dag_continuation"` ⇒ `source_issue_id IS NULL`
   - `category="quality_remediation"` ⇒ `source_issue_id IS NOT NULL`
3. **不引入推断**：runtime 不自己挑 axis、不自己挑配对 frame、不自己挑 sampling unit。需要这些的算子不进入 followup
4. **可重放**：同一 result 二次生成 followup 集合必须字节相等
5. **不依赖运行时数据统计**：followup 生成必须基于 result shape + lineage + BlockingIssue + session artifact 索引，**不读 frame raw data**

`tests/test_followup_generation_rules.py` 必须覆盖：

- 每个 (family, shape) 组合下 C1 followup 集合完全等于 C1 白名单表的对应行
- 每种 BlockingIssue.kind 下 C2 followup 完全等于 remediation 表对应行（含 "不发" 的情形）
- 任意非白名单 / 非映射表的 followup 出现 → 测试失败

## Surface 2：Session-Bound

agent 在 session 中跑了多步后，需要横切问"我已经知道什么 / 还不确定什么 / 接下来该做什么"。

### 入口

```python
knowledge: SessionKnowledge = session.knowledge()
```

`session.knowledge()` 是 read method，**不创建 step、不进入 lineage**。返回值是 immutable snapshot。

### `SessionKnowledge` 契约（默认入口）

```python
class SessionKnowledge:
    session_id: str
    snapshot_id: str
    snapshot_at: datetime                                                    # timezone-aware UTC
    evidence_completeness: Literal["complete", "partial", "unavailable"]

    def facts(self, kind: FactKind | None = None) -> list[Fact]:
        """按 kind 过滤已建立事实。kind ∈ {"change","driver","tested_hypothesis","forecast","association"}。"""

    def open_items(self, kind: OpenItemKind | None = None) -> list[OpenItem]:
        """待判断 / 待复查项。kind ∈ {"anomaly","question"}。"""

    def blocked_followups(self) -> list[BlockedFollowup]:
        """因 BlockingIssue 而无法直接执行的 followup。"""

    def next_steps(self, top: int = 5) -> list[FollowupAction]:
        """跨所有 result 的 recommended_followups 去重保序，只返回未执行的。"""

    def for_subject(self, subject: Subject) -> SessionKnowledge:
        """按 subject canonical key 过滤的子视图。"""
```

### `evidence_completeness` 语义

| 值 | 含义 |
| --- | --- |
| `complete` | judgment store 健康；所有 step 的 finding/proposition/assessment 都已成功 |
| `partial` | judgment store 健康，但存在 ≥ 1 个 step 处于 `evidence_status=partial`；返回的 facts/open_items 可能少于真实情况 |
| `unavailable` | judgment store 不可用；**所有列表为空，但"空"含义是"未知"，不是"无"**。agent 必须先检查 `evidence_completeness` 再消费列表 |

### 细分 typed fact

`knowledge.facts(kind=...)` 返回的对象按 kind 是具体 typed fact，共享以下基字段：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `id` | str | 稳定 id（底层 `proposition_id` 的 Pythonic 包装） |
| `kind` | `FactKind` | discriminator |
| `subject` | Subject | semantic subject |
| `window` | TimeWindow \| None | observed window |
| `status` | enum | `validated` \| `refuted` \| `inconclusive` \| `pending` |
| `confidence` | float | scalar 0..1 |
| `confidence_basis` | str | 机器可读基础（如 `latest_test_p_lt_alpha`） |
| `source_refs` | list[ArtifactRef] | trace 回产生该 fact 的 step / result |
| `latest_assessment_id` | str | 直读 Surface 3 用 |

附加字段：

| 类 | 附加字段 |
| --- | --- |
| `ChangeFact` | `direction`、`magnitude`、`comparison_window`、`comparison_basis`、`dimension_keys: Mapping[str,str] \| None` |
| `AttributedDriver` | `dimension`、`dimension_keys`、`contribution_value`、`contribution_share`、`contribution_role`、`scope_change_id` |
| `TestedHypothesis` | `hypothesis_family`、`alternative`、`method_family`、`alpha`、`p_value`、`reject_null` |
| `ForecastSummary` | `forecast_window`、`horizon_index`、`forecast_kind`、`prediction_interval` |
| `AssociationSummary` | `left_subject`、`right_subject`、`method_family`、`coefficient`、`lag_mode: Literal["single","sweep"]`、`lag: TimeOffset \| None`、`lag_sweep: LagSweepSummary \| None`、`join_basis` |

### Fact projection rules

typed fact 的字段由 **proposition + latest assessment + seed finding** 三者投影合并。投影规则：

| Fact 字段 | 取自 |
| --- | --- |
| `id`、`subject`、`window`、定义性参数（如 `alpha`、`comparison_basis`） | proposition base / payload |
| `status`、`confidence`、`confidence_basis`、`reject_null`、`p_value` | latest assessment payload |
| `magnitude`、`coefficient`、`prediction_interval`、`contribution_value` 等量化字段 | seed finding payload（assessment recompute 时快照到 fact） |

实现层必须保证投影 deterministic：同一 `(proposition, latest_assessment, seed_findings)` 输入产出同一 fact 对象。

### 待判断对象（`OpenItem`）

仅两种 kind：

| `kind` | 类 | 含义 | 来源 |
| --- | --- | --- | --- |
| `"anomaly"` | `OpenAnomaly` | 异常候选已注册成 proposition，assessment 尚未到 `validated`/`refuted` | seeding 自 `anomaly_candidate` finding |
| `"question"` | `OpenQuestion` | 综合视图，`reason` 字段标明来源 | 见下 |

`OpenQuestion.reason` 封闭枚举：

- `"reopened_gap"`：assessment 触发的 gap reopened
- `"persistent_blocking_issue"`：跨 ≥ 2 个 step 重复出现的 `BlockingIssue`，未被 followup 解决

**不包含**：

- `open_candidates`（非 anomaly 的 `discover` objective）—— 阻塞于 `discover` 多 shape seeding 设计
- `forecast_evaluations` —— 阻塞于 `forecast_evaluation_result` finding family 设计

### `next_steps(top=5)` 语义

跨所有 result `recommended_followups` 去重、**按 emit 顺序（commit_at 升序）保序**、过滤已执行项、返回前 N。

**不做语义排序**。result 上发出的 followup 已经是 C1 / C2，结构性可靠；排序权交给 agent。

去重 key：`(operator, canonical(input_refs), canonical(params))`。

"已执行"判定靠 `followups.executed_step_id`；该字段由带 `triggered_by_followup` lineage 的已 commit step 回填（见 [Followup execution lineage](#followup-execution-lineage)）。

### `for_subject(subject)`

按 [Subject canonical key](#subject-canonical-key) 过滤的子视图，保持同一 schema。

### 同步语义

`session.knowledge()` 返回的 snapshot 必须包含本 session 内**所有已 commit step** 产生的 evidence。新 step commit 完成的瞬间，下一次 `session.knowledge()` 立即可见，无延迟。实现层：调用时从 SQLite 同步查询；不返回过期缓存。

### Followup 执行方式

```python
knowledge = session.knowledge()
for action in knowledge.next_steps(top=5):
    if action.operator == "assess_quality":
        frame = session.get_frame(action.input_refs[0])
        result = session.assess_quality(frame)
    elif action.operator == "decompose":
        delta = session.get_frame(action.input_refs[0])
        result = session.decompose(delta, axis=session.catalog.get(str(action.params["axis"])).ref)
    else:
        # agent 显式选择对应 typed operator，并负责补齐 operator 所需参数
        continue
```

runtime 不提供统一 followup dispatcher。agent 从 `knowledge.next_steps()` 读取 `FollowupAction` 后，按 `action.operator` 显式调用对应 typed operator（`session.observe / compare / decompose / discover / correlate / hypothesis_test / forecast / assess_quality`），并从 `action.input_refs / action.params` 中补齐该 operator 的参数。

所有 followup 已是 C1 / C2 可靠类，**不需要** `accept_class` 参数。

当前公共 `session.*` wrapper 示例只负责执行 typed step，不暴露 followup execution marker，也不会更新 `followups.executed_step_id`；agent 可能在下一次 `next_steps()` 中再次看到同一个 action。`triggered_by_followup` 是内部 / persistence lineage 字段，只有实现层在 step commit 时显式携带该 lineage，runtime 才会回填 `executed_step_id`。

## Surface 3：Object-Bound

为审计、回放提供的引擎对象级访问。**默认 agent 不需要碰**。

### 命名空间

```python
session.evidence.findings(
    artifact_id: ArtifactRef | None = None,
    finding_type: FindingType | None = None,
    subject: Subject | None = None,
) -> Iterable[Finding]

session.evidence.propositions(
    proposition_type: PropositionType | None = None,
    subject: Subject | None = None,
    status: AssessmentStatus | None = None,
) -> Iterable[Proposition]

session.evidence.assessments(
    proposition_id: str | None = None,
    latest_only: bool = True,
) -> Iterable[Assessment]

session.evidence.proposition(proposition_id: str) -> Proposition
session.evidence.latest_assessment(proposition_id: str) -> Assessment | None
session.evidence.trace(proposition_id: str) -> EvidenceTrace
```

`session.findings(...)` / `session.propositions(...)` / `session.assessments(...)`
仅作为 backward-compatibility alias 保留，不作为 agent-facing Surface 3 入口。

**不暴露** `session.action_proposals(...)`——policy engine 未实现，没有写入源；保留命名给未来。

### `EvidenceTrace`

```python
class EvidenceTrace:
    proposition: Proposition
    latest_assessment: Assessment | None
    seed_findings: list[Finding]
    support_findings: list[Finding]
    oppose_findings: list[Finding]
    source_artifacts: list[ArtifactRef]
    source_steps: list[StepRef]
```

`StepRef` 复用 [python-analysis-operator-design.md](python-analysis-operator-design.md) 的 typed ref 定义。

### 对象 schema

Python 轨道暴露的 `Finding` / `Proposition` / `Assessment` 字段集遵循本文 Surface 3 规则，Python 表达层约束：

- 所有对象 `@dataclass(frozen=True)` 或等价 immutable
- 所有引用字段为 typed `*Ref`，禁止 raw string
- subtype payload 用 TypedDict union

### Delta finding payload fields

Delta finding payloads carry the following unit-related field:

- `unit` (`str | None`): The subject metric's declared UCUM unit, sourced from
  `MetricIR.unit`, threaded through frame meta; `null` when the metric has no
  declared unit. Change proposition payloads copy this value.

## 实现范围

Python 轨道主动收窄实现范围以控制规模。下表是目标能力 vs 实际落地：

实现 package 边界：

```text
marivo/analysis/evidence/
  types.py
  identity.py
  extraction/
  seeding.py
  assessment.py
  followups.py
  knowledge.py
  store.py
  pipeline.py
```

`pipeline.py` 提供统一 `commit_result(...)` helper。现有 operator 仍负责计算 DataFrame 与基础语义 metadata；`commit_result(...)` 负责 deterministic identity、Parquet / job / SQLite 写入、finding extraction、proposition seeding、assessment recompute、blocking issue、C1 / C2 followup 和 Surface 1 字段回填。这样避免每个 operator 分散实现 evidence side effect。

| spec 能力 | 落地 | 说明 |
| --- | --- | --- |
| finding 抽取（7 个 family） | ✅ 全实现 | 严格遵循本文 finding 抽取规则 |
| seed template registry（T1-T6） | ✅ 全实现 | 严格遵循本文 seeding 规则 |
| assessment recompute / status 决议 | ⚠️ 简化版 | 仅维护 latest snapshot，不维护完整 supersede 链 |
| gap engine / inference rules | ⚠️ 部分 | 仅 `precondition_gate` + `quality_gate`；`comparability_gate` 复用 lineage compatibility |
| action proposal policy engine | ❌ 不实现 | 用 `FollowupAction` (C1+C2) 合成视图（`next_steps()`）代替 |
| supersede 链全量保留 | ❌ 仅 latest | 老 snapshot 不可读 |
| migration / invalidation policy | ⚠️ 部分 | 仅 soft invalidation；不支持 mixed-version 边界 |
| 跨 session 引用 | ❌ session-local | 当前仅承诺 session 内读写 |
| `agent_authored` proposition | ❌ 仅 system-seeded | |
| `discover` 多 shape seeding | ❌ 仅 anomaly | 等 `discover` 多 shape finding subtype 设计 |
| `forecast_evaluation_result` finding family | ❌ 不实现 | 当前无公共 forecast evaluation operator；等 finding family 与 operator 设计 |
| C3 / C4 followup | ❌ 永不实现 | 见 [原则 P5](#原则-p5recommended_followups-严格限定-c1--c2) |

## 存储设计

### 落点

```text
<project_root>/.marivo/analysis/
  ├── analysis.db                       # SQLite: session index, artifacts, jobs, current_session_id
  └── sessions/<session_id>/
      ├── meta.json                    # session metadata
      ├── jobs/<job_id>.json           # existing analysis job records
      ├── frames/<artifact_id>/
      │   ├── data.parquet             # raw frame data only
      │   └── meta.json                # cached Surface 1 fields
      └── judgment.db                  # SQLite: artifact metadata + finding/proposition/assessment
```

**SQLite 是 source of truth**——artifact metadata、lineage、findings、propositions、assessments、blocking issues、followups 全部在 SQLite。Frame `meta.json` 只缓存 Surface 1 字段，服务 `load_frame()` ergonomics；Parquet 仅承载 raw tabular data，是数据副本，不承载身份或语义。

### 跨 store recovery 协议

step commit 序列：

1. 内存构造 frame → 写 `frames/<artifact_id>/data.parquet.tmp` + fsync
2. 原子 rename 为 `frames/<artifact_id>/data.parquet`
3. 计算 Parquet SHA-256
4. SQLite 开事务：插 artifact metadata（含 `frame_path`、`frame_sha`）+ 抽 finding + 校验 D4 + seed proposition + recompute assessment + 生成 C1 / C2 followup
5. SQLite commit

**失败模式与恢复**：

| 失败发生在 | SQLite 状态 | Parquet 状态 | 启动 recovery 行为 |
| --- | --- | --- | --- |
| 1 之前 | 无记录 | 无 | n/a |
| 1-2 之间 | 无记录 | `.tmp` 孤儿 | GC 扫描 `frames/*/*.tmp` 删除 |
| 2-5 之间 | 无记录 | 正式 frame dir 孤儿 | GC 扫描 `frames/<artifact_id>/` 与 SQLite 反 join，孤儿移到 `.gc/` 后删除 |
| SQLite commit 失败 | 回滚 | 正式 frame dir 孤儿 | 同上 |
| commit 成功 | 完整 | 完整 | n/a |

**SQLite 内** finding / proposition / assessment / followup 在同一事务内 commit，跨 store 一致性化归为单 store 事务一致性。

### Schema

```sql
PRAGMA user_version = 1;
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE artifacts (
  artifact_id              TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  step_type                TEXT NOT NULL,
  artifact_type            TEXT NOT NULL,
  artifact_schema_version  TEXT NOT NULL,
  subject_payload          TEXT NOT NULL,       -- JSON
  lineage_payload          TEXT NOT NULL,       -- JSON
  confidence_scope         TEXT,                -- JSON
  quality_summary          TEXT,                -- JSON
  evidence_status          TEXT NOT NULL,       -- complete | partial | unavailable
  frame_path               TEXT,
  frame_sha                TEXT,
  triggered_by_followup    TEXT,                -- JSON {action_id, source_artifact_id} | NULL
  committed_at_us          INTEGER NOT NULL     -- microseconds since unix epoch UTC
);
CREATE INDEX idx_artifacts_session_type ON artifacts(session_id, step_type);

CREATE TABLE findings (
  finding_id               TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  artifact_id              TEXT NOT NULL REFERENCES artifacts(artifact_id),
  finding_type             TEXT NOT NULL,
  canonical_item_key       TEXT NOT NULL,
  subject_axis             TEXT,
  subject_payload          TEXT NOT NULL,
  observed_window_start_us INTEGER,
  observed_window_end_us   INTEGER,
  quality_status           TEXT,
  payload                  TEXT NOT NULL,
  artifact_schema_version  TEXT,
  extractor_version        TEXT,
  committed_at_us          INTEGER NOT NULL,
  UNIQUE (artifact_id, finding_type, canonical_item_key)
);
CREATE INDEX idx_findings_session_type ON findings(session_id, finding_type);

CREATE TABLE propositions (
  proposition_id     TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  proposition_type   TEXT NOT NULL,
  origin_kind        TEXT NOT NULL,             -- 'system_seeded'
  derivation_version TEXT NOT NULL,
  subject_key        TEXT NOT NULL,
  payload            TEXT NOT NULL,
  seed_finding_refs  TEXT NOT NULL,
  created_at_us      INTEGER NOT NULL,
  UNIQUE (session_id, proposition_id)
);
CREATE INDEX idx_propositions_session_type ON propositions(session_id, proposition_type);
CREATE INDEX idx_propositions_subject ON propositions(session_id, subject_key);

CREATE TABLE assessment_snapshots (
  snapshot_id      TEXT PRIMARY KEY,
  proposition_id   TEXT NOT NULL REFERENCES propositions(proposition_id),
  session_id       TEXT NOT NULL,
  supersedes_id    TEXT,                        -- 当前总为 NULL；schema 预留
  status           TEXT NOT NULL,
  confidence       REAL,
  confidence_basis TEXT,
  payload          TEXT NOT NULL,
  created_at_us    INTEGER NOT NULL,
  is_latest        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_assess_latest ON assessment_snapshots(proposition_id, is_latest);

CREATE TABLE assessment_edges (
  snapshot_id  TEXT NOT NULL REFERENCES assessment_snapshots(snapshot_id),
  finding_id   TEXT NOT NULL REFERENCES findings(finding_id),
  role         TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, finding_id, role)
);

CREATE TABLE blocking_issues (
  issue_id           TEXT PRIMARY KEY,           -- stable hash of (artifact_id, kind, canonical(source_refs))
  session_id         TEXT NOT NULL,
  artifact_id        TEXT NOT NULL REFERENCES artifacts(artifact_id),
  kind               TEXT NOT NULL,              -- BlockingIssue.kind enum
  severity           TEXT NOT NULL,              -- 'warning' | 'blocking'
  payload            TEXT NOT NULL,              -- JSON: full BlockingIssue
  resolved_by_step_id TEXT,                      -- set when a later step removes this issue
  created_at_us      INTEGER NOT NULL
);
CREATE INDEX idx_blocking_issues_session_kind ON blocking_issues(session_id, kind);
CREATE INDEX idx_blocking_issues_artifact ON blocking_issues(artifact_id);
-- 索引支撑 OpenQuestion.reason='persistent_blocking_issue' 跨 artifact 按 kind 检索

CREATE TABLE followups (
  followup_id          TEXT PRIMARY KEY,         -- = action_id (stable hash)
  session_id           TEXT NOT NULL,
  source_artifact_id   TEXT NOT NULL REFERENCES artifacts(artifact_id),
  category             TEXT NOT NULL,            -- 'dag_continuation' | 'quality_remediation'
  source_issue_id      TEXT REFERENCES blocking_issues(issue_id),  -- NOT NULL iff category='quality_remediation'
  operator             TEXT,
  payload              TEXT NOT NULL,            -- JSON: full FollowupAction
  executed_step_id     TEXT,                     -- internal marker set only when commit receives triggered_by_followup
  created_at_us        INTEGER NOT NULL
);
CREATE INDEX idx_followups_session ON followups(session_id);
CREATE INDEX idx_followups_source ON followups(source_artifact_id);
```

Surface 1 字段在 SQLite 的落点对应关系：

| Surface 1 字段 | 落点 |
| --- | --- |
| `artifact_id` / `subject` / `source_refs` / `lineage` / `confidence_scope` / `quality` / `evidence_status` | `artifacts` 表（每 result 一行） |
| `blocking_issues` | `blocking_issues` 表（每 result 0..N 行） |
| `recommended_followups` | `followups` 表（每 result 0..N 行） |

### 时间纪律

- 所有时间列以 `INTEGER` 存 **microseconds since unix epoch UTC**
- 列名后缀 `_us` 显式标注单位
- Python 层 `datetime` 字段一律 **timezone-aware UTC**
- `observed_window` 的单位由 finding payload 自描述

### Schema migration

- 启动时 `PRAGMA user_version` 读出 → 与代码内 `EXPECTED_SCHEMA_VERSION` 比对
- 更小 → 顺序 apply `migrations/v{n}_to_v{n+1}.sql`
- 更大 → 抛 `SchemaVersionMismatch(AnalysisError)`，拒绝打开
- migration 失败 → 抛 `MigrationFailed(AnalysisError)`，保留原 db 不破坏

### 并发与多进程

- WAL 模式启用，允许多 reader + 1 writer
- 同一 session 同时被多进程持有 writer 锁会 `SQLITE_BUSY` → 抛 `SessionLockedByAnotherProcess(AnalysisError)`
- **不支持** 多 writer 协同写同一 session
- **不支持** 跨 session 查询；每 session 独立 db，跨 session 查询不在 scope

### 不写入 judgment.db 的内容

- secrets / credentials（per agent guide）
- 用户 prompt / agent 自由文本输出
- Frame raw data（在 `frames/` 下）

## Identity 与稳定性

### Artifact ID / Session ID 稳定性

| Id | 生成 | 稳定性 |
| --- | --- | --- |
| `session_id` | session 创建时；ULID（time-ordered + 随机） | 跨进程稳定；持久化在 `meta.json` |
| `artifact_id` | step commit 时；`stable_hash(step_type, normalized_inputs, normalized_params, semantic_anchors)` deterministic | **同 input replay 命中同 artifact_id** |
| `finding_id` | `stable_hash(artifact_id, finding_type, canonical_item_key)` | 跟随 `artifact_id` 稳定 |
| `proposition_id` | 按本文 seeding identity normalization | 跨 replay 稳定 |
| `followup.action_id` | `stable_hash(source_artifact_id, category, operator, canonical(input_refs), canonical(params))` deterministic | 同 result replay 命中同 action_id |

**replay 的实现要求**：

- `normalized_inputs` 把 source artifact refs 按 typed ref 标准化
- `normalized_params` 走 RFC 8785 JCS canonical
- `semantic_anchors` 锁定 metric / dimension / calendar 等 catalog id + version

### Subject fields

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `metric` | `str \| None` | metric semantic id |
| `entity` | `str \| None` | entity semantic id |
| `slice` | `dict[str, str | int | float | bool \| None]` | segment key map |
| `grain` | `str \| None` | grain token string (e.g. `"day"` for calendar grains, `"5minute"` for dynamic sub-day grains) |
| `analysis_axis` | `Literal[...]` | frame shape discriminator |

`grain` stores the normalized grain token.  For calendar grains the token
is the unit name (`"day"`, `"week"`, `"month"`, `"quarter"`, `"year"`).
For dynamic sub-day grains the token is `"{count}{unit}"` (e.g.
`"5minute"`).  This token is used both in `subject_key` computation and
in cross-step `ConfidenceScope` compatibility checks.

### Subject canonical key

`subject_key` 计算规则：

1. Subject payload 按 RFC 8785 JCS 规范化
2. nested object 的 key 按 lexical 序排
3. array 元素若有显式 sort_key，按 sort_key 排
4. SHA-256 取前 16 字节 hex

实现位置：`analysis/_subject.py::canonical_key(subject)`。

### Followup execution lineage

step lineage 新增字段 `triggered_by_followup: TriggeredByFollowup | None`：

```python
class TriggeredByFollowup:
    action_id: str
    source_artifact_id: str
    via: Literal["run_followup", "manual"]
```

`triggered_by_followup` 是内部 / persistence lineage 字段，当前公共 `session.*` wrapper 示例不暴露设置入口。实现层若显式记录 typed followup execution，新的 step lineage 使用 `via="manual"`；`via="run_followup"` 仅为历史 lineage / 兼容读取保留，类型字面量不删除，但不再对应现存执行入口。

`next_steps()` 通过 `followups.executed_step_id IS NULL` 过滤未执行项；`via` 只解释该 executed marker 来自哪种 lineage。

## Exception Taxonomy

per [agent-guide.md](../../../agent-guide.md)，所有新 exception subclass `AnalysisError`，携带 `kind / message / hint / details`。

| Exception | kind | 触发场景 |
| --- | --- | --- |
| `EvidenceStoreUnavailable` | `evidence_store_unavailable` | judgment.db 打不开 / IO 错误 |
| `FollowupGenerationRuleViolated` | `followup_rule_violated` | runtime 试图发出非 C1 / C2 的 followup（实现 bug guard） |
| `PropositionNotFound` | `proposition_not_found` | `session.evidence.proposition(id)` id 不存在 |
| `FindingExtractionFailed` | `finding_extraction_failed` | extractor 抛错或违反 D4 contract |
| `SchemaVersionMismatch` | `schema_version_mismatch` | db schema 版本与代码不兼容 |
| `MigrationFailed` | `migration_failed` | migration SQL 失败 |
| `SessionLockedByAnotherProcess` | `session_locked` | 多进程并发写同一 session |
| `EvidencePartial` | `evidence_partial` | seeding/assessment 失败但 artifact + findings 已 commit；作为 `BlockingIssue` 出现在 result |

## Analysis 流程衔接

### Step 提交副作用流

```text
agent: session.compare(current, baseline)
          │
          ▼
   ┌──────────────────────────────────────────────────┐
   │ analysis commit_result(...) pipeline          │
   │  1. build & run ibis expression                  │
   │  2. write frames/<artifact_id>/data.parquet.tmp  │
   │     + fsync                                      │
   │  3. rename to frames/<artifact_id>/data.parquet  │
   │  4. BEGIN SQLite transaction                     │
   │  4a. insert artifact metadata                    │
   │      (evidence_status='complete' 暂定)           │
   │  4b. extract canonical findings (D4 validated)   │
   │      → insert findings                           │
   │  4c. compute & insert confidence_scope,          │
   │      quality_summary                             │
   │  ── SAVEPOINT sp_evidence ──                     │
   │  4d. seed propositions (template registry)       │
   │  4e. recompute affected assessments (latest)     │
   │  4f. generate C1 + C2 followups; compute &       │
   │      insert blocking_issues                      │
   │  ── on 4d-4f success: RELEASE sp_evidence ──     │
   │  ── on 4d-4f failure:                            │
   │       ROLLBACK TO sp_evidence;                   │
   │       UPDATE artifact SET evidence_status='partial';│
   │       INSERT blocking_issue kind=evidence_partial──│
   │  5. COMMIT                                       │
   │  6. return result with all Surface 1 fields      │
   └──────────────────────────────────────────────────┘
          │
          ▼
   delta: DeltaFrame ← Surface 1 字段就位
```

savepoint 设计的关键性质：

- savepoint **之前**的写入（artifact / findings / confidence_scope / quality_summary）在最外层 COMMIT 时一定落盘
- savepoint **之内**的写入（seeding / assessment / followup / blocking_issues）可独立 rollback 而不影响 savepoint 之前的内容
- 失败时仍走最外层 COMMIT，把 `evidence_status='partial'` 与 `kind=evidence_partial` blocking issue 一并写入
- 整个流程仍是**单一外层事务一次 COMMIT**，跨 store 一致性化归为单 store 原子性

### 失败语义

| 阶段 | 失败处理 | `evidence_status` |
| --- | --- | --- |
| 1（ibis 执行） | abort；不返回 result；抛 typed exception | n/a |
| 2-3（Parquet 写） | abort；Parquet `.tmp` 在 GC 中清；抛 typed exception | n/a |
| 4a（artifact insert） | abort 外层 tx；Parquet GC | n/a |
| 4b（finding extraction / D4） | abort 外层 tx；Parquet GC；抛 `FindingExtractionFailed` | n/a |
| 4c（confidence_scope / quality_summary） | abort 外层 tx；Parquet GC | n/a |
| 4d-4f（seeding / assessment / followup / blocking_issues） | `ROLLBACK TO SAVEPOINT sp_evidence`；artifact + findings + confidence_scope + quality_summary 保留；`UPDATE artifact SET evidence_status='partial'`；插一条 `kind=evidence_partial` blocking_issue；继续到 COMMIT | `partial` |
| 5（外层 COMMIT） | 全部回滚；Parquet GC；抛 typed exception | n/a |
| judgment store 启动期不可用 | step 仍执行；result 仅 **in-memory**（不写 SQLite）；intrinsic 字段在；`blocking_issues` 含 `kind=evidence_store_unavailable`；本进程内下游可继续消费，进程重启后丢失 | `unavailable` |

Evidence 失败**不阻塞分析推进**。SAVEPOINT 模型保证 4d-4f 失败不影响前置 artifact / findings / quality 落盘——agent 仍可在 partial 状态下读取 quality 与 confidence_scope。

### Replay 语义

整 session replay 必须保证：

- 所有 `artifact_id` / `finding_id` / `proposition_id` / `followup.action_id` 一致
- `SessionKnowledge.snapshot_id` 同 input + 同 SQLite 状态下一致
- `next_steps()` 排序在同一 codebase version 下稳定

## ConfidenceScope 跨 step 兼容性

`result.meta.confidence_scope` 是**暴露字段**，不是 runtime 自动门控。agent 在跨 result 推理时**应**调 `confidence_scope.compatible_with(other)` 自查；runtime 不会自动拒绝 step。

```python
class ConfidenceScope:
    metrics: list[SemanticObject | SemanticRef]
    dimensions: list[SemanticObject | SemanticRef]
    time_window: TimeWindow
    alignment: AlignmentPolicy | None
    assumptions: list[str]
    definition_versions: Mapping[SemanticObject | SemanticRef, str]

    def compatible_with(self, other: ConfidenceScope) -> ScopeCompatibility:
        """返回 exact / compatible / incompatible / unknown，对齐 MetricDefinitionCompatibility。"""
```

未来可考虑把 `incompatible` 升级为 `BlockingIssue` 自动注入。

## 命名空间总览

```python
# === 公开（agent 默认 surface） ===
session.observe(...)
session.compare(...)
session.decompose(...)
session.discover.<objective>(...)
session.correlate(...)
session.hypothesis_test(...)
session.forecast(...)
session.assess_quality(...)

session.knowledge() -> SessionKnowledge

# === result meta 字段（自动填充） ===
result.meta.artifact_id
result.meta.lineage
result.meta.confidence_scope
result.meta.quality
result.meta.blocking_issues
result.meta.recommended_followups        # C1 + C2 only
result.meta.evidence_status

# === 半公开（audit / advanced） ===
session.evidence.findings(...)
session.evidence.propositions(...)
session.evidence.assessments(...)
session.evidence.proposition(id)
session.evidence.latest_assessment(prop_id)
session.evidence.trace(prop_id)

# === 内部 ===
session._judgment_store
session._extract_findings(...)
session._seed_propositions(...)
session._recompute_assessments(...)
session._generate_followups(...)
```

## SKILL.md 暴露范围

[`marivo/skills/marivo-analysis/SKILL.md`](../../../marivo/skills/marivo-analysis/) 应仅写入：

- 算子集
- `session.knowledge()` 与 `knowledge.facts/open_items/next_steps()` 默认入口（4 类）
- result meta 字段名 + walkthrough 示例
- `session.assess_quality(...)` 与 `result.meta.quality` 的区分
- `knowledge.next_steps()` + agent 显式 typed operator dispatch 的用法
- **`recommended_followups` 仅含 C1 + C2 的明确声明**——避免 agent 期待战略性建议

**不在 SKILL.md 暴露**：

- 5 类细分 typed fact 的完整字段集（放 `references/`）
- Surface 3 引擎对象
- judgment.db schema
- followup 生成规则白名单细节

SKILL.md 必须使用 [python-analysis-operator-design.md](python-analysis-operator-design.md) canonical 写法：`session = analysis.session.get_or_create(...)` + `session.observe(timescope=..., dimensions=...)`。不容忍两套并行写法。

## 典型用法示例

### 单步分析 + 直接读 followup（Surface 1）

```python
session = analysis.session.get_or_create(name="dau_investigation")
current = session.observe(
    metric=session.catalog.get("analytics.dau"),
    timescope={"start": "2026-05-01", "end": "2026-05-07"}, grain="day",
)
baseline = session.observe(
    metric=session.catalog.get("analytics.dau"),
    timescope={"start": "2026-04-24", "end": "2026-04-30"}, grain="day",
)
delta = session.compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"))

if delta.meta.blocking_issues:
    for issue in delta.meta.blocking_issues:
        print(issue.kind, issue.message)

for followup in delta.meta.recommended_followups:
    # category 必是 dag_continuation 或 quality_remediation
    if followup.category == "quality_remediation":
        print(f"remediation for issue {followup.source_issue_id}: {followup.operator}")
    else:
        print(f"valid next operator: {followup.operator}")
```

### 综合判断 + typed dispatch 执行 followup（Surface 2）

```python
knowledge = session.knowledge()
if knowledge.evidence_completeness == "unavailable":
    raise RuntimeError("judgment store unavailable; cannot reason about session state")

for change in knowledge.facts(kind="change"):
    print(change.subject, change.direction, change.magnitude, change.status)

for question in knowledge.open_items(kind="question"):
    print(question.reason, question.subject)

# next_steps 全是 C1 + C2，agent 自行评估业务相关性
next_steps = knowledge.next_steps(top=3)
for action in next_steps:
    if action.operator == "assess_quality":
        frame = session.get_frame(action.input_refs[0])
        result = session.assess_quality(frame)
    elif action.operator == "decompose":
        delta = session.get_frame(action.input_refs[0])
        result = session.decompose(delta, axis=session.catalog.get(str(action.params["axis"])).ref)
```

### 审计（Surface 3）

```python
for prop in session.evidence.propositions(proposition_type="change"):
    trace = session.evidence.trace(prop.id)
    print(trace.proposition.subject)
    print(trace.latest_assessment.status if trace.latest_assessment else None)
    print(len(trace.support_findings), len(trace.oppose_findings))
```

## 落地建议

实施切成 10 个任务，分 4 个 milestone。原则：先打通一条最薄的端到端竖切（compare → ChangeFact 全链路），再横向铺开其他 family 与 surface，最快暴露架构问题。

### Milestone A：基础设施（横切铺基础）

T1-T3 串行最小耗时；T2/T3 可部分并行。整体约 1 周。

#### T1. Contract types & exceptions

- 所有 dataclass / TypedDict / `*Ref`：`Subject`、`ResultLineage`、`ConfidenceScope`、`QualitySummary`、`BlockingIssue`、`FollowupAction`（含 `category` 扩展）、5 类 Fact 子类、2 类 `OpenItem` 子类、`EvidenceTrace`、`Finding`、`Proposition`、`Assessment`、`TriggeredByFollowup`
- Exception 表全部 8 类，subclass `AnalysisError`
- 不含任何 runtime 行为

**验收**：类型构造 + immutability + `to_dict()` 序列化 round-trip。

**规模**：1-2 天。**依赖**：无。

#### T2. Identity primitives

- `stable_hash()` 用于 `artifact_id` / `finding_id` / `proposition_id` / `followup.action_id`
- `canonical_key(subject)` JCS (RFC 8785) + lexical key sort + SHA-256 截 16 字节
- `normalize_inputs` / `normalize_params` JCS canonical
- 纯函数，无 IO

**验收**：固定 fixture 输入产固定字节输出；replay 等价测试。

**规模**：1-2 天。**依赖**：T1。

#### T3. judgment.db storage layer

- SQLite schema：`artifacts` / `findings` / `propositions` / `assessment_snapshots` / `assessment_edges` / `blocking_issues` / `followups` + 索引
- `PRAGMA user_version` + migration framework
- WAL 启用、connection lock 检测（`SessionLockedByAnotherProcess`）
- Repository 模式：每张表 CRUD
- 跨 store recovery：startup GC 扫描 `frames/*.tmp` 与孤儿 `.parquet`
- SAVEPOINT helper（包装 4d-4f 失败回滚 + `evidence_status='partial'` 写入逻辑）

**验收**：schema 完整性、migration round-trip、锁竞争、GC 情景；含 crash injection 测试覆盖跨 store recovery。

**规模**：3-4 天。**依赖**：T1。这是基础，所有后续任务都依赖它。

### Milestone B：第一条竖切（验证架构）

只覆盖 `observe` + `compare` → `DeltaFrame` → `ChangeFact` 一条链路。目的是用最小代码量验证 SAVEPOINT 流程、id 稳定性、Surface 1/2 数据流是否真的能跑通。整体约 1.5 周（端到端最容易踩问题，要留 buffer）。

#### T4. Extractor + seeding + assessment — change family only

- `metric_value` / `delta` finding extractor（仅这两种 family）
- T1 (change proposition) seed template
- `change_assessment` recompute（latest only）
- support / oppose edge 写入
- 不实现其他 6 个 family

**验收**：fixture-based 测试 compare → finding → proposition → assessment 链路 ids 与字段稳定。

**规模**：2-3 天。**依赖**：T2 + T3。

#### T5. Step executor + Surface 1 minimal

- `analysis.session.session()` + `observe` + `compare` 三个 operator hook 进 executor
- Pipeline：ibis → Parquet → SQLite tx → SAVEPOINT 流程图全跑通
- `confidence_scope` / `quality_summary` 同步计算
- `BlockingIssue` 注入逻辑（仅 `null_rate_high` / `comparability_incompatible` / `evidence_partial` 三种 kind）
- result 扁平字段填充
- `evidence_status` 三态实现

**验收**：端到端 `session.compare(...)` 返回带完整 Surface 1 字段的 result；partial / unavailable 路径覆盖。

**规模**：3-4 天。**依赖**：T3 + T4。

#### T6. Followup generator + Surface 2 minimal

- C1 白名单表实现（仅覆盖 `MetricFrame[time_series]` / `DeltaFrame[*]` 两行）
- C2 remediation 映射表实现（仅 `null_rate_high` / `comparability_incompatible` / `evidence_partial`）
- `FollowupGenerationRuleViolated` guard
- `SessionKnowledge` 最小实现：`facts(kind="change")` 投影、`next_steps()` 去重保序
- agent 显式 typed operator dispatch 示例（从 `knowledge.next_steps()` 读取 action，再调用对应 operator）
- `triggered_by_followup` 内部 lineage 写入（`via="manual"`；`via="run_followup"` 仅历史兼容；不通过当前公共 wrapper 示例暴露）

**验收**：完整脚本 `observe` → `compare` → `knowledge.facts(kind="change")` → `next_steps()` → agent 显式 typed operator dispatch 跑通；followup 生成规则 conformance test 通过。

**规模**：2-3 天。**依赖**：T5。

> 🎯 **第一个 demo-able milestone**：可以让 agent 完整跑一个 DAU WoW 比较 + 拿到 ChangeFact + 选 followup + 自动执行。如果架构有根本问题，这一步暴露。完成后必须做一次 agent 真实 walkthrough，验证 C1 / C2 白名单是否真的足够 agent 使用。

### Milestone C：横向铺开（按 family 并行）

T4-T6 验证了竖切可行后，剩下的 family / operator 可以多个工程师并行做。整体约 1.5 周（并行做能压缩）。

#### T7. 剩余 finding family extractors + seed templates

- 5 个剩余 family extractor：`decomposition_item`、`anomaly_candidate`、`correlation_result`、`test_result`、`forecast_point`、`observation`
- T2-T6 seed templates
- 各自的 `*_assessment` recompute 逻辑

**验收**：每 family 一组 fixture 测试；finding count + `canonical_item_key` + `subject_axis` 与 spec 一致。

**规模**：5-7 天（最大单任务；可考虑拆 2-3 个 sub-PR by family）。**依赖**：T6 验证完成。

#### T8. 剩余 operator step executor 接入

- `decompose` / `discover` / `correlate` / `hypothesis_test` / `forecast` / `assess_quality` / `transform` 全部接入 executor pipeline
- C1 白名单表补齐其余行（`MetricFrame[scalar/segmented/panel]`、`AttributionFrame`、`CandidateSet`、`AssociationResult`、`HypothesisTestResult`、`ForecastFrame`）
- C2 remediation 表补齐其余 `BlockingIssue.kind`

**验收**：每 operator 端到端 + conformance test 全覆盖。

**规模**：4-5 天。**依赖**：T6 + T7（部分）。可与 T7 并行：每接入一个 operator 配对它的 finding extractor。

#### T9. Surface 2 完整 + Surface 3

- `SessionKnowledge` 剩余 4 类 fact（`AttributedDriver` / `TestedHypothesis` / `ForecastSummary` / `AssociationSummary`）投影规则
- `OpenItem` 完整（`OpenAnomaly` + `OpenQuestion` 含两种 reason 来源）
- `BlockedFollowup` 计算
- `for_subject()` 过滤
- `session.evidence.findings` / `propositions` / `assessments` 查询 API
- `session.evidence.proposition` / `latest_assessment` / `trace`
- `EvidenceTrace` assembly

**验收**：跨 step 复杂场景测试；replay 后 `snapshot_id` + facts 集合不变。

**规模**：3-4 天。**依赖**：T7 + T8。Surface 2 完整与 Surface 3 可拆双 PR 并行。

### Milestone D：收尾

#### T10. SKILL.md 对齐 + walkthrough

- `marivo/skills/marivo-analysis/SKILL.md` 迁移到 `session.observe(timescope=..., dimensions=...)` canonical 写法
- 默认 surface 文档（Surface 1 字段 + `knowledge.facts/open_items/next_steps`）
- 明示 `recommended_followups` 仅 C1 + C2
- 完整 walkthrough 示例
- `references/` 下放细分 Fact schema、Surface 3 文档

**验收**：SKILL 内每个 code block 实际可执行（doctest）。

**规模**：1-2 天。**依赖**：T9。

### 依赖图

```text
T1 ── T2 ── T3
            │
            ├── T4 ── T5 ── T6  (🎯 first end-to-end demo)
            │                │
            │                ├── T7 ─┐
            │                │      ├── T9 ── T10
            │                └── T8 ─┘
            │
            └─ (T3 lock contention test 可与 T4 并行)
```

**关键并行机会**：

- T7 与 T8 可以配对并行：每接一个新 operator (T8) 同时实现它的 extractor (T7)。两人协作能把 milestone C 压缩到 5-6 天总周期
- T9 内部 Surface 2 完整与 Surface 3 可拆双 PR 并行

### 风险点（决定先后顺序的真实原因）

1. **SAVEPOINT 模型在生产 SQLite 中是否真的工作如预期** — T3 + T5 是验证点。如果有 corner case（比如 SAVEPOINT 嵌套 + `foreign_keys = ON` 的交互），整套失败语义要重做。优先级最高，所以放在竖切早期暴露
2. **id 稳定性在真实 ibis expression 上的 normalize 是否 deterministic** — T2 单测可能覆盖不到 ibis 表达式细节，T5 端到端会暴露。所以竖切必须包含真实 operator，不能只用合成 fixture
3. **C1 / C2 白名单是否真的足够 agent 使用** — 只有 milestone B 跑完之后，让 Claude Code / Codex 真正试一遍才知道。如果跑完发现 followup 永远空、agent 不知所措，可能需要 spec 回滚——所以 T10 不要等到最后才让 agent 上手；T6 完成后就该做一次 agent 真实 walkthrough
4. **跨 store recovery 在 crash 注入测试下是否真的能收口** — T3 必须做 crash injection；否则线上偶发 orphan 不可恢复

### 时间估算

| Milestone | 单人耗时 | 备注 |
| --- | --- | --- |
| A | 约 1 周 | T1-T3 串行最小耗时；T2/T3 可部分并行 |
| B | 约 1.5 周 | 端到端最容易踩问题，要留 buffer |
| C | 约 1.5 周 | 并行做能压缩 |
| D | 3 天 | |

**总计**：单人约 5-6 周；双人并行（T7/T8 + Surface 2/3 双流）约 4 周。

每个任务必须以测试（含生成规则 conformance）+ spec 引用更新为 land 闸口。

## 相关文档

- [`python-analysis-operator-design.md`](python-analysis-operator-design.md)
