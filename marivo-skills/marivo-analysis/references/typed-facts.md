# Surface 2 Typed Facts

This reference lists the fields agents may read from
`session.knowledge()` projections. These are semantic projections over
`judgment.db`, not raw evidence-engine rows.

## Shared Fact Fields

All typed facts share these fields:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `str` | Proposition-backed fact id. |
| `kind` | `str` | One of `change`, `driver`, `tested_hypothesis`, `forecast`, `association`. |
| `subject` | `Subject` | Metric and analysis-axis subject. |
| `window` | `TimeWindow \| None` | Observed or relevant window when available. |
| `status` | `pending \| validated \| refuted \| inconclusive` | Latest assessment status. |
| `confidence` | `float \| None` | Latest assessment confidence. |
| `confidence_basis` | `str` | Short basis for the confidence value. |
| `source_refs` | `list[str]` | Source artifact ids. |
| `latest_assessment_id` | `str` | Latest assessment snapshot id. |

## ChangeFact

Returned by `knowledge.facts(kind="change")`.

| Field | Type | Notes |
| --- | --- | --- |
| `direction` | `increase \| decrease \| flat \| undefined` | Direction of the measured change. |
| `magnitude` | `float \| None` | Absolute magnitude when available. |
| `comparison_window` | `TimeWindow \| None` | Baseline window when available. |
| `comparison_basis` | `str` | Basis such as current versus previous. |
| `dimension_keys` | `dict[str, str] \| None` | Segment keys for segmented facts. |

## AttributedDriver

Returned by `knowledge.facts(kind="driver")`.

| Field | Type | Notes |
| --- | --- | --- |
| `dimension` | `str` | Driver dimension. |
| `dimension_keys` | `dict[str, str \| int \| float \| bool \| None]` | Segment key values. |
| `contribution_value` | `float \| None` | Contribution amount. |
| `contribution_share` | `float \| None` | Share of total change. |
| `contribution_role` | `offsetting_factor \| primary_driver \| secondary_driver \| material_component` | Driver role. |
| `scope_change_id` | `str \| None` | Related change fact/proposition id. |

## TestedHypothesis

Returned by `knowledge.facts(kind="tested_hypothesis")`.

| Field | Type | Notes |
| --- | --- | --- |
| `hypothesis_family` | `difference \| association` | Tested hypothesis family. |
| `alternative` | `two_sided \| greater \| less` | Alternative hypothesis. |
| `method_family` | `str` | Test method family. |
| `alpha` | `float` | Significance threshold. |
| `p_value` | `float \| None` | P-value when computed. |
| `reject_null` | `bool \| None` | Null rejection result when available. |

## ForecastSummary

Returned by `knowledge.facts(kind="forecast")`.

| Field | Type | Notes |
| --- | --- | --- |
| `forecast_window` | `TimeWindow` | Forecasted window. |
| `horizon_index` | `int` | Horizon step index. |
| `forecast_kind` | `interval \| point` | Forecast output shape. |
| `prediction_interval` | `list[float] \| None` | Lower/upper interval when available. |

## AssociationSummary

Returned by `knowledge.facts(kind="association")`.

| Field | Type | Notes |
| --- | --- | --- |
| `left_subject` | `dict[str, Any]` | Left-side subject payload. |
| `right_subject` | `dict[str, Any]` | Right-side subject payload. |
| `method_family` | `str` | Association method family. |
| `coefficient` | `float \| None` | Association coefficient. |
| `lag_mode` | `single \| sweep` | Lag policy mode. |
| `lag` | `float \| None` | Selected lag for single-lag results. |
| `lag_sweep` | `LagSweepSummary \| None` | Sweep metadata when applicable. |
| `join_basis` | `str` | Join/alignment basis. |

## OpenAnomaly

Returned by `knowledge.open_items(kind="anomaly")`.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `str` | Open item id. |
| `kind` | `anomaly` | Open item kind. |
| `subject` | `Subject` | Metric and analysis-axis subject. |
| `window` | `TimeWindow \| None` | Affected window. |
| `status` | `pending \| inconclusive` | Open assessment status. |
| `confidence` | `float \| None` | Current confidence. |
| `confidence_basis` | `str` | Confidence basis. |
| `source_refs` | `list[str]` | Source artifact ids. |
| `latest_assessment_id` | `str` | Latest assessment snapshot id. |

## OpenQuestion

Returned by `knowledge.open_items(kind="question")`.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `str` | Open item id. |
| `kind` | `question` | Open item kind. |
| `subject` | `Subject` | Metric and analysis-axis subject. |
| `window` | `TimeWindow \| None` | Affected window. |
| `status` | `pending \| inconclusive` | Open assessment status. |
| `confidence` | `float \| None` | Current confidence. |
| `confidence_basis` | `str` | Confidence basis. |
| `source_refs` | `list[str]` | Source artifact ids. |
| `latest_assessment_id` | `str` | Latest assessment snapshot id. |
| `reason` | `persistent_blocking_issue \| insufficient_evidence \| needs_human_judgment` | Why the item remains open. |

## BlockedFollowup

Returned by `knowledge.blocked_followups()`.

| Field | Type | Notes |
| --- | --- | --- |
| `action_id` | `str` | Followup action id. |
| `operator` | `str \| None` | Target operator when applicable. |
| `source_artifact_id` | `str` | Artifact that produced the followup. |
| `reason` | `missing_input_artifact \| blocking_issue_unresolved \| downstream_of_unavailable_evidence` | Why the followup is blocked. |
| `blocking_issue_kind` | `str \| None` | Related blocking issue kind when present. |
