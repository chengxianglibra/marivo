"""First-round Analysis DSL native Help bindings owned by the Session surface."""

from __future__ import annotations

from inspect import Parameter, getdoc, isfunction, signature

import marivo.analysis._cohort as cohort
from marivo.analysis import anchors as windows
from marivo.analysis import public_dsl as dsl
from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    ExampleInput,
    ExportInput,
    ParameterInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis._subject import SubjectBinding
from marivo.analysis.materialization import graph_fields as fields
from marivo.introspection.live.reflect import required_arguments

_METHOD_GROUPS = {
    ("_MaterializedRead", "show"): "artifacts.reads",
    ("_MaterializedRead", "to_pandas"): "artifacts.reads",
    ("_MaterializedRead", "evidence_digest"): "artifacts.reads",
    ("_MaterializedRead", "findings"): "artifacts.reads",
    ("_MaterializedRead", "finding"): "artifacts.reads",
    ("_OriginalContinuation", "group_by"): "methods.metric.reduce",
    ("_OriginalContinuation", "rollup"): "methods.metric.reduce",
    ("_OriginalContinuation", "summarize"): "methods.metric.summary",
    ("_NumericComparison", "rank"): "methods.rows",
    ("_NumericComparison", "runs"): "methods.rows",
    ("_NumericComparison", "deviation"): "methods.rows",
    ("_NumericComparison", "correlate"): "methods.association",
    ("_NumericComparison", "forecast"): "methods.forecast",
    ("_AnchorDomain", "retention"): "methods.events",
    ("_InstanceRetention", "by_subject"): "methods.events",
    ("_Retention", "known_true"): "methods.events",
    ("_Retention", "known_false"): "methods.events",
    ("_Retention", "unknown"): "methods.events",
    ("_AnchorDomain", "observe"): "methods.events",
    ("_AnchorDomain", "subjects"): "methods.events",
    ("_Journey", "funnel"): "methods.events",
    ("_Funnel", "read"): "methods.events",
    ("_FunnelResult", "compare"): "methods.compare",
    ("_FunnelComparison", "attribute"): "methods.compare",
    ("_Attribution", "where"): "methods.compare",
    ("LogicalDifferenceRelation", "attribute"): "methods.compare",
    ("MaterializedDifferenceRelation", "attribute"): "methods.compare",
    ("_Ranking", "where"): "methods.rows",
    ("_Ranking", "limit"): "methods.rows",
    ("_NumericComparison", "compare"): "methods.compare",
    ("_NumericComparison", "ratio"): "methods.compare",
    ("_NumericComparison", "share_of"): "methods.metric.reference",
    ("_NumericComparison", "standardize"): "methods.metric.reference",
    ("_CohortDomain", "penetration_in"): "methods.metric.reference",
    ("GroupedRatioRelation", "rollup"): "methods.metric",
    ("_CohortDomain", "cohort"): "methods.rows",
    ("LogicalAnalysisDomain", "each"): "methods.metric",
    ("LogicalAnalysisDomain", "read"): "inputs.population",
    ("LogicalAnalysisDomain", "group_by"): "methods.metric",
    ("LogicalAnalysisDomain", "observe"): "methods.metric",
    ("LogicalNumericRelation", "group_by"): "methods.metric",
    ("LogicalNumericRelation", "rollup"): "methods.metric",
    ("LogicalNumericRelation", "summarize"): "methods.metric.summary",
    ("LogicalNumericRelation", "correlate"): "methods.association",
    ("MaterializedNumericRelation", "summarize"): "methods.metric.summary",
    ("MaterializedNumericRelation", "group_by"): "methods.metric.reduce",
    ("MaterializedNumericRelation", "rollup"): "methods.metric.reduce",
    ("MaterializedNumericRelation", "correlate"): "methods.association",
    ("MaterializedNumericRelation", "where"): "methods.rows",
    ("LogicalRatioRelation", "group_by"): "methods.metric",
    ("LogicalRatioRelation", "rollup"): "methods.metric",
    ("LogicalRatioRelation", "summarize"): "methods.metric.summary",
    ("MaterializedRatioRelation", "rollup"): "methods.metric",
    ("LogicalCategoryRelation", "where"): "methods.rows",
    ("LogicalDifferenceRelation", "where"): "methods.rows",
    ("LogicalDifferenceRelation", "summarize"): "methods.compare",
    ("MaterializedSelectedDifferenceRelation", "members"): "methods.rows",
    ("MaterializedCoefficientRelation", "where"): "methods.association",
}

