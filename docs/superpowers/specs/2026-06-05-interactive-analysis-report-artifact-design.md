# Interactive Analysis Report Artifact - 总体设计

Date: 2026-06-05

Status: draft design proposal, pending written-spec review.

## 1. 背景与目标

Marivo 的核心使用场景之一是 agent 通过 `marivo.semantic` 和
`marivo.analysis` 完成数据分析，并向用户交付最终分析报告。最终报告不是
执行日志的补充，而是用户判断分析质量、采纳结论、继续追问和复核证据的主
入口。

当前已有三类相关设计或参考：

- `marivo-skills/marivo-analysis/references/final-report.md` 已要求最终报
  告结论优先、说明范围口径、呈现核心发现、风险和下一步。
- `docs/specs/analysis/python-track-evidence-surface.md` 已定义 result-bound
  evidence、session knowledge 和 audit surface，强调 `frame.meta`、稳定
  `artifact_id`、`lineage`、`source_refs`、`evidence_status`。
- `docs/superpowers/specs/2026-06-01-semantic-report-publishing-design.md` 曾
  定义 HTML-first `analysis_report` package，包括 `index.html`、
  `flow.json`、`grounding.json`、`replay.py`、`semantic-embed/` 和
  publish validation。本文只把它作为背景参考，不要求新设计服从旧 package
  shape。

本设计的目标是把这些能力收敛成一个更明确的目标态：**Marivo 自有的交互
式分析报告 artifact 合约**。该合约以结构化数据、证据链、claim grounding
和 report spec 为核心，可以适配 Codex Data Analytics MCP artifact app，
也可以渲染成 standalone HTML。

### 1.1 与既有 `analysis_report` package 的关系

`MarivoReportArtifact` 是新的目标态报告 package 合约。它**替代**旧
`analysis_report` package shape 作为后续实现、验证和发布的设计对象，而不是
在旧 shape 旁边增加一个并行 package。

旧 `analysis_report` 设计中的 `index.html`、`flow.json`、`grounding.json`、
`replay.py`、`semantic-embed/`、secret exclusion、content hash、publish
lifecycle 等机制可以作为参考素材；但当旧设计和本文冲突时，以本文为准。
未来的 report publishing helper，例如旧设计中设想的
`mv.publish.report_package(...)` 或新的等价 API，目标应是接收并发布
`MarivoReportArtifact`，而不是要求 agent 同时满足旧 HTML-first package 和
新 artifact package 两套结构。

## 2. 用户体验目标

报告应满足以下体验：

1. 最重要的结论在最前面，用户不需要先读过程才能知道答案。
2. 中间是分析步骤：每一步做了什么、使用了什么语义对象 / intent / SQL /
   script / frame、得到了什么结果。
3. 每个主结论都能追溯到支撑它的数据和步骤。
4. 正确性风险、数据质量问题、partial evidence、口径假设和无法证明的部分
   必须可见。
5. 下一步建议动作要和证据边界匹配，区分业务动作、继续分析、数据质量修复
   和监控项。
6. 数据以 chart、KPI、compact table、candidate review、driver view 等
   用户友好的形式展示，不以裸 `head()` / `summary()` / 大表格作为最终交付。
7. 分析步骤之间通过链接串联。用户能从结论跳到步骤，从步骤跳到 artifact，
   从 artifact 跳到 SQL、semantic object、frame snapshot 或 replay script。

### 2.1 Reader-facing composition model

The report is narrative-first and artifact-backed. Users should read a clear
answer, interpretation, caveats, and next action first; structured components
support, verify, and make the narrative interactive.

Reader-facing reports have three layers:

| Layer | Owner | Visible role |
| --- | --- | --- |
| Narrative layer | Agent / skill | Title, executive summary, finding takeaways, chart interpretation, step explanations, caveats, and recommended actions. This layer explains what the evidence means and why it matters. |
| Evidence presentation layer | Artifact data + renderer | KPI strips, charts, compact tables, candidate reviews, driver views, and step traces. This layer presents the data that supports the narrative. |
| Audit layer | Marivo library + artifact package | Grounding refs, source provenance, semantic refs, frame refs, SQL or intent details, scripts, replay metadata, and evidence status. This layer is available through links, drawers, or expanded panels by default, not as the main reading path. |

Agent-authored readable prose belongs in the narrative layer. It should be
concise, conclusion-first, and adjacent to the relevant visual or table. The
agent should write:

- the report title and executive-summary bullets
- each finding's takeaway and interpretation
- how to read each important chart or table
- why an analysis step was run and what it changed
- caveats that affect the conclusion
- recommended next actions and monitoring points
- human-readable source summaries when implementation details would otherwise
  be hard to interpret

Structured display belongs in the evidence and audit layers. KPI cards, charts,
tables, claim-evidence panels, source drawers, and step traces should provide
the numbers, identifiers, links, and provenance that make the prose verifiable.
They should not replace the narrative or force users to infer the conclusion
from raw data.

## 3. 调研结论

### 3.1 Marivo 现有设计

Marivo 已经具备报告 artifact 的底座：

- Python analysis operator 以 canonical artifact 为中心，适合形成 step DAG。
- Evidence surface 规定了 `artifact_id`、`subject`、`source_refs`、
  `lineage`、`confidence_scope`、`quality`、`blocking_issues`、
  `recommended_followups`、`evidence_status`。
- `explore_ibis` / pandas scratch / promotion 已经有 provenance 边界：scratch
  默认不能进入 canonical 链路，只有 promotion 后才能成为 typed frame。
- 旧 publishing design 已要求 package 包含 HTML、flow、grounding、replay、
  semantic embed，并校验 partial evidence 可见。这些是可复用机制，不是本文
  必须继承的 package shape。

缺口是：已有设计没有把“交互式报告数据层”定义为一等合约。`index.html` 是
主要读者体验，但 artifact 数据、chart spec、claim graph、step link 和
renderer adapter 还没有统一结构。

### 3.2 Codex Data Analytics MCP artifact 可借鉴点

Codex Data Analytics 插件的 MCP report/dashboard 机制提供了一个成熟参考：

- artifact 使用 `manifest` 描述 reader-facing blocks、cards、charts、tables
  和 sources。
- 使用 bounded `snapshot.datasets` 提供已审核、紧凑、聚合后的数据。
- 报告使用 `blocks[]` 形成阅读路径，dashboard 使用 blocks/cards/charts/tables
  形成可扫描布局。
- 每个 chart/table 都有 canonical `source`，包含 SQL/code、query summary、
  tables used、filters、metric definitions。
- 渲染前先 `validate_artifact`，通过后再 `render_artifact`。
- Hosted export 不是重写 HTML，而是把 validated artifact payload 和真实 MCP
  artifact runtime 打包。

Marivo 应借鉴这些机制，但不应把自己的报告合约压扁成 Data Analytics 插件
schema。Marivo 的核心差异是它必须保留 semantic lineage、intent lineage、
frame lineage、scratch promotion 和 evidence/judgment 信息。

### 3.3 当前设计漂移风险

`docs/superpowers/specs/2026-06-03-analysis-replay-script-design.md` 提到
`docs/specs/analysis/2026-06-03-executed-sql-audit-trail-design.md`，但当前
checkout 未找到该文件。后续实现前应确认 executed SQL audit trail 的真实
文档位置或重新补齐该设计，避免 replay / report provenance 对不存在的输入
形成依赖。

## 4. 核心设计原则

### P1：报告 artifact 先于 renderer

Marivo 的报告能力应以 `MarivoReportArtifact` 为核心，而不是以某个 HTML 或
MCP renderer 为核心。HTML、MCP app、PDF、Slides、notebook appendix 都是
adapter。

### P2：报告数据和展示分离

Artifact 提供数据、证据、来源、claim graph 和 chart/table specs。Renderer
只负责展示、交互、链接、排序、筛选、展开和导出，不重新发明分析逻辑。

所有 reader-facing 数字都必须来自 artifact 数据层。`report_spec.json`、
`grounding.json`、HTML 和 MCP adapter 可以引用 dataset cell、artifact
measure 或 evidence field，但不能把 KPI、chart label、table value 或正文里
的关键数字作为第二份真相重写一遍。

### P3：结论可以排在最前，证据不能断在后面

报告阅读顺序是 answer-first；证据结构是 graph-first。每个主结论都必须能
反向解析到支撑它的 step、artifact、frame、source query、semantic object
或明确的 commentary 标记。

### P4：HTML 可以交互，但不能重算主结论

HTML / MCP UI 可以做展示层排序、分页、tooltip、局部筛选、chart view 切换
和展开详情。主结论、driver 排名、异常分类、质量判断、推荐动作的证据基础
应来自 Marivo analysis step 或 agent-authored grounded claim，不应由前端
临场重算。