_INPUT_GUIDANCE = {
    "order": "Choose ascending or descending; ranking compares exact represented values.",
    "ties": "Choose ordinal, dense, min or max. Ordinal breaks ties by complete typed instance key.",
    "partition_by": "Bind an ordered tuple of complete CategoryRelations; () selects one global partition.",
    "count": "For ranking.limit, use an integer 1..100000 excluding bool; the prefix is global.",
    "reference": "Use an exact compatible reference in this Session and source/fixed mode; standardize consumes mv.reference_weights(...).",
    "values": "Use complete grouped dimensionless stratum values in this Session.",
    "strata": "Use the ordered CategoryRelation tuple bound through the existing grouping or inclusion mapping.",
    "unit": "Use the statistical Entity proved by the frozen Metric components, distinct from measurement units.",
    "field": "Use an exact Measure, Dimension or TimeDimension Ref from the current catalog.",
    "at": "Select the attribute version independently with datetime or TimeScope.before_end.",
    "metric": "Use one exact Metric Ref from the current Semantic catalog.",
    "keys": "Retain complete axes or explicit classifications; unkeyed categories group their values, other receivers use Singleton.",
    "dimensions": "Retain complete Entity/Dimension axes or explicit corresponding categories.",
    "groups": "Use an explicit matching typed target domain to retain empty groups.",
    "dimension": "Use a declared categorical Dimension Ref on this receiver's domain.",
    "during": "Use mv.time_scope(start=..., end=...) with absolute bounds.",
    "duration": "Use a positive mv.duration(...) with exactly one named integer unit.",
    "days": "Choose positive whole local days, excluding bool.",
    "timezone": "Pass ZoneInfo with an explicit IANA key; no fixed-offset replacement.",
    "within": "Use mv.elapsed(mv.duration(...)) or mv.calendar_days(days, ZoneInfo(...)); actual deadlines must be exact and unambiguous.",
    "via": "Use the exact relationship Ref or mv.routes(...) required by this Metric.",
    "axes": "Use unique ordered retained contribution Dimension Refs.",
    "mode": "Choose joint or hierarchy; hierarchy requires at least two axes.",
    "top_k": "Common basis limit 1..1000 excluding bool, or None.",
    "coordinates": "Distinct qualified contribution Dimension Refs; complete tuples remain bound together.",
    "baseline": "Use recursively compatible numeric endpoints under the selected design.",
    "design": "Use mv.TimeChange(), mv.CohortContrast(), or mv.PeriodChange(alignment=mv.window_bucket()).",
    "pairing": "Use mv.ExactKeys() or an exact-node-bound mv.one_to_one(...) for ratio; comparison designs also accept mv.UnionKeys(missing=...).",
    "value": "Choose difference or relative_change; zero baselines remain Undefined.",
    "other": "Use another numeric quantity on the same complete observation domain.",
    "others": "Pass 1..15 distinct corresponding NumericRelations; request order defines signed lag direction.",
    "lag_range": "Use None for zero lag, or a nonempty range on the original complete time grid; +k pairs left(t) with right(t+k).",
    "horizon": "Use mv.periods(1..1000) with an approved future continuation.",
    "model": "Use mv.naive(), mv.drift() or mv.seasonal_naive(periods=s).",
    "interval_level": "Use a finite float strictly between zero and one for nominal prediction intervals.",
    "method": "Use one closed mv.sum/count/count_defined/min/max/mean() value or the stated method literal.",
    "predicate": "Build a predicate from this receiver or an exactly corresponding numeric relation.",
    "max_output_bytes": "Keep the default bound or request a smaller positive byte limit.",
}


def _doc_section(value: object, heading: str) -> str:
    lines = (getdoc(value) or "").splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(heading + ":"):
            continue
        result = [line.partition(":")[2].strip()]
        for following in lines[index + 1 :]:
            if not following.strip() or following.endswith(":"):
                break
            result.append(following.strip())
        return " ".join(part for part in result if part)
    return ""


def _effects(name: str) -> str:
    if name == "execute":
        return "Execute one qualified graph in Store 7; publish atomically or reuse an exact fixed key."
    if name in ("show", "to_pandas"):
        return "Read the exact committed Artifact under bounded or isolated-read guards."
    if name in ("read", "group_by", "observe"):
        return "Bind a typed graph; live inputs may use schema-only R1 preflight, without business rows or Run."
    if name == "contract":
        return "Read bound definition and retained metadata without source I/O."
    return "Construct a typed continuation without business-source I/O."