### P5：partial evidence 是一等状态

如果 evidence 是 `partial` 或 `unavailable`，报告 body、manifest 和对应
claim 都必须可见地表达这一点。不能只把风险藏在 source metadata。

### P6：默认不打包 row-level 数据

报告 artifact 默认只包含聚合数据、bounded top-N、候选明细、chart-ready
dataset 和必要 audit table。Row-level frame snapshot 必须显式 opt in，并
记录 data policy。

## 5. 总体架构

```text
Marivo analysis session
  -> canonical frames / result artifacts / exploration results
  -> evidence surface / judgment DB / semantic refs
  -> report assembly by agent + marivo-analysis skill
  -> MarivoReportArtifact
       manifest.json
       report_spec.json
       flow.json
       grounding.json
       datasets/
       evidence/
       semantic-embed/
       frames/                 optional
       replay.py
       assets/                 optional rendered charts
       adapters/               optional materialized renderer payloads
  -> adapters
       Data Analytics MCP artifact manifest + snapshot
       standalone index.html
       publish package
       future PDF / Slides / notebook appendix
```

### 5.1 Artifact 提供数据，HTML 提供交互展示

目标运行模型是：**artifact 是可信数据和证据提供者，HTML 是读者体验层**。

在 standalone package 中，HTML 直接读取 package-local JSON：

```text
index.html
  -> manifest.json
  -> report_spec.json
  -> datasets/*.json
  -> grounding.json
  -> flow.json
  -> evidence/*.json
```

在 hosted 或 MCP app export 中，可以通过只读 API 暴露同一份 payload：

```text
/api/manifest
/api/report-spec
/api/snapshot
/api/grounding
/api/flow
/api/source-file
```

无论是本地文件读取还是 hosted API，HTML 都不直接连接 datasource，不读取
Marivo session store，不调用 live SQL，不生成新的 analysis step。这样可以让
用户获得交互体验，同时保证报告数字、证据和口径来自同一个 frozen package。

## 6. `MarivoReportArtifact` 内容

### 6.1 `manifest.json`

Manifest 是 package-level 元数据和 validation 入口。

建议字段：

```json
{
  "kind": "marivo_analysis_report",
  "manifest_version": 1,
  "report_id": "cdn_drop_review",
  "export_id": "exp_20260605_120000",
  "title": "CDN 5分钟带宽骤降复核报告",
  "created_at": "2026-06-05T12:00:00Z",
  "marivo_version": "...",
  "entrypoints": {
    "html": "index.html"
  },
  "adapters": {
    "mcp": {
      "materialized": false,
      "target_schema": null,
      "manifest": null,
      "snapshot": null
    }
  },
  "analysis": {
    "flow": "flow.json",
    "grounding": "grounding.json",
    "report_spec": "report_spec.json",
    "evidence_root": "evidence/",
    "dataset_root": "datasets/",
    "artifact_count": 12
  },
  "semantic_embed": {
    "included": true,
    "root": "semantic-embed/",
    "hash": "sha256:..."
  },
  "replay_script": {
    "path": "replay.py",
    "generated_by": "skill",
    "validation": "static_checked",
    "input_mode": "live_datasource"
  },
  "data_policy": {
    "row_level_data": "omitted",
    "frame_snapshots": "omitted",
    "authority": "manifest_default"
  },
  "evidence_status": "complete"
}
```

`manifest_version` governs the schema for all package child files in this v1
contract, including `report_spec.json`, `flow.json`, `grounding.json`,
`datasets/`, and `evidence/`. Child files do not need separate schema versions
unless a future revision allows independent evolution.

### 6.2 `report_spec.json`

`report_spec.json` 是 reader-facing 报告结构。它不保存大数据，只保存 sections、
blocks、visual specs、step links 和 source refs。

`report_spec.json` 不能保存 KPI 或正文关键数字的 literal value。Metric strip、
chart、table、inline numeric callout 都应通过 `dataset_id`、row selector 和
field path 引用 `datasets/` 中的一个单元格，或引用已持久化 artifact/evidence
field。这样同一个数字只有一个 source of truth。带数字的可读文案应使用
template + value refs，例如 `text_template: "{province} bandwidth dropped by
{drop_pct}"`，而不是持久化已经渲染好的数字句子。

`report_spec.json` should preserve a top-to-bottom reading path. Narrative
blocks carry the user-facing story; visual/table blocks carry supporting
evidence; source and trace blocks are audit affordances and should be collapsed,
linked, or placed after the relevant narrative unless the user explicitly asks
for a methods-first report.

建议 section 类型：

| Section | 用途 |
| --- | --- |
| `executive_summary` | 结论优先，2-4 个主结论 |
| `scope` | 指标、时间窗口、grain、filter、baseline、口径 |
| `finding` | 核心发现，每个 finding 绑定 claim refs 和 evidence refs |
| `analysis_step` | 可读步骤说明和跳转，解释为什么做这一步、得到了什么 |
| `candidate_review` | 异常、driver、segment、rank 候选明细 |
| `caveat` | 风险、假设、partial evidence、质量问题 |
| `next_step` | 下一步建议 |
| `source_detail` | source / semantic / SQL / script / frame 信息；默认作为 audit detail |

建议 block 类型：

| Block | 说明 |
| --- | --- |
| `markdown` | 叙事段落、标题、解释 |
| `metric_strip` | headline KPI；每个 metric 引用 dataset cell，不内联数值，并由相邻叙事解释含义 |
| `chart` | 绑定 `datasets/<id>.json` 的 chart spec；重要 chart 必须有相邻解释 |
| `table` | compact evidence table 或 audit table；候选明细优先 compact，宽表默认 lower/detail |
| `step_trace` | 展示 step -> artifact -> source 链路；默认用于展开复核，不替代步骤叙事 |
| `claim_evidence` | 展示 claim grounding；默认贴近主结论或 finding，可折叠展示细节 |
| `source_code` | 安全范围内展示 SQL / script 摘要或链接；默认作为 source drawer / audit detail |

### 6.3 `flow.json`

`flow.json` 是分析步骤链路，不等同于用户可读报告。

每个 step 至少包含：

- `step_id`
- `order`
- `kind`: `intent` / `explore_ibis` / `pandas_scratch` / `promotion` /
  `transform` / `quality_assessment` / `agent_decision`
- `description`
- `input_artifacts`
- `output_artifacts`
- `semantic_refs`
- `source_queries`
- `script_ref`
- `evidence_status`
- `links`: previous / next / parent / derived_from

Step provenance is required by step kind, but v1 validation treats missing
step-kind execution provenance as a warning rather than a package failure.
Broken references from claims, blocks, or datasets to a missing step/artifact
still fail validation.

Minimum step-kind provenance:

| Step kind | Minimum provenance |
| --- | --- |
| `intent` | intent name, semantic refs, datasource refs, timescope, grain, filters, dimensions, parameters/policies, planner or Marivo version, result frame ref, query summary |
| `explore_ibis` / SQL-backed exploration | datasource refs, source query metadata, SQL status, result ref, query summary |
| `pandas_scratch` | source frame refs, script ref, transform summary, output ref |
| `promotion` | source scratch refs, semantic anchors, promotion policy, validation result, output artifact |
| `transform` | input artifact refs, op, params, output artifact refs |
| `agent_decision` | inspected artifacts, decision literal, rationale, downstream refs |

`source_queries` may carry SQL when available, but typed intents do not require
SQL to be evidence-backed. For no-SQL intent paths, logical intent provenance is
the primary source contract and SQL status should be `not_applicable`,
`unavailable`, or `redacted` as appropriate.

### 6.4 `grounding.json`

`grounding.json` 负责把报告里的 claim 和证据绑定。

Claim 分类：

| Type | 说明 |
| --- | --- |
| `evidence_backed` | 由 artifact / finding / assessment / frame / query 支撑 |
| `derived_from_flow` | 由分析流程、参数、lineage 或 promotion path 支撑 |
| `commentary` | agent 的解释、建议或业务判断，不伪装成数据事实 |

`grounding_type` 表达 claim 的支撑来源类别；`evidence_status` 表达这些支撑
证据的完整性。两者是正交字段：一个 claim 可以是 `evidence_backed`，同时因
部分 assessment 或 source query 缺失而带有 `partial` evidence status。

每个 claim 至少包含：

- `claim_id`
- `text_template`
- `value_refs`
- `section_id`
- `grounding_type`
- `supporting_artifacts`
- `supporting_steps`
- `supporting_datasets`
- `source_refs`
- `risk_refs`
- `confidence_scope`