def inputs() -> tuple[tuple[Descriptor, ...], tuple[ExportInput, ...]]:
    """Bind first-round public values and callables to one native Help owner."""
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    types = (
        windows.Duration,
        windows.ElapsedWindow,
        windows.CalendarWindow,
        windows.AnyAnchor,
        windows.EveryAnchor,
        dsl.LogicalRetentionResult,
        dsl.MaterializedRetentionResult,
        dsl.LogicalSubjectRetentionResult,
        dsl.MaterializedSubjectRetentionResult,
        dsl.LogicalAnchorDomain,
        dsl.MaterializedAnchorDomain,
        SubjectBinding,
        cohort.AnyInstance,
        cohort.AtLeast,
        cohort.AllInstances,
        cohort.EmptyOpportunityPolicy,
        cohort.empty_opportunity,
        dsl.OneToOneCorrespondence,
        dsl.ReferenceWeights,
        dsl.LogicalAttributionResult,
        dsl.LogicalTimeRunResult,
        dsl.MaterializedTimeRunResult,
        dsl.LogicalDeviationResult,
        dsl.MaterializedDeviationResult,
        dsl.MaterializedAttributionResult,
        dsl.LogicalRankingResult,
        dsl.MaterializedRankingResult,
        dsl.LogicalTable,
        dsl.MaterializedTable,
        dsl.PeriodChange,
        dsl.UnionKeys,
        dsl.ExactKeys,
        dsl.TimeChange,
        dsl.CohortContrast,
        dsl.AnalysisAction,
        dsl.AnalysisContract,
        dsl.GroupedAnalysisDomain,
        dsl.GroupedNumericRelation,
        dsl.GroupedStatisticRelation,
        dsl.GroupedRatioRelation,
        dsl.LogicalFunnelResult,
        dsl.MaterializedFunnelResult,
        dsl.LogicalFunnelComparisonResult,
        dsl.MaterializedFunnelComparisonResult,
        dsl.LogicalHistoryResult,
        dsl.LogicalStateDistributionResult,
        dsl.MaterializedStateDistributionResult,
        dsl.LogicalTransitionSummary,
        dsl.MaterializedTransitionSummary,
        dsl.LogicalViolationResult,
        dsl.MaterializedViolationResult,
        dsl.LogicalStateIntervalResult,
        dsl.MaterializedStateIntervalResult,
        dsl.LogicalDwellSummary,
        dsl.MaterializedDwellSummary,
        dsl.MaterializedHistoryResult,
        dsl.LogicalJourneyResult,
        dsl.MaterializedJourneyResult,
        dsl.LogicalEventDurationResult,
        dsl.MaterializedEventDurationResult,
        dsl.LogicalCompletedJourneys,
        dsl.MaterializedCompletedJourneys,
        dsl.LogicalAnalysisDomain,
        dsl.LogicalTimeAnalysisDomain,
        dsl.MaterializedTimeAnalysisDomain,
        dsl.TimeGrid,
        dsl.GridWindow,
        dsl.GridEndpoint,
        dsl.LogicalAssociationResult,
        dsl.LogicalForecastResult,
        dsl.MaterializedForecastResult,
        dsl.LogicalCoefficientRelation,
        dsl.LogicalCategoryRelation,
        dsl.LogicalBooleanRelation,
        dsl.MaterializedBooleanRelation,
        dsl.LogicalTemporalRelation,
        dsl.MaterializedTemporalRelation,
        dsl.LogicalSelectedBooleanRelation,
        dsl.MaterializedSelectedBooleanRelation,
        dsl.LogicalSelectedTemporalRelation,
        dsl.MaterializedSelectedTemporalRelation,
        dsl.LogicalSelectedNumericRelation,
        dsl.MaterializedSelectedNumericRelation,
        dsl.LogicalNumericRelation,
        dsl.MaterializedAnalysisDomain,
        dsl.MaterializedAssociationResult,
        dsl.MaterializedCategoryRelation,
        dsl.MaterializedNumericRelation,
        dsl.MaterializedGroupedNumericRelation,
        dsl.LogicalRolledNumericRelation,
        dsl.MaterializedRolledNumericRelation,
        dsl.LogicalRolledRatioRelation,
        dsl.MaterializedRolledRatioRelation,
        dsl.LogicalRatioRelation,
        dsl.MaterializedRatioRelation,
        dsl.LogicalDifferenceRelation,
        dsl.MaterializedDifferenceRelation,
        dsl.LogicalSelectedDifferenceRelation,
        dsl.MaterializedSelectedDifferenceRelation,
        dsl.LogicalStatisticRelation,
        dsl.MaterializedStatisticRelation,
        dsl.MaterializedCoefficientRelation,
        dsl.LogicalCoefficientSelectionRelation,
        dsl.MaterializedCoefficientSelectionRelation,
        dsl.LogicalFixedAnalysisDomain,
        dsl.LogicalSelectedCategoryRelation,
        dsl.MaterializedSelectedCategoryRelation,
        dsl.RowMethod,
        dsl.CountMethod,
        dsl.RootRoute,
        dsl.RootRoutes,
    )
    for type_value in types:
        name = (
            type_value.__name__
            if type_value not in (dsl.RootRoute, dsl.RootRoutes)
            else ("RootRoute" if type_value is dsl.RootRoute else "RootRoutes")
        )
        policy_examples = {
            windows.AnyAnchor: "mv.any_anchor()",
            windows.EveryAnchor: "mv.every_anchor()",
            dsl.LogicalRetentionResult: "anchors.retention(returning, within=mv.elapsed(mv.duration(hours=168)))",
            dsl.MaterializedRetentionResult: "retention.execute()",
            dsl.LogicalSubjectRetentionResult: "retention.by_subject(rule=mv.any_anchor())",
            dsl.MaterializedSubjectRetentionResult: "subjects.execute()",
            windows.Duration: "mv.duration(hours=168)",
            windows.ElapsedWindow: "mv.elapsed(mv.duration(hours=168))",
            windows.CalendarWindow: "mv.calendar_days(7, ZoneInfo('America/New_York'))",
            dsl.LogicalAnchorDomain: "session.anchors(buyer, population=members, during=window)",
            dsl.MaterializedAnchorDomain: "anchors.execute()",
            dsl.LogicalFunnelResult: "journeys.funnel(axes=(channel,))",
            dsl.MaterializedFunnelResult: "funnel.execute()",
            dsl.LogicalFunnelComparisonResult: "current.compare(baseline)",
            dsl.MaterializedFunnelComparisonResult: "change.execute()",
            dsl.LogicalHistoryResult: "session.lifecycle.replay(model, population=members, window=window, seed=mv.from_inception())",
            dsl.MaterializedHistoryResult: "history.execute()",
            dsl.LogicalDwellSummary: "history.dwell()",
            dsl.MaterializedDwellSummary: "history.dwell().execute()",
            dsl.LogicalStateIntervalResult: "history.intervals()",
            dsl.MaterializedStateIntervalResult: "history.intervals().execute()",
            dsl.LogicalViolationResult: "history.violations()",
            dsl.MaterializedViolationResult: "history.violations().execute()",
            dsl.LogicalTransitionSummary: "history.transitions()",
            dsl.MaterializedTransitionSummary: "history.transitions().execute()",
            dsl.LogicalStateDistributionResult: "history.distribution(at=(checkpoint,))",
            dsl.MaterializedStateDistributionResult: "history.distribution(at=(checkpoint,)).execute()",
            dsl.LogicalJourneyResult: "session.events.match(pattern, population=members, cohort_window=window, completion_through=end, matching=mv.first_per_subject())",
            dsl.MaterializedJourneyResult: "journeys.execute()",
            dsl.LogicalEventDurationResult: "journeys.time_to_event(from_step=start, to_step=finish)",
            dsl.MaterializedEventDurationResult: "elapsed.execute()",
            dsl.LogicalCompletedJourneys: "elapsed.completed()",
            dsl.MaterializedCompletedJourneys: "elapsed.completed().execute()",
            SubjectBinding: "relation.subject_binding",
            cohort.AnyInstance: "mv.any_instance()",
            cohort.AtLeast: "mv.at_least(3)",
            cohort.AllInstances: "mv.all_instances(empty=mv.empty_opportunity.false())",
            cohort.EmptyOpportunityPolicy: "mv.empty_opportunity.false()",
            cohort.empty_opportunity: "mv.empty_opportunity",
            dsl.ExactKeys: "mv.ExactKeys()",
            dsl.UnionKeys: 'mv.UnionKeys(missing="keep")',
            dsl.TimeChange: "mv.TimeChange()",
            dsl.CohortContrast: "mv.CohortContrast()",
            dsl.PeriodChange: "mv.PeriodChange(alignment=mv.window_bucket())",
            dsl.OneToOneCorrespondence: "mv.one_to_one(left=left, right=right, via=relationship)",
            dsl.ReferenceWeights: "mv.reference_weights(values, strata=(category,), unit=entity)",
            dsl.LogicalAttributionResult: "change.attribute(axes=(channel,))",
            dsl.LogicalTimeRunResult: "daily.runs(where=daily.value.gt(20))",
            dsl.MaterializedTimeRunResult: "segments.execute()",
            dsl.LogicalDeviationResult: 'change.deviation(method="mad")',
            dsl.LogicalAssociationResult: "current.correlate(count)",
            dsl.MaterializedAssociationResult: "association.execute()",
            dsl.LogicalForecastResult: "daily.forecast(horizon=mv.periods(2))",
            dsl.MaterializedForecastResult: "forecast.execute()",
            dsl.LogicalCoefficientRelation: "association.coefficient",
            dsl.MaterializedDeviationResult: "scored.execute()",
            dsl.MaterializedAttributionResult: "attribution.execute()",
            dsl.LogicalRankingResult: 'values.rank(order="descending", ties="dense")',
            dsl.MaterializedRankingResult: "ranking.execute()",
            dsl.LogicalTable: "mv.table(values=ranking.values, ranks=ranking.ranks)",
            dsl.MaterializedTable: "table.execute()",
        }
        history_families = {
            dsl.LogicalStateDistributionResult: ("distribution", False),
            dsl.MaterializedStateDistributionResult: ("distribution", True),
            dsl.LogicalTransitionSummary: ("transitions", False),
            dsl.MaterializedTransitionSummary: ("transitions", True),
            dsl.LogicalViolationResult: ("violations", False),
            dsl.MaterializedViolationResult: ("violations", True),
            dsl.LogicalStateIntervalResult: ("intervals", False),
            dsl.MaterializedStateIntervalResult: ("intervals", True),
            dsl.LogicalDwellSummary: ("dwell", False),
            dsl.MaterializedDwellSummary: ("dwell", True),
        }
        acquisition = (
            f"Call {policy_examples[type_value]}."
            if type_value in policy_examples
            else "Call mv.time_grid(during=scope, grain=mv.grain('day'))."
            if type_value is dsl.TimeGrid
            else "Read grid.window."
            if type_value is dsl.GridWindow
            else "Read grid.start, grid.end or grid.before_end."
            if type_value is dsl.GridEndpoint
            else "Read one action from relation.contract().actions."
            if type_value is dsl.AnalysisAction
            else "Call relation.contract()."
            if type_value is dsl.AnalysisContract
            else "Call mv.count() or mv.count_defined()."
            if type_value is dsl.CountMethod
            else "Call mv.sum(), mv.count(), mv.count_defined(), mv.min(), mv.max(), or mv.mean()."
            if type_value is dsl.RowMethod
            else "Call mv.route(root, through=(...))."
            if type_value is dsl.RootRoute
            else "Call mv.routes(first_route, second_route)."
            if type_value is dsl.RootRoutes
            else "Construct through session.members() or the returned typed relation."
        )
        producers = (
            ("lifecycle.replay",)
            if type_value is dsl.LogicalHistoryResult
            else ("dsl.LogicalHistoryResult.execute",)
            if type_value is dsl.MaterializedHistoryResult
            else ("dsl.Journey.funnel",)
            if type_value is dsl.LogicalFunnelResult
            else ("dsl.LogicalFunnelResult.execute",)
            if type_value is dsl.MaterializedFunnelResult
            else ("dsl.FunnelResult.compare",)
            if type_value is dsl.LogicalFunnelComparisonResult
            else ("dsl.LogicalFunnelComparisonResult.execute",)
            if type_value is dsl.MaterializedFunnelComparisonResult
            else ("events.match",)
            if type_value is dsl.LogicalJourneyResult
            else ("dsl.Journey.time_to_event",)
            if type_value is dsl.LogicalEventDurationResult
            else ("dsl.Duration.completed",)
            if type_value is dsl.LogicalCompletedJourneys
            else ("dsl.LogicalDifferenceRelation.attribute",)
            if type_value is dsl.LogicalAttributionResult
            else ("dsl.LogicalAttributionResult.execute",)
            if type_value is dsl.MaterializedAttributionResult
            else ("dsl.table",)
            if type_value is dsl.LogicalTable
            else ("dsl.LogicalTable.execute",)
            if type_value is dsl.MaterializedTable
            else ("dsl.NumericComparison.rank",)
            if type_value is dsl.LogicalRankingResult
            else ("dsl.LogicalRankingResult.execute",)
            if type_value is dsl.MaterializedRankingResult
            else ("dsl.reference_weights",)
            if type_value is dsl.ReferenceWeights
            else ("dsl.any_instance",)
            if type_value is cohort.AnyInstance
            else ("dsl.at_least",)
            if type_value is cohort.AtLeast
            else ("dsl.all_instances",)
            if type_value is cohort.AllInstances
            else ("empty_opportunity",)
            if type_value is cohort.EmptyOpportunityPolicy
            else ("dsl.CohortDomain.cohort",)
            if type_value is SubjectBinding
            else ("dsl.time_grid",)
            if type_value is dsl.TimeGrid
            else ("TimeGrid",)
            if type_value in (dsl.GridWindow, dsl.GridEndpoint)
            else ("dsl.LogicalAnalysisDomain.each",)
            if type_value is dsl.LogicalTimeAnalysisDomain
            else ("dsl.Value.contract",)
            if type_value is dsl.AnalysisContract
            else ("AnalysisContract",)
            if type_value is dsl.AnalysisAction
            else ("dsl.count", "dsl.count_defined")
            if type_value is dsl.CountMethod
            else ("dsl.sum", "dsl.count", "dsl.count_defined", "dsl.min", "dsl.max", "dsl.mean")
            if type_value is dsl.RowMethod
            else ("dsl.route",)
            if type_value is dsl.RootRoute
            else ("dsl.routes",)
            if type_value is dsl.RootRoutes
            else ("session.members",)
        )
        if type_value is windows.Duration:
            producers = ("dsl.duration",)
        elif type_value is dsl.LogicalTimeRunResult:
            producers = ("dsl.NumericComparison.runs",)
        elif type_value is dsl.LogicalAssociationResult:
            producers = ("dsl.NumericComparison.correlate",)
        elif type_value is dsl.LogicalForecastResult:
            producers = ("dsl.NumericComparison.forecast",)
        elif type_value is dsl.LogicalDeviationResult:
            producers = ("dsl.NumericComparison.deviation",)
        elif type_value is windows.ElapsedWindow:
            producers = ("dsl.elapsed",)
        elif type_value is windows.CalendarWindow:
            producers = ("dsl.calendar_days",)
        elif type_value in (windows.AnyAnchor, windows.EveryAnchor):
            producers = (
                "dsl.any_anchor" if type_value is windows.AnyAnchor else "dsl.every_anchor",
            )
        elif type_value is dsl.LogicalRetentionResult:
            producers = ("dsl.AnchorDomain.retention",)
        elif type_value is dsl.LogicalSubjectRetentionResult:
            producers = ("dsl.InstanceRetention.by_subject",)
        elif type_value is dsl.LogicalAnchorDomain:
            producers = ("session.anchors",)
        if type_value in history_families:
            method, materialized = history_families[type_value]
            producers = (
                ("dsl.Logical" + type_value.__name__.removeprefix("Materialized") + ".execute",)
                if materialized
                else ("dsl.History." + method,)
            )
        descriptors.append(
            value_type(
                name,
                type_value,
                summary=f"Governed Analysis {name} value type.",
                acquisition=acquisition,
                producers=("session.artifact",) if name.startswith("Materialized") else producers,
                consumers=(
                    "dsl.LogicalAssociationResult.where",
                    "dsl.LogicalAssociationResult.execute",
                    "LogicalCoefficientRelation",
                    "dsl.Value.contract",
                )
                if type_value in (dsl.LogicalAssociationResult, dsl.MaterializedAssociationResult)
                else (
                    "dsl.LogicalForecastResult.where",
                    "dsl.LogicalForecastResult.execute",
                    "dsl.Value.contract",
                )
                if type_value in (dsl.LogicalForecastResult, dsl.MaterializedForecastResult)
                else (
                    "dsl.LogicalCoefficientRelation.where",
                    "dsl.LogicalCoefficientRelation.execute",
                    "dsl.Value.contract",
                )
                if type_value is dsl.LogicalCoefficientRelation
                else (
                    "dsl.LogicalTimeRunResult.where",
                    "dsl.LogicalTimeRunResult.execute",
                    "dsl.Value.contract",
                )
                if type_value in (dsl.LogicalTimeRunResult, dsl.MaterializedTimeRunResult)
                else ("dsl.InstanceRetention.by_subject",)
                if type_value in (windows.AnyAnchor, windows.EveryAnchor)
                else (
                    "dsl.LogicalDeviationResult.where",
                    "dsl.LogicalDeviationResult.execute",
                    "dsl.Value.contract",
                )
                if type_value in (dsl.LogicalDeviationResult, dsl.MaterializedDeviationResult)
                else (
                    "dsl.Retention.known_true",
                    "dsl.Retention.known_false",
                    "dsl.Retention.unknown",
                    "dsl.Value.contract",
                    "dsl.InstanceRetention.by_subject",
                )
                if type_value in (dsl.LogicalRetentionResult, dsl.MaterializedRetentionResult)
                else (
                    "dsl.Retention.known_true",
                    "dsl.Retention.known_false",
                    "dsl.Retention.unknown",
                    "dsl.Value.contract",
                )
                if type_value
                in (dsl.LogicalSubjectRetentionResult, dsl.MaterializedSubjectRetentionResult)
                else ("dsl.AnchorDomain.observe", "dsl.AnchorDomain.retention")
                if type_value in (windows.ElapsedWindow, windows.CalendarWindow)
                else ("dsl.elapsed",)
                if type_value is windows.Duration
                else ("dsl.AnchorDomain.subjects", "dsl.Value.contract")
                if type_value is dsl.MaterializedAnchorDomain
                else (
                    "dsl.AnchorDomain.observe",
                    "dsl.AnchorDomain.retention",
                    "dsl.AnchorDomain.subjects",
                    "dsl.Value.contract",
                )
                if type_value is dsl.LogicalAnchorDomain
                else ("dsl.Value.contract",)
                if type_value in history_families
                else (
                    "dsl.LogicalHistoryResult.execute",
                    "dsl.History.read",
                    "dsl.History.distribution",
                    "dsl.History.transitions",
                    "dsl.History.violations",
                    "dsl.History.intervals",
                    "dsl.History.dwell",
                    "dsl.Value.contract",
                )
                if type_value in (dsl.LogicalHistoryResult, dsl.MaterializedHistoryResult)
                else ("dsl.Funnel.read", "dsl.FunnelResult.compare")
                if type_value in (dsl.LogicalFunnelResult, dsl.MaterializedFunnelResult)
                else ("dsl.Funnel.read", "dsl.FunnelComparison.attribute")
                if type_value
                in (dsl.LogicalFunnelComparisonResult, dsl.MaterializedFunnelComparisonResult)
                else (
                    "dsl.Journey.funnel",
                    "dsl.Journey.time_to_event",
                    "dsl.Journey.read",
                    "dsl.Journey.subjects",
                )
                if type_value in (dsl.LogicalJourneyResult, dsl.MaterializedJourneyResult)
                else ("dsl.Duration.completed", "dsl.LogicalEventDurationResult.execute")
                if type_value
                in (
                    dsl.LogicalEventDurationResult,
                    dsl.MaterializedEventDurationResult,
                    dsl.LogicalCompletedJourneys,
                    dsl.MaterializedCompletedJourneys,
                )
                else ("dsl.Attribution.where", "dsl.LogicalAttributionResult.execute")
                if type_value in (dsl.LogicalAttributionResult, dsl.MaterializedAttributionResult)
                else ("dsl.LogicalTable.execute",)
                if type_value is dsl.LogicalTable
                else ("dsl.MaterializedTable.show", "dsl.MaterializedTable.to_pandas")
                if type_value is dsl.MaterializedTable
                else ("dsl.Ranking.where", "dsl.Ranking.limit", "dsl.table")
                if type_value in (dsl.LogicalRankingResult, dsl.MaterializedRankingResult)
                else ("dsl.NumericComparison.standardize", "dsl.ReferenceWeights.show")
                if type_value is dsl.ReferenceWeights
                else ("GridWindow", "GridEndpoint", "dsl.TimeGrid.show")
                if type_value is dsl.TimeGrid
                else (
                    "dsl.LogicalAnalysisDomain.read",
                    "dsl.LogicalAnalysisDomain.observe",
                    "dsl.GridEndpoint.show",
                )
                if type_value is dsl.GridEndpoint
                else ("dsl.LogicalAnalysisDomain.observe", "dsl.GridWindow.show")
                if type_value is dsl.GridWindow
                else ("AnalysisAction",)
                if type_value is dsl.AnalysisContract
                else ("dsl.routes",)
                if type_value is dsl.RootRoute
                else ("dsl.LogicalAnalysisDomain.observe",)
                if type_value is dsl.RootRoutes
                else ("methods.metric",)
                if type_value in (dsl.RowMethod, dsl.CountMethod)
                else (
                    "dsl." + name + ".false"
                    if type_value is cohort.empty_opportunity
                    else "dsl." + name + ".show",
                    "dsl.NumericComparison.compare",
                    "dsl.NumericComparison.ratio",
                    *(
                        ("dsl.empty_opportunity.true", "dsl.empty_opportunity.undefined")
                        if type_value is cohort.empty_opportunity
                        else ()
                    ),
                    *(
                        ("EmptyOpportunityPolicy", "empty_opportunity")
                        if type_value is cohort.AllInstances
                        else ()
                    ),
                )
                if type_value in policy_examples
                else (),
                constraints=("Exact member, semantic and Artifact bindings govern continuations.",),
            )
        )
        exports.append(ExportInput(name, type_value, name))

    descriptors.append(
        operation(
            "dsl.time_grid",
            "mv.time_grid",
            dsl.time_grid,
            summary="Construct a finite time grid with exact receiver-bound handles.",
            discovery_group="methods.metric",
            parameters=(
                ParameterInput("during", "Use one finite half-open TimeScope."),
                ParameterInput("grain", "Use a builtin or certified calendar Grain."),
                ParameterInput(
                    "timezone",
                    "Optional boundary authority; a calendar must match its certification.",
                ),
            ),
            output="TimeGrid",
            constraints=("The Session binds report timezone once; conflicting reuse rejects.",),
            effects="Pure construction; no business rows or Run.",
            failures=("AnalysisError: inspect the exact binding repair.",),
            example=ExampleInput(
                "grid = mv.time_grid(during=mv.time_scope(start='2026-08-01', end='2026-09-01'), grain=mv.grain('day'))",
                (),
                "grid",
                "A bounded time-grid specification.",
                True,
            ),
        )
    )
    exports.append(ExportInput("time_grid", dsl.time_grid, "dsl.time_grid"))

    descriptors.append(
        operation(
            "dsl.table",
            "mv.table",
            dsl.table,
            summary="Construct an ordered complete-key terminal table.",
            discovery_group="methods.rows",
            parameters=(
                ParameterInput(
                    "columns", "Use labeled scalar Relations with complete matching typed keys."
                ),
            ),
            output="LogicalTable",
            constraints=(
                "One Session and source/fixed mode; terminal export only. Non-Defined pandas values lose their Cell labels, retained by show() and the Artifact.",
            ),
            effects="Pure definition binding; no business rows or Run.",
            failures=("AnalysisError: use the exact structured binding or key repair.",),
            example=ExampleInput(
                "result = mv.table(values=values, ranks=ranks)",
                ("values", "ranks"),
                "result",
                "A terminal complete-key table.",
                True,
            ),
        )
    )
    exports.append(ExportInput("table", dsl.table, "dsl.table"))
    functions = (
        windows.duration,
        windows.elapsed,
        windows.calendar_days,
        windows.any_anchor,
        windows.every_anchor,
        dsl.route,
        dsl.routes,
        dsl.sum,
        dsl.count,
        dsl.count_defined,
        dsl.min,
        dsl.max,
        dsl.mean,
    )
    for function in functions:
        name = function.__name__
        params = tuple(
            ParameterInput(
                key,
                f"Use integer {key}; omit the other named units."
                if name == "duration"
                else _INPUT_GUIDANCE.get(key, "Use the exact governed input."),
            )
            for key in signature(function).parameters
        )
        descriptors.append(
            operation(
                "dsl." + name,
                "mv." + name,
                function,
                summary=f"Construct the admitted {name} argument for the Analysis DSL.",
                discovery_group="methods.events"
                if name in ("duration", "elapsed", "calendar_days", "any_anchor", "every_anchor")
                else None,
                parameters=params,
                output={
                    "duration": "Duration",
                    "elapsed": "ElapsedWindow",
                    "calendar_days": "CalendarWindow",
                    "any_anchor": "AnyAnchor",
                    "every_anchor": "EveryAnchor",
                }[name]
                if name in ("duration", "elapsed", "calendar_days", "any_anchor", "every_anchor")
                else "CountMethod"
                if name in ("count", "count_defined")
                else "RootRoute"
                if name == "route"
                else "RootRoutes"
                if name == "routes"
                else "RowMethod",
                constraints=(
                    _doc_section(function, "Constraints")
                    if name
                    in ("duration", "elapsed", "calendar_days", "any_anchor", "every_anchor")
                    else "Only qualified typed graph input shapes are admitted.",
                ),
                effects="Pure argument construction; no source read or Run.",
                failures=("AnalysisError: use the structured expected and received repair.",),
                example=ExampleInput(
                    "result = mv.duration(hours=168)"
                    if name == "duration"
                    else "result = mv.elapsed(mv.duration(hours=168))"
                    if name == "elapsed"
                    else "from zoneinfo import ZoneInfo\nresult = mv.calendar_days(7, ZoneInfo('America/New_York'))"
                    if name == "calendar_days"
                    else f"result = mv.{name}()"
                    if name
                    in (
                        "sum",
                        "count",
                        "count_defined",
                        "min",
                        "max",
                        "mean",
                        "any_anchor",
                        "every_anchor",
                    )
                    else f"result = mv.{name}(root, through=(relationship,))"
                    if name == "route"
                    else "result = mv.routes(first_route, second_route)",
                    ()
                    if name
                    in (
                        "sum",
                        "count",
                        "count_defined",
                        "min",
                        "max",
                        "mean",
                        "duration",
                        "elapsed",
                        "calendar_days",
                        "any_anchor",
                        "every_anchor",
                    )
                    else ("root", "relationship")
                    if name == "route"
                    else ("first_route", "second_route"),
                    "result",
                    "A closed Analysis DSL argument.",
                    True,
                ),
            )
        )
        exports.append(ExportInput(name, function, "dsl." + name))

    descriptors.append(
        operation(
            "dsl.one_to_one",
            "mv.one_to_one",
            dsl.one_to_one,
            summary="Bind an explicit one-to-one relationship to exact ordered numeric endpoints.",
            discovery_group="methods.compare",
            parameters=tuple(
                ParameterInput(
                    name,
                    _INPUT_GUIDANCE.get(name, "Use exact ordered numeric endpoint bindings."),
                    (),
                )
                for name in ("left", "right", "via", "time")
            ),
            output="OneToOneCorrespondence",
            constraints=(
                "Requires complete retained relationship identity keys; no many-to-one, Union or node reuse.",
            ),
            effects="Construct a bound correspondence without business reads or Run.",
            failures=("AnalysisError: follow the exact binding or retained-parts repair.",),
            example=ExampleInput(
                "result = mv.one_to_one(left=left, right=right, via=relationship)",
                ("left", "right", "relationship"),
                "result",
                "An exact ordered correspondence.",
                True,
            ),
        )
    )
    exports.append(ExportInput("one_to_one", dsl.one_to_one, "dsl.one_to_one"))

    descriptors.append(
        operation(
            "dsl.reference_weights",
            "mv.reference_weights",
            dsl.reference_weights,
            summary="Freeze complete stratum weights and their statistical Entity.",
            discovery_group="methods.metric.reference",
            parameters=tuple(
                ParameterInput(name, _INPUT_GUIDANCE[name]) for name in ("values", "strata", "unit")
            ),
            output="ReferenceWeights",
            constraints=(
                "Same Session and mode, exact ordered strata; no normalization or standalone execute.",
            ),
            effects="Pure construction; no business rows or Run.",
            failures=("AnalysisError: follow the exact reference binding repair.",),
            example=ExampleInput(
                "result = mv.reference_weights(values, strata=(category,), unit=entity)",
                ("values", "category", "entity"),
                "result",
                "A frozen reference composition.",
                True,
            ),
        )
    )
    exports.append(ExportInput("reference_weights", dsl.reference_weights, "dsl.reference_weights"))

    for quantifier_function, output, example in (
        (cohort.any_instance, "AnyInstance", "mv.any_instance()"),
        (cohort.at_least, "AtLeast", "mv.at_least(3)"),
        (
            cohort.all_instances,
            "AllInstances",
            "mv.all_instances(empty=mv.empty_opportunity.false())",
        ),
    ):
        name = quantifier_function.__name__
        descriptors.append(
            operation(
                "dsl." + name,
                "mv." + name,
                quantifier_function,
                summary=(getdoc(quantifier_function) or name).splitlines()[0],
                discovery_group="methods.rows",
                parameters=tuple(
                    ParameterInput(
                        key,
                        "Use a positive integer excluding bool."
                        if key == "count"
                        else "Use mv.empty_opportunity.true/false/undefined().",
                    )
                    for key in signature(quantifier_function).parameters
                ),
                output=output,
                constraints=(
                    "Requires complete opportunities and a decidable qualification for every target.",
                ),
                effects="Pure construction; no business read or Run.",
                failures=("AnalysisError: inspect the structured repair.",),
                example=ExampleInput("result = " + example, (), "result", output, True),
            )
        )
        exports.append(ExportInput(name, quantifier_function, "dsl." + name))

    for composite_function in (fields.all_of, fields.any_of, fields.not_):
        name = composite_function.__name__
        descriptors.append(
            operation(
                name,
                "mv." + name,
                composite_function,
                summary="Compose typed relation predicates after checking every input.",
                discovery_group="filters",
                parameters=(
                    ParameterInput(
                        "predicate" if name == "not_" else "predicates",
                        "Use exact relation.value predicates; conjunction and disjunction need at least two operands.",
                    ),
                ),
                output="closed bound predicate",
                constraints=(
                    "Every child consumes the complete receiver domain; there is no short-circuit exemption.",
                ),
                effects="Pure construction; no business read or Run.",
                failures=("AnalysisError: bind exact compatible input fields.",),
                example=ExampleInput(
                    "result = mv." + name + "(values.value.gt(0), values.value.lt(10))"
                    if name != "not_"
                    else "result = mv.not_(values.value.eq(0))",
                    ("values",),
                    "result",
                    "An immutable predicate.",
                    True,
                ),
            )
        )
        exports.append(ExportInput(name, composite_function, name))

    field_types = (
        fields.NumericField,
        fields.CategoryField,
        fields.BooleanField,
        fields.TemporalField,
    )
    predicate_types = (
        fields.NumericPredicate,
        fields.CategoryPredicate,
        fields.ScalarPredicate,
        fields.StatePredicate,
        fields.CompositePredicate,
    )
    for field_type in (*field_types, *predicate_types):
        descriptors.append(
            value_type(
                "dsl." + field_type.__name__,
                field_type,
                summary="An exact bound predicate input.",
                acquisition="Read relation.value or construct a typed predicate.",
                producers=("dsl.LogicalNumericRelation.where",),
                consumers=(
                    "dsl.LogicalNumericRelation.where",
                    "dsl.CohortDomain.cohort",
                    "dsl.BoundValue.show",
                    *(
                        "dsl." + field_type.__name__ + "." + method
                        for method, operation_value in vars(field_type).items()
                        if not method.startswith("_") and isfunction(operation_value)
                    ),
                    *(
                        (
                            "dsl.NumericField",
                            "dsl.CategoryField",
                            "dsl.BooleanField",
                            "dsl.TemporalField",
                            "dsl.StatePredicate",
                        )
                        if field_type is fields.CompositePredicate
                        else ()
                    ),
                ),
                constraints=("Exact binding; no implicit truth or short-circuit exemption.",),
            )
        )

    owner_methods: tuple[type[object], ...] = (
        fields._BoundValue,
        *field_types,
        dsl._Value,
        dsl._History,
        dsl._HistoryInstance,
        dsl._StateDistributionResult,
        dsl._TransitionSummary,
        dsl._ViolationResult,
        dsl._StateIntervalResult,
        dsl._DwellSummary,
        dsl._AnchorDomain,
        dsl._Retention,
        dsl._InstanceRetention,
        dsl._Journey,
        dsl._Funnel,
        dsl._FunnelResult,
        dsl._FunnelComparison,
        dsl._Duration,
        dsl._Attribution,
        dsl._Ranking,
        dsl._CohortDomain,
        dsl._CountRelation,
        dsl._NumericComparison,
        dsl._OriginalContinuation,
        dsl._StatisticContinuation,
        dsl._MaterializedRead,
        *(
            value
            for value in types
            if value not in (dsl.RowMethod, dsl.CountMethod, dsl.RootRoute, dsl.RootRoutes)
        ),
    )
    for owner in owner_methods:
        for name, value in vars(owner).items():
            if isinstance(value, staticmethod):
                value = value.__func__
            if name.startswith("_") or not isfunction(value):
                continue
            target = "dsl." + owner.__name__.lstrip("_") + "." + name
            if owner is dsl._MaterializedRead:
                target = {
                    "show": "actions.show",
                    "to_pandas": "actions.to_pandas",
                    "findings": "artifact.findings",
                    "finding": "artifact.finding",
                }.get(name, target)
            installed = signature(value)
            arguments = required_arguments(
                parameter for key, parameter in installed.parameters.items() if key != "self"
            )
            params = tuple(
                ParameterInput(
                    key,
                    _INPUT_GUIDANCE.get(key, "Use the exact bound relation or governed input."),
                    ("TimeChange", "CohortContrast", "PeriodChange")
                    if key == "design"
                    else ("ExactKeys", "UnionKeys", "OneToOneCorrespondence")
                    if key == "pairing"
                    else ("dsl.CompositePredicate",)
                    if key == "predicate"
                    else ("AnyAnchor", "EveryAnchor")
                    if key == "rule" and owner is dsl._InstanceRetention
                    else ("AnyInstance", "AtLeast", "AllInstances")
                    if key == "rule"
                    else ("SubjectBinding",)
                    if key == "through" and name in ("cohort", "members")
                    else ("dsl.route", "dsl.routes")
                    if key == "via"
                    else ("dsl.elapsed", "dsl.calendar_days")
                    if key == "within" and owner is dsl._AnchorDomain
                    else (
                        "dsl.sum",
                        "dsl.count",
                        "dsl.count_defined",
                        "dsl.min",
                        "dsl.max",
                        "dsl.mean",
                    )
                    if key == "method" and name == "summarize"
                    else (),
                )
                for key in installed.parameters
                if key != "self"
            )
            descriptors.append(
                operation(
                    target,
                    "relation." + name,
                    value,
                    bindings=(bind(value, owner),),
                    summary=(getdoc(value) or f"{owner.__name__}.{name}.").splitlines()[0],
                    discovery_group=_METHOD_GROUPS.get((owner.__name__, name)),
                    parameters=params,
                    output=str(installed.return_annotation),
                    constraints=(
                        _doc_section(value, "Constraints")
                        or "The exact receiver and current contract gate this operation.",
                    ),
                    effects=_effects(name),
                    failures=(
                        "AnalysisError: inspect the structured repair for the current shape.",
                    ),
                    example=ExampleInput(
                        f"result = relation.{name}()"
                        if not params
                        else f"result = relation.{name}({', '.join(arguments)})",
                        (
                            "relation",
                            *(
                                parameter.name
                                for parameter in installed.parameters.values()
                                if parameter.name != "self" and parameter.default is Parameter.empty
                            ),
                        ),
                        "result",
                        "The receiver-bound result.",
                        True,
                    ),
                )
            )
    return tuple(descriptors), tuple(exports)