Validation 规则：报告前部的主结论不能是 ungrounded；没有数据支撑的业务建议
必须标成 `commentary`，并在相邻段落说明依据边界。

`grounding.json` covers main claims and referenced numeric callouts, not every
numeric statement in the report. Required grounding targets include executive
summary bullets, section-level finding takeaways, recommendation claims,
material caveats, standalone KPI/numeric callouts, and numeric statements that
are not adjacent to the chart/table they explain. Chart axis ticks, table cells,
source metadata, replay status numbers, and ordinary explanatory numbers next to
their visual/table do not need separate claim entries, but they still follow the
single-source rule: rendered numbers reference datasets or artifact/evidence
fields instead of being restated inline. Claim text that includes values is
rendered from `text_template` and `value_refs`; validators should reject literal
numeric claim text when those numbers are meant to be reader-facing facts.

### 6.5 `datasets/`

`datasets/` 是 chart/table/KPI 使用的数据层。默认使用紧凑 JSON，每个 dataset
是 plain row array，而不是 `{columns, rows}` 包装。列元数据放在
`report_spec.json` 或 adapter manifest 中。

Manifest-level `data_policy` 是 package 的 authoritative default。Dataset
metadata 可以声明更严格的局部策略，例如某个 dataset 被 truncated 或只包含
aggregate rows；但不能放宽 manifest 的 row-level / frame-snapshot policy。
如果 dataset policy 与 manifest policy 冲突，validation 必须失败。

每个 dataset 应有 dataset metadata：

- `dataset_id` — auto-derived from `Dataset.dataset_id`; optional in metadata
- `grain` — default `"overall"`
- `row_count` — auto-derived from `len(rows)`; optional in metadata
- `truncated` — default `False`
- `columns` — column key names derived from first row keys; default `()`
- `source_artifacts`
- `source_provenance` — default `SourceProvenance()` with `generated_from="intent"`, `query_summary=""`
- `metric_definitions`
- `filters`
- `data_policy`

`Dataset.from_rows(dataset_id=..., rows=...)` provides a convenience factory
that auto-derives `dataset_id`, `row_count`, and `columns`.

Dataset source metadata must be appropriate to `generated_from.step_kind`.
Typed intents do not require SQL; the minimum contract is logical intent
provenance plus a human-readable query summary. If SQL exists, include it. If
SQL does not exist or cannot be safely exposed, record `sql_status` and expose
the logical intent request instead of fabricating SQL. SQL/script availability
is status-driven: `sql_status="available"` requires SQL text;
`not_applicable`, `unavailable`, or `redacted` requires a reason and logical
provenance. A pandas-produced dataset requires `script_ref`; a promoted dataset
requires promotion metadata.

Chart dataset 应保留比可见 encoding 更丰富但仍安全的上下文，例如 numerator、
denominator、baseline、rank、segment、bucket、source refs。这样用户展开图表
时能复核，而不是只能看到被画出来的两列。

KPI dataset 也遵循同一规则。Metric strip 的 headline value、secondary badge、
delta 和 percent change 都必须引用 dataset cell；`grounding.json` 引用该
dataset cell 或其 source artifact，不重新保存数值。

### 6.6 `evidence/`

`evidence/` 保存 evidence chain 的 package-local 投影。它不必复制完整
judgment DB，但必须足以让 `grounding.json` 中的 evidence refs 解析。

建议包含：

- artifact summaries
- findings
- propositions / assessments 的摘要
- blocking issues
- quality summaries
- source provenance metadata
- promotion metadata

### 6.7 `semantic-embed/`

报告需要嵌入用到的 semantic object 源文件或只读 projection。嵌入规则应沿用
semantic release 的 loader-based copy 规则，不能发明固定
`datasets.py/fields.py/metrics.py/relationships.py` 布局。

### 6.8 `replay.py`

`replay.py` 仍按现有 replay 设计作为 live datasource reproducibility recipe。
它不是报告阅读路径，不负责重写 prose，也不负责重建 evidence。Manifest 必须
说明它是 `static_checked`、`executed` 还是 `not_run`。

## 7. Data Analytics MCP artifact adapter

Marivo 可以提供一个 adapter，把 `MarivoReportArtifact` 转成 Data Analytics
MCP artifact app 能渲染的 payload：

```text
MarivoReportArtifact
  -> manifest: { title, blocks, cards, charts, tables, sources, version: 1 }
  -> snapshot: { version: 1, status, datasets }
  -> package_info / sources
  -> validate_artifact
  -> render_artifact
```

映射原则：

| Marivo | Data Analytics MCP artifact |
| --- | --- |
| `report_spec.sections[].blocks` | `manifest.blocks[]` |
| `datasets/<id>.json` | `snapshot.datasets[dataset_id]` |
| chart/table specs | `manifest.charts[]` / `manifest.tables[]` |
| source provenance metadata | block `source` or `manifest.sources[]` |
| headline KPI | `cards[]` + `metric-strip` block |
| `evidence_status` | `snapshot.status` + visible caveat block |
| grounding refs | source metadata / supporting hidden detail blocks |

Adapter 不应丢失 Marivo 专有 lineage。MCP payload 可以展示压缩后的 source，
但 package 仍必须保留完整 `grounding.json`、`flow.json`、`evidence/` 和
semantic refs。

MCP adapter output 默认是按需生成的 in-memory payload，不是 core package 的
必需文件。只有在需要发布、复查或离线复用 MCP payload 时，adapter 才把结果
物化到 `adapters/mcp/manifest.json` 和 `adapters/mcp/snapshot.json`，并更新
`manifest.adapter_mcp`。因此 core manifest 不再声明不存在的
`entrypoints.mcp`。When materialized, `manifest.adapter_mcp.target_schema`
must record the target Data Analytics MCP artifact schema or plugin version used
by the adapter tests.

## 8. Standalone HTML adapter

Standalone HTML adapter 面向离线打开、S3 发布或非 Codex 环境。它消费同一个
`MarivoReportArtifact`，生成 `index.html` 和必要 assets。

规则：

- HTML 初屏显示标题和结论摘要。
- 每个结论旁边提供 evidence links 或 expandable proof panel。
- 分析步骤作为可折叠 timeline / step list，支持跳到对应 chart/table/source。
- Chart 可以使用轻量前端库或静态图片；关键是数据来自 `datasets/`，不是内联
  手写数字。
- HTML may support bounded interaction: section navigation, anchor links,
  collapsible step timeline, evidence/source drawers, chart tooltips, table
  sorting/pagination, local search, and predeclared filters over existing
  bounded datasets.
- Predeclared filters may update multiple charts/tables, but they may only
  subset or switch already-packaged datasets/views. They must not create new
  aggregations, run live queries, change executive-summary claims, or recompute
  main findings.
- Aggregate KPI or chart values under a filter must come from precomputed
  filtered datasets/views. The frontend may subset display rows, but it must not
  derive new totals, rates, rankings, or headline values from hidden rows.
- HTML 可以展示 SQL/script 摘要，但不得暴露 secrets、credentials 或未审核
  的大规模原始数据。
- HTML 必须可在不导入 Marivo、不访问 S3 API、不连接 datasource 的情况下打开。

Use MCP artifact or BI dashboard instead of standalone HTML when the user needs
free exploration, arbitrary filters or dimensions, live or scheduled refresh,
multi-user governance, chart re-encoding, or interaction that would change the
main claims.

## 9. 职责边界

### 9.1 Marivo Python library 负责

- 产出和持久化 canonical analysis artifacts。
- 维护 result meta / evidence surface / session knowledge / trace。
- 暴露 artifact export APIs，生成 package-local artifact/evidence/dataset
  projections。
- 校验 `MarivoReportArtifact`：manifest、flow、grounding、datasets、semantic
  embed、data policy、partial evidence visibility、replay validation state。
- 提供 adapters 的 deterministic helpers，但不生成业务 prose。

### 9.2 `marivo-analysis` skill 负责

- 规定 close-out workflow：何时生成 report artifact，如何选择 sections，
  如何检查 result meta，如何处理 caveats。
- 规定 narrative quality：结论优先、图表旁必须有解释、source/audit 细节默认
  不淹没正文。
- 规定 claim grounding 规则和 report QA checklist。
- 规定可视化选择原则和反模式。
- 指导 agent 组装 `report_spec.json`、`grounding.json`、`replay.py`。
- 调用 Marivo validation / MCP adapter / HTML adapter。

### 9.3 Agent 负责

- 选择分析路径并执行 intent、Ibis、pandas、promotion。
- 读取中间结果并决定下一步。
- 形成 claim、可读解释、风险和建议；把结构化 evidence 转成用户能判断的叙事。
- 将每个主结论绑定到 evidence；无法绑定时降级为 commentary 或删除。
- 选择最适合用户交付的 surface：MCP app、standalone HTML、published package
  或轻量 Markdown。

### 9.4 Renderer 负责

- 展示报告结构、图表、表格、链接、筛选、排序、展开。
- 保持阅读路径和交互体验。
- 支持 audit detail 的链接、drawer、折叠和展开，而不是把 provenance 全部铺在
  主阅读流里。
- 不重算主结论，不伪造 provenance，不隐藏 partial evidence。

## 10. 生成流程

```text
1. Agent 执行分析
   - built-in intents first
   - explore_ibis / pandas scratch only when needed
   - promotion when scratch result must re-enter canonical chain

2. Agent close-out
   - inspect result.meta.evidence_status / blocking_issues / quality
   - read session.knowledge() when cross-step reasoning is needed
   - curate final flow
   - define claims and caveats

3. Skill-guided report assembly
   - write report_spec.json
   - write grounding.json
   - export compact datasets
   - export evidence summaries
   - embed semantic refs
   - assemble replay.py when required

4. Library validation
   - validate package structure
   - validate grounding refs resolve
   - validate chart/table datasets exist
   - validate data policy
   - validate partial evidence visibility
   - validate replay state

5. Render / publish
   - MCP artifact adapter for in-Codex report
   - HTML adapter for standalone package
   - optional publish helper for hosted storage
```

## 11. Validation contract

Validation must fail on:

- Missing `report_spec.json`, `flow.json`, `grounding.json`, or required datasets.
- A main executive-summary claim without grounding.
- A chart/table block referencing a missing dataset.
- A metric strip, chart label, table cell, or body numeric callout that stores a
  reader-facing number inline instead of resolving to a dataset cell or
  artifact/evidence field.
- A dataset that lacks source provenance appropriate to `generated_from.step_kind`.
- A source declares `sql_status="available"` without SQL text, or declares
  `not_applicable`, `unavailable`, or `redacted` without logical provenance and
  a reason.
- A pandas-produced dataset without `script_ref`, or a promoted dataset without
  promotion metadata.
- `evidence_status != complete` without visible caveat / risk section.
- `row_level_data = omitted` but package includes row-level frame snapshots.
- Replay script marked `executed` without execution evidence.
- Semantic embed missing for semantic refs used by claims.
- HTML or MCP adapter omits required report sections.
- A quantitative finding has a chart/table/KPI without adjacent narrative
  interpretation, unless the report is explicitly methods-first.
- Source/provenance/audit details dominate the main reading path before the
  executive summary or finding narrative.

Validation should warn, not fail, on:

- Optional source limitations recorded as caveats.
- Chart dataset intentionally minimal due to privacy or cost, when documented.
- Missing step-kind execution provenance in v1 warning mode.
- Commentary claims that are clearly marked and do not pretend to be data-backed.
- Minor visuals or lookup tables without adjacent prose when they are clearly
  subordinate to a nearby interpreted finding.

## 12. Phased implementation proposal

### Phase 1：Spec + skill hardening

- Add this design as the target-state document.
- Update `marivo-skills/marivo-analysis/references/final-report.md` to mention
  report artifact / grounding / chart dataset expectations.
- Add examples for claim grounding and step links.

### Phase 2：Library package schema and validation

- Extend the existing `marivo.analysis.publish` package, which currently exposes
  `replay_check.py`, or add a sibling `marivo.analysis.report` package if the
  report schema becomes large enough to deserve a separate namespace. Do not
  create a second package shape beside `MarivoReportArtifact`.
- Implement Pydantic models, builder APIs, JSON Schema export, and validation for
  manifest, report_spec, flow, grounding, datasets, semantic embed and evidence
  status visibility.
- Add tests with a small multi-step analysis fixture.

### Phase 3：MCP artifact adapter

- Implement adapter from Marivo report artifact to Data Analytics MCP manifest
  and snapshot shape as a Marivo-owned optional extra, such as
  `marivo[mcp-report]`.
- Validate via the MCP `validate_artifact` contract where available.
- Record the target MCP/Data Analytics artifact schema or plugin version in
  adapter metadata and adapter tests.
- Preserve Marivo-specific lineage in package metadata and source detail blocks.

### Phase 4：Standalone HTML adapter

- Generate `index.html` from the same artifact.
- Provide interactive step timeline, claim evidence panels, chart/table rendering
  and source detail drawers.
- Ensure HTML opens offline and does not require Marivo runtime.

### Phase 5：Publish integration

- Define or update the report publishing helper so it accepts
  `MarivoReportArtifact` as the package contract.
- Support S3 or other publish targets while preserving content hash, user path
  validation and secret exclusion.

## 13. Affected files

Likely affected areas once implementation starts:

- `docs/superpowers/specs/2026-06-01-semantic-report-publishing-design.md`
  for archival cross-reference, replacement notes, or migration cleanup.
- `docs/superpowers/specs/2026-06-03-analysis-replay-script-design.md`
  for executed-SQL audit trail reference cleanup.
- `docs/specs/analysis/python-track-evidence-surface.md`
  for report artifact projection requirements if needed.
- `marivo-skills/marivo-analysis/SKILL.md`
  and `marivo-skills/marivo-analysis/references/final-report.md`
  for close-out guidance.
- Existing `marivo/analysis/publish/` package, especially as the home for
  deterministic report package validation alongside the current replay checker;
  `marivo/analysis/report/` remains an option only if schema/model code should
  be separated from publishing helpers.
- Tests for report package validation and adapter mapping.

## 14. Acceptance criteria

- A non-trivial Marivo analysis can produce a report artifact where every
  executive-summary claim resolves to `grounding.json`.
- Every rendered number, including KPI strips and numeric callouts, resolves to
  a bounded dataset cell or artifact/evidence field with source metadata.
- The report includes visible conclusion, scope, step chain, findings, risks,
  next steps and source details.
- Important visuals and tables have adjacent narrative interpretation, and audit
  details are accessible without crowding out the main reading path.
- `partial` / `unavailable` evidence is visible in both manifest and rendered
  report.
- The same `MarivoReportArtifact` can render through at least one MCP artifact
  adapter and one standalone HTML adapter without changing the analysis data.
- Validation rejects ungrounded main claims, missing datasets, broken step refs,
  policy-inconsistent frame snapshots and hidden partial evidence.
- The Marivo library does not generate final business prose; the skill/agent owns
  narrative and claim assembly.

## 15. Resolved design decisions

1. Schema: `MarivoReportArtifact` uses Pydantic models as the Python authoring
   and validation surface, exports JSON Schema for package and adapter
   validation, and writes canonical JSON files as the durable artifact. Agents
   use builder APIs rather than authoring raw JSON. Pydantic stays in the
   report/publish layer and does not spread into analysis core.
2. MCP adapter location: MCP support is a Marivo-owned optional extra, such as
   `marivo[mcp-report]`, implemented under the report adapter namespace. Core
   report packages do not depend on Codex or MCP schemas, but adapter mapping is
   tested in the Marivo repo rather than living as skill-only script logic.
3. Typed intent source metadata: SQL is optional. Minimum provenance is the
   logical intent request, semantic refs, datasource refs, timescope, grain,
   filters, dimensions, params/policies, planner or Marivo version, result frame
   ref, and query summary. No-SQL typed intents can still support
   `evidence_backed` claims.
4. Execution provenance: replace the old "executed SQL audit trail" framing with
   step-kind execution provenance. v1 validation warns on missing step-kind
   provenance and fails only on broken refs, hidden partial evidence, policy
   conflicts, or unsupported evidence-backed claims.
5. Grounding scope: `grounding.json` covers main claims and referenced numeric
   callouts, not every numeric statement. The single-source numeric rule remains
   universal: rendered numbers reference datasets or artifact/evidence fields.
6. Standalone HTML interaction: HTML is a bounded interactive report. It may use
   predeclared filters over packaged bounded datasets and precomputed views, but
   it must not run live queries, create new aggregations, mutate main claims, or
   become a dashboard/BI replacement.

## 16. References consulted

- `marivo-skills/marivo-analysis/references/final-report.md`
- `docs/specs/analysis/python-track-evidence-surface.md`
- `docs/specs/analysis/python-analysis-operator-design.md`
- `docs/superpowers/specs/2026-06-01-semantic-report-publishing-design.md`
- `docs/superpowers/specs/2026-06-03-analysis-replay-script-design.md`
- Data Analytics plugin `skills/build-report/specifications/mcp-app-report.md`
- Data Analytics plugin `skills/build-dashboard/specifications/mcp-artifact-dashboard.md`
- Data Analytics plugin `src/analytics-app-core.md`
