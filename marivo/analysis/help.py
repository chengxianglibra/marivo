"""mv.help - agent-facing introspection of the analysis surface."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from marivo.refs import SemanticRef
    from marivo.semantic.reader import SemanticProject

from marivo.introspection.constraints import Constraint
from marivo.introspection.render import format_family_block
from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import Surface, render, top_level_families

from .constraints import constraints_for_symbol, iter_constraints


class _SemanticHelpIR(Protocol):
    semantic_id: str


_HELP_ONLY_ENTRIES: tuple[str, ...] = (
    "workflow",
    "session",
    "catalog",
    "observe",
    "compare",
    "attribute",
    "discover",
    "correlate",
    "hypothesis_test",
    "forecast",
    "derive_metric_frame",
    "assess_quality",
    "alignment",
    "calendar",
    "artifacts",
    "recovery",
    "advanced",
    "cumulative_frame",
)

_FRAME_SYMBOLS: set[str] = {
    "MetricFrame",
    "DeltaFrame",
    "AttributionFrame",
    "ForecastFrame",
    "QualityReport",
    "CandidateSet",
    "AssociationResult",
    "ComponentFrame",
    "CoverageFrame",
    "HypothesisTestResult",
}

_CONSTRUCTED_BY: dict[str, str] = {
    "MetricFrame": "session.observe(...), session.derive_metric_frame(...)",
    "DeltaFrame": "session.compare(...)",
    "AttributionFrame": "session.attribute(...)",
    "ForecastFrame": "session.forecast(...)",
    "QualityReport": "session.assess_quality(...)",
    "CandidateSet": "session.discover.<objective>(...)",
    "AssociationResult": "session.correlate(...)",
    "ComponentFrame": "MetricFrame.components(), DeltaFrame.components()",
    "CoverageFrame": "MetricFrame.coverage()",
    "HypothesisTestResult": "session.hypothesis_test(...)",
}

_SUMMARIES: dict[str, str] = {
    "help": "this introspection entry point",
    "help_text": "return analysis help text without printing",
    "workflow": "default agent runbook: session, catalog discovery, observe, read artifacts, recovery",
    "session": "analysis session lifecycle and persistence helpers",
    "catalog": "analysis-side semantic catalog consumption: list domains, metrics, dimensions",
    "artifacts": "artifact read protocol: show(), contract(), to_pandas()",
    "recovery": "cross-script frame and job recovery helpers",
    "advanced": "non-default surfaces: transform, select, contract DTO, lineage",
    "datasources": "DEPRECATED: use marivo.datasource (md.*) for datasource registration, validation, and runtime lookup",
    "evidence": "analysis evidence DTOs and session knowledge helpers",
    "errors": "AnalysisError hierarchy and analysis error kinds",
    "frames": "analysis frame and frame metadata types",
    "observe": "build a MetricFrame from one metric or a same-scope metric list and window",
    "compare": "compare two MetricFrames into a DeltaFrame",
    "attribute": "attribute a DeltaFrame into an AttributionFrame over explicit axes; missing axes are materialized from recoverable lineage",
    "discover": "discover deterministic candidate sets from analysis artifacts",
    "transform": "family-preserving reshape of a MetricFrame or DeltaFrame",
    "correlate": "correlate compatible analysis frames",
    "forecast": "project a time_series or panel MetricFrame forward",
    "assess_quality": "inspect artifact quality and produce a QualityReport",
    "hypothesis_test": "run a paired hypothesis_test over compatible MetricFrames",
    "derive_metric_frame": "run a governed Ibis query and validate the output as a MetricFrame",
    "IbisQuerySpec": "query builder returned by mv.ibis_query(...) for derive_metric_frame",
    "MetricColumns": "column binding object returned by mv.metric_columns(...)",
    "MetricColumnBinding": "one output-column to semantic-ref binding for derive_metric_frame",
    "DeriveContext": "deterministic query-build context passed to mv.ibis_query builders",
    "ibis_query": "construct a governed Ibis query spec for derive_metric_frame",
    "metric_columns": "bind derive_metric_frame output columns to metric roles",
    "time_column": "bind one query output column to a catalog time dimension",
    "dimension_column": "bind one query output column to a catalog dimension",
    "alignment": "AlignmentPolicy variants and output columns",
    "calendar": "project-local calendar JSON file shape",
    "select": "read typed fields from a CandidateSet row",
    "cumulative_frame": "cumulative MetricFrame running-total caveats and intent gates",
    "Session": "live analysis session object with execution and artifact methods",
    "SessionSummary": "lightweight row returned by mv.session.list()",
    "JobSummary": "lightweight row returned by Session.jobs() and recent_jobs()",
    "BaseFrame": "base immutable analysis artifact protocol: ref, kind, show(), contract(), state, to_pandas()",
    "BaseFrameMeta": "shared metadata model available as frame.meta",
    "FrameSummaryEntry": "rich persisted frame metadata returned by Session.frame_summaries()",
    "Lineage": "ordered provenance for an analysis frame",
    "LineageStep": "single lineage step within a frame provenance chain",
    "MetricFrame": "observed metric values with scalar, time_series, segmented, or panel shape",
    "DeltaFrame": "comparison output with aligned current and baseline values",
    "AttributionFrame": "decomposition attribution output",
    "ForecastFrame": "forecast output for a time_series or panel metric history",
    "QualityReport": "quality assessment output for an observed metric frame",
    "CandidateSet": "candidate rows returned by discovery; candidates are not recommendations",
    "AssociationResult": "correlation result (show() displays r, method, sample size)",
    "ComponentFrame": "component values linked to component-aware derived metric frames",
    "CoverageFrame": "sampled metric time-slot coverage linked from a MetricFrame",
    "HypothesisTestResult": "statistical test result frame",
    "AbsoluteWindow": "half-open time interval [start, end) for observe time_scope",
    "AlignmentKind": "literal values for AlignmentPolicy.kind",
    "AlignmentPolicy": "alignment strategy for compare and correlate",
    "window_bucket": "construct window-bucket AlignmentPolicy",
    "dow_aligned": "construct day-of-week calendar AlignmentPolicy",
    "holiday_aligned": "construct holiday calendar AlignmentPolicy",
    "holiday_and_dow_aligned": "construct holiday-then-day-of-week AlignmentPolicy",
    "ArtifactRef": "session-local analysis artifact ref",
    "ArtifactAffordance": "non-ranked mechanical compatibility entry",
    "ArtifactColumn": "column descriptor within an artifact schema",
    "ArtifactContract": "mechanical consumption contract returned by artifact.contract()",
    "ArtifactParamTemplate": "parameter template for an affordance entry",
    "ArtifactPrecondition": "precondition pass/fail entry within an affordance",
    "ArtifactSchema": "schema descriptor embedded in artifact.contract().artifact_schema",
    "ArtifactState": "baseline materialization and content hash facts",
    "BlockingIssue": "blocking issue attached to frame meta",
    "CalendarPolicy": "calendar provider policy for calendar-backed alignment",
    "CalendarRef": "calendar provider ref",
    "CandidateObjective": "literal values for CandidateSet objective field",
    "ConfidenceScope": "confidence scope attached to frame meta",
    "DiscoverSensitivity": "literal values for discover sensitivity parameter",
    "SemanticObject": "catalog object returned by session.catalog.get(...)",
    "SemanticRef": "catalog ref returned by SemanticObject.ref",
    "SamplingPolicy": "sampling policy for compare and correlate",
    "SlicePredicate": "typed dict for analysis slice predicates",
    "SlicePredicateOp": "literal values for SlicePredicate.op field",
    "SliceScalar": "scalar types allowed in slice values",
    "SliceValue": "accepted value types for analysis slice filters",
    "TimeScope": "half-open time interval model for observe",
    "TimeScopeInput": "accepted time_scope input types",
}

_TYPE_ALIASES: set[str] = {
    "AlignmentKind",
    "CandidateObjective",
    "DiscoverSensitivity",
    "SlicePredicateOp",
    "SliceScalar",
    "SliceValue",
    "TimeScopeInput",
}

_SEE_ALSO: dict[str, tuple[str, ...]] = {
    "MetricFrame": (
        "mv.help('observe')",
        "mv.help('MetricFrame.components')",
        "mv.help('MetricFrame.metric')",
    ),
    "DeltaFrame": ("mv.help('compare')", "mv.help('attribute')"),
    "CandidateSet": ("mv.help('discover')", "mv.help('select')"),
    "AlignmentPolicy": ("mv.help('alignment')", "mv.help('calendar')"),
}

_SESSION_INTENT_HELP_TARGETS: tuple[str, ...] = (
    "observe",
    "compare",
    "attribute",
    "correlate",
    "forecast",
    "assess_quality",
    "hypothesis_test",
    "derive_metric_frame",
)

_SESSION_NAMESPACE_HELP_TARGETS: tuple[str, ...] = ("discover",)

_SESSION_HELP_ALIASES: dict[str, str] = {
    alias: target
    for target in (*_SESSION_INTENT_HELP_TARGETS, *_SESSION_NAMESPACE_HELP_TARGETS)
    for alias in (
        f"mv.Session.{target}",
        f"session.{target}",
        f"mv.session.{target}",
    )
}


def _discover_content() -> dict[str, object]:
    from marivo.analysis.intents.discover import (
        _OBJECTIVE_COMPATIBILITY,
        _OBJECTIVE_REQUIRED_KWARGS,
        _OBJECTIVE_THRESHOLD,
        _OBJECTIVE_TO_SHAPE,
    )

    objectives: list[dict[str, object]] = []
    for objective in sorted(_OBJECTIVE_COMPATIBILITY):
        compat = _OBJECTIVE_COMPATIBILITY[objective]
        threshold_info = _OBJECTIVE_THRESHOLD.get(objective)
        objectives.append(
            {
                "objective": objective,
                "helper": f"session.discover.{objective}",
                "shape": _OBJECTIVE_TO_SHAPE[objective],
                "sources": {
                    source_kind: sorted(semantic_kinds)
                    for source_kind, semantic_kinds in sorted(compat.items())
                },
                "required_kwargs": list(_OBJECTIVE_REQUIRED_KWARGS.get(objective, ())),
                "threshold": threshold_info,
            }
        )
    return {
        "summary": "session.discover objective helper matrix.",
        "objectives": objectives,
        "example": (
            'region = session.catalog.get("dimension.sales.orders.region").ref\n'
            "session.discover.driver_axes(\n"
            '    delta, search_space=[region], analysis_purpose="find driver dimensions for revenue change"\n'
            ")"
        ),
    }


def _threshold_label(threshold_info: dict[str, object] | None) -> str:
    if threshold_info is None:
        return "-"
    method = cast("str", threshold_info["method"])
    default = cast("float", threshold_info["default"])
    return f"{method} >= {default}"


def _discover_text(content: dict[str, object]) -> str:
    objectives = cast("list[dict[str, object]]", content["objectives"])
    lines = ["session.discover objective helper matrix:", ""]
    header = (
        f"  {'helper':<42}{'source':<14}{'semantic_kind':<40}{'shape':<26}{'threshold':<28}required"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for item in objectives:
        sources = cast("dict[str, list[str]]", item["sources"])
        required = ", ".join(cast("list[str]", item["required_kwargs"])) or "-"
        threshold_label = _threshold_label(cast("dict[str, object] | None", item.get("threshold")))
        for source_kind in sorted(sources):
            kinds = "|".join(sources[source_kind])
            lines.append(
                f"  {item['helper']:<42}{source_kind:<14}{kinds:<40}{item['shape']:<26}{threshold_label:<28}{required}"
            )
    lines.append("")
    lines.append(f"Example: {content['example']}")
    return "\n".join(lines)


def _select_content() -> dict[str, object]:
    from marivo.analysis.intents.select import _FIELD_BY_SHAPE

    return {
        "summary": "CandidateSet.select attribute-by-shape matrix.",
        "fields_by_shape": {
            shape: sorted(fields) for shape, fields in sorted(_FIELD_BY_SHAPE.items())
        },
        "dot_paths": [
            "keys.<dim>",
            "selector.<dim>",
        ],
        "example": 'cs.select(rank=1, attribute="window")',
    }


def _select_text(content: dict[str, object]) -> str:
    fields_by_shape = cast("dict[str, list[str]]", content["fields_by_shape"])
    lines = ["CandidateSet.select attribute-by-shape matrix:", ""]
    for shape in sorted(fields_by_shape):
        lines.append(f"  {shape:<28}{', '.join(fields_by_shape[shape])}")
    lines.append("")
    lines.append('Dot-paths "keys.<dim>" / "selector.<dim>" pull a single key out')
    lines.append(f"of the candidate row. Example: {content['example']}")
    return "\n".join(lines)


def _transform_content() -> dict[str, object]:
    from marivo.analysis.intents.transform import _SUPPORTED_OPS

    required_args: dict[str, tuple[str, ...]] = {
        "filter": ("predicate",),
        "slice": ("slice_by",),
        "rollup": (),  # at least one of drop_axes/grain — see notes
        "topk": ("by", "limit"),
        "bottomk": ("by", "limit"),
        "rank": ("by",),
        "normalize": ("mode",),
        "window": ("window",),
    }
    return {
        "summary": "frame.transform op helper matrix.",
        "ops": [
            {
                "op": op,
                "helper": f"frame.transform.{op}",
                "required_kwargs": list(required_args.get(op, ())),
            }
            for op in _SUPPORTED_OPS
        ],
        "notes": [
            "normalize is MetricFrame-only; DeltaFrame.transform has no normalize method.",
            "rollup requires at least one of drop_axes= or grain= (grain re-buckets the time axis).",
            "bottomk(by='delta') returns the most-negative deltas: largest declines.",
        ],
        "example": (
            "delta.transform.bottomk(\\n"
            '    by="delta", limit=3, analysis_purpose="keep largest declines"\\n'
            ")"
        ),
    }


def _transform_text(content: dict[str, object]) -> str:
    ops = cast("list[dict[str, object]]", content["ops"])
    lines = ["frame.transform op helper matrix:", ""]
    for op in ops:
        required = ", ".join(cast("list[str]", op["required_kwargs"])) or "-"
        if op["op"] == "rollup":
            required = "drop_axes|grain"
        lines.append(f"  {op['helper']:<32}required: {required}")
    lines.append("")
    lines.append(f"Example: {content['example']}")
    lines.append("")
    for note in cast("list[str]", content["notes"]):
        lines.append(note)
    return "\n".join(lines)


_SESSION_METHODS: tuple[dict[str, str], ...] = (
    {
        "name": "observe",
        "group": "intents",
        "summary": "materialize one or more same-scope semantic metrics as a MetricFrame",
    },
    {
        "name": "compare",
        "group": "intents",
        "summary": "align two MetricFrames and produce a DeltaFrame",
    },
    {
        "name": "attribute",
        "group": "intents",
        "summary": "attribute a DeltaFrame over explicit axes, materializing missing axes when replay is recoverable",
    },
    {
        "name": "correlate",
        "group": "intents",
        "summary": "correlate compatible analysis frames",
    },
    {
        "name": "forecast",
        "group": "intents",
        "summary": "forecast a time_series or panel MetricFrame",
    },
    {
        "name": "assess_quality",
        "group": "intents",
        "summary": "inspect artifact quality and produce a QualityReport",
    },
    {
        "name": "hypothesis_test",
        "group": "intents",
        "summary": "run a paired hypothesis_test over compatible MetricFrames",
    },
    {
        "name": "discover",
        "group": "namespaces/evidence",
        "summary": "objective helpers for deterministic candidate discovery",
    },
    {
        "name": "evidence",
        "group": "namespaces/evidence",
        "summary": "audit iterators for persisted findings, propositions, and assessments",
    },
    {
        "name": "knowledge",
        "group": "namespaces/evidence",
        "summary": "session knowledge snapshot: observations, established facts, open items, next steps",
    },
    {
        "name": "derive_metric_frame",
        "group": "intents",
        "summary": "governed Ibis escape hatch that returns a MetricFrame",
    },
    {
        "name": "jobs",
        "group": "lifecycle",
        "summary": "list persisted jobs for the session",
    },
    {
        "name": "recent_jobs",
        "group": "lifecycle",
        "summary": "list the most recent persisted jobs",
    },
    {
        "name": "job",
        "group": "lifecycle",
        "summary": "load one persisted job by id",
    },
    {
        "name": "is_read_only",
        "group": "lifecycle",
        "summary": "report whether the session is attached read-only",
    },
    {
        "name": "close",
        "group": "lifecycle",
        "summary": "close the session and release resources",
    },
)


_SESSION_IDENTITY_FIELDS: tuple[dict[str, str], ...] = (
    {"name": "id", "summary": "stable session id"},
    {"name": "name", "summary": "human-readable session name"},
    {"name": "question", "summary": "optional guiding analysis question"},
    {"name": "created_at", "summary": "session creation timestamp"},
    {"name": "updated_at", "summary": "last session metadata update timestamp"},
    {"name": "default_calendar", "summary": "default calendar name for time-aware operators"},
    {"name": "tz", "summary": "session report timezone (backward compat; prefer report_tz)"},
    {"name": "report_tz", "summary": "session persisted report timezone"},
    {"name": "report_tz_name", "summary": "IANA name of the session report timezone"},
    {"name": "cwd", "summary": "working directory captured when the session was created"},
    {"name": "project_root", "summary": "project root that owns the session state"},
    {"name": "catalog", "summary": "session semantic catalog for browsing project refs"},
)


def _session_content(constraints: tuple[Constraint, ...]) -> dict[str, object]:
    lifecycle = [dict(method) for method in _SESSION_METHODS if method["group"] == "lifecycle"]
    return {
        "summary": "Session object methods and namespaces advertised for agents.",
        "identity_fields": [dict(field) for field in _SESSION_IDENTITY_FIELDS],
        "lifecycle": lifecycle,
        "methods": [dict(method) for method in _SESSION_METHODS],
        "constraints": [constraint.to_summary_dict() for constraint in constraints],
        "construction": [
            "mv.session.get_or_create(...)",
            "mv.session.list()",
            "mv.session.current()",
        ],
        "frame_recovery": [
            "session.frame_summaries()",
            "session.recent_jobs(limit=5)",
            "session.get_frame(ref)",
        ],
        "audit_tools": [
            "session.knowledge()",
            "session.evidence",
        ],
        "example": (
            "session = mv.session.get_or_create(name='analysis')\n"
            "revenue = session.catalog.get('metric.orders.revenue')\n"
            "metric = session.observe(revenue, "
            "time_scope={'start': '2026-01-01', 'end': '2026-01-31'}, "
            "analysis_purpose='confirm January revenue level')"
        ),
    }


def _session_text(content: dict[str, object]) -> str:
    identity_fields = cast("list[dict[str, str]]", content["identity_fields"])
    lifecycle = cast("list[dict[str, str]]", content["lifecycle"])
    methods = cast("list[dict[str, str]]", content["methods"])
    lines = ["Construction:"]
    for step in cast("list[str]", content["construction"]):
        lines.append(f"  {step}")
    lines.extend(("", "Identity fields:"))
    for field in identity_fields:
        lines.append(f"  {field['name']:<24}{field['summary']}")
    lines.extend(("", "Lifecycle:"))
    for method in lifecycle:
        lines.append(f"  {method['name']:<24}{method['summary']}")
    lines.extend(("", "Methods:"))
    for group in ("intents", "namespaces/evidence", "expert"):
        lines.append(f"  {group}:")
        for method in methods:
            if method["group"] == group:
                lines.append(f"    {method['name']:<28}{method['summary']}")
    lines.extend(("", "Frame recovery:"))
    for step in cast("list[str]", content["frame_recovery"]):
        lines.append(f"  {step}")
    lines.extend(("", "Audit tools (not default authoring path):"))
    for tool in cast("list[str]", content["audit_tools"]):
        lines.append(f"  {tool}")
    lines.extend(("", "Example:", cast("str", content["example"])))
    return "\n".join(lines)


def _observe_content() -> dict[str, object]:
    return {
        "summary": "Build a MetricFrame from one metric or a same-scope metric list and window.",
        "multi_metric": {
            "input": (
                "metric accepts a non-empty sequence of simple, unfolded metrics "
                "over one shared scope; bare strings are rejected"
            ),
            "result": (
                "the frame carries one value column per metric (frame.measures_meta()) "
                "and one measure per metric in meta"
            ),
            "projection": "frame.metric(id) projects one metric out as an arity-1 MetricFrame",
            "arity_gate": (
                "analytical intents (compare/discover/correlate/transform/"
                "assess_quality/hypothesis_test/forecast) require arity-1 frames"
            ),
        },
        "sampled_semi_additive": {
            "fold": "sampled semi-additive metrics use their bound sampled time axis",
            "coverage": "return coverage through frame.coverage()",
            "reaggregation": "re-run observe rather than rolling up sampled folded frames",
        },
        "notes": [
            "dimensions=None or dimensions=[] means no segment axes; with grain "
            "observe returns a time_series, without grain it returns a scalar.",
            "Sampled semi-additive metrics use their bound sampled time axis, return "
            "coverage through frame.coverage(), and should be re-observed rather than "
            "rolled up.",
        ],
        "example": (
            "session.observe(\n"
            '    revenue, time_scope={"start": "2026-01-01", "end": "2026-02-01"}, '
            'analysis_purpose="confirm January revenue level"\n'
            ")"
        ),
    }


def _observe_text(content: dict[str, object]) -> str:
    lines = [
        "observe: build a MetricFrame from one metric or a same-scope metric list and window",
        "",
    ]
    multi = cast("dict[str, object]", content["multi_metric"])
    lines.append("Multi-metric:")
    lines.append(f"  input: {multi['input']}")
    lines.append(f"  result: {multi['result']}")
    lines.append(f"  projection: {multi['projection']}")
    lines.append(f"  arity gate: {multi['arity_gate']}")
    sampled = cast("dict[str, object]", content["sampled_semi_additive"])
    lines.extend(("", "Sampled semi-additive metrics:"))
    lines.append(f"  fold axis: {sampled['fold']}")
    lines.append(f"  coverage: {sampled['coverage']}")
    lines.append(f"  reaggregation: {sampled['reaggregation']}")
    lines.extend(("", "Notes:"))
    for note in cast("list[str]", content["notes"]):
        lines.append(f"  - {note}")
    lines.extend(("", "Example:", cast("str", content["example"])))
    return "\n".join(lines)


def _cumulative_frame_content() -> dict[str, object]:
    return {
        "summary": (
            "Cumulative MetricFrames carry running totals whose statistical "
            "hazards depend on the anchor: all_history (monotonic trend), "
            "trailing (rolling-window autocorrelation), grain_to_date "
            "(non-stationary period reset). contract() dispatches the caveat "
            "wording and compare affordance on the anchor."
        ),
        "allowed": [
            "show()",
            "contract()",
            "transform.window(...)",
            "transform.rollup(...) when meta.rollup_fold is set",
            "correlate (with anchor caveat in mind)",
            "discover",
            "assess_quality",
            "derive",
            "hypothesis_test (with anchor caveat in mind)",
        ],
        "conditional": [
            "compare (trailing: identical anchor; grain_to_date: single-period boundary-anchored)",
        ],
        "rejected_in_v1": [
            "compare (all_history: hard caveat)",
            "attribute",
            "decompose",
            "forecast",
        ],
        "anchor_caveats": {
            "all_history": (
                "running totals anchored to all history; shared monotonic trend "
                "can pollute correlation and hypothesis-test interpretation"
            ),
            "trailing": (
                "rolling window; rolling-series autocorrelation can pollute "
                "correlation and hypothesis-test interpretation"
            ),
            "grain_to_date": (
                "values reset at period boundaries; non-stationary within and "
                "across periods, which can pollute correlation and hypothesis-test "
                "interpretation"
            ),
        },
        "hint": (
            "Use the base flow metric for rejected intents. "
            "A cumulative delta over a window equals the base total over that window."
        ),
        "example": (
            "cum_frame = session.observe(\n"
            "    cumulative_active_users,\n"
            '    time_scope={"start": "2026-01-01", "end": "2026-04-01"},\n'
            '    grain="day",\n'
            ")\n"
            "cum_frame.contract()  # anchor-aware running_total_caveat\n"
            'windowed = cum_frame.transform.window(window={"start": "2026-02-01", "end": "2026-03-01"})\n'
            "# For compare/attribute/forecast, observe the base metric instead:\n"
            "base_frame = session.observe(active_users, ...)"
        ),
    }


def _cumulative_frame_text(content: dict[str, object]) -> str:
    allowed = cast("list[str]", content["allowed"])
    conditional = cast("list[str]", content["conditional"])
    rejected = cast("list[str]", content["rejected_in_v1"])
    anchor_caveats = cast("dict[str, str]", content["anchor_caveats"])
    lines = [
        "Cumulative MetricFrames:",
        "",
        str(content["summary"]),
        "",
        "Allowed in v1:",
    ]
    for item in allowed:
        lines.append(f"  - {item}")
    lines.extend(("", "Conditional (anchor-dispatched):"))
    for item in conditional:
        lines.append(f"  - {item}")
    lines.extend(("", "Rejected in v1:"))
    for item in rejected:
        lines.append(f"  - {item}")
    lines.extend(("", "Anchor caveats:"))
    for anchor, caveat in anchor_caveats.items():
        lines.append(f"  - {anchor}: {caveat}")
    lines.extend(("", "Hint:"))
    lines.append(f"  {content['hint']}")
    lines.extend(("", "Example:", cast("str", content["example"])))
    return "\n".join(lines)


def _workflow_content() -> dict[str, object]:
    return {
        "summary": "Default agent runbook: session, catalog discovery, route intent, read, recover.",
        "steps": [
            "import marivo.analysis as mv",
            "session = mv.session.get_or_create(...)",
            'session.catalog.list("domain").show()',
            'session.catalog.list("metric", scope="domain.<domain>").show()',
            'revenue = session.catalog.get("metric.sales.revenue")',
            'region = session.catalog.get("dimension.sales.orders.region")',
            "revenue.details().show()",
            "region.details().show()",
            "mv.help(revenue)",
            "session.catalog.readiness(refs=[revenue.ref, region.ref]).show()",
            "frame = session.observe(...)",
            "artifact.show()",
            "artifact.contract()",
            "artifact.meta.evidence_status",
            "artifact.to_pandas()",
        ],
        "intent_routing": [
            {"question": "Value of a metric in one window?", "route": "observe"},
            {"question": "Current vs baseline change?", "route": "observe x2 -> compare"},
            {"question": "Why did the metric change?", "route": "compare -> attribute"},
            {
                "question": "Spikes, drops, unusual buckets?",
                "route": "observe -> discover.<objective>",
            },
            {"question": "Two metrics move together?", "route": "observe both -> correlate"},
            {
                "question": "Mean changed between paired samples?",
                "route": "observe x2 -> hypothesis_test",
            },
            {"question": "Need a future projection?", "route": "observe series -> forecast"},
            {"question": "Need auditable quality evidence?", "route": "assess_quality"},
            {
                "question": "Custom Ibis result must re-enter typed flow?",
                "route": "derive_metric_frame",
            },
        ],
        "default_operators": [
            {"operator": "observe", "returns": "MetricFrame"},
            {"operator": "compare", "returns": "DeltaFrame"},
            {"operator": "attribute", "returns": "AttributionFrame"},
            {"operator": "discover.<objective>", "returns": "CandidateSet"},
            {"operator": "correlate", "returns": "AssociationResult"},
            {"operator": "hypothesis_test", "returns": "HypothesisTestResult"},
            {"operator": "forecast", "returns": "ForecastFrame"},
            {"operator": "derive_metric_frame", "returns": "MetricFrame"},
            {"operator": "assess_quality", "returns": "QualityReport"},
        ],
        "catalog_discovery": [
            'session.catalog.list("domain").show()',
            'session.catalog.list("metric", scope="domain.<domain>").show()',
        ],
        "read_order": [
            "artifact.show()",
            "artifact.contract()",
            "artifact.meta.evidence_status",
            "artifact.to_pandas()",
        ],
        "recovery": [
            "session.frame_summaries()",
            "session.recent_jobs(limit=5)",
            "session.get_frame(ref)",
        ],
        "quality_gate": (
            "Call session.assess_quality(artifact) when you need auditable "
            "quality evidence, before reporting, or when artifact meta is not clearly ok."
        ),
        "cumulative_note": (
            'Cumulative MetricFrames have intent gates; read mv.help("cumulative_frame") '
            "when artifact.contract() reports cumulative caveats."
        ),
        "operator_boundaries": [
            "observe/derive_metric_frame and axis-like transform/discover params consume catalog refs or objects.",
            "compare/correlate/hypothesis_test consume typed MetricFrames.",
            "attribute consumes a DeltaFrame plus catalog axes.",
        ],
        "affordance_boundary": (
            "artifact.contract().affordances are mechanical compatibility facts, "
            "not advisory endorsements from Marivo."
        ),
        "purpose_guidance": (
            "Pass analysis_purpose on artifact-producing intents so persisted "
            "frames/results remain easy to identify during session recovery."
        ),
        "see_also": [
            'mv.help("catalog")',
            'mv.help("artifacts")',
            'mv.help("recovery")',
            'mv.help("cumulative_frame")',
            'mv.help("advanced")',
        ],
    }


def _workflow_text(content: dict[str, object]) -> str:
    steps = cast("list[str]", content["steps"])
    operators = cast("list[dict[str, str]]", content["default_operators"])
    routes = cast("list[dict[str, str]]", content["intent_routing"])
    lines = ["Default agent workflow:", ""]
    for i, step in enumerate(steps, 1):
        lines.append(f"  {i}. {step}")
    lines.extend(("", "Question -> first operator:"))
    for item in routes:
        lines.append(f"  {item['question']} -> {item['route']}")
    lines.extend(("", "Default operators:"))
    for item in operators:
        lines.append(f"  session.{item['operator']:<24}-> {item['returns']}")
    lines.extend(("", "Artifact read order:"))
    for step in cast("list[str]", content["read_order"]):
        lines.append(f"  {step}")
    lines.extend(("", f"  {content['affordance_boundary']}"))
    lines.extend(("", "Quality and cumulative gates:"))
    lines.append(f"  {content['quality_gate']}")
    lines.append(f"  {content['cumulative_note']}")
    lines.extend(("", "Operator input boundaries:"))
    for boundary in cast("list[str]", content["operator_boundaries"]):
        lines.append(f"  {boundary}")
    lines.extend(("", "Purpose labels:"))
    lines.append(f"  {content['purpose_guidance']}")
    lines.extend(("", "Recovery branch (cross-script only):"))
    for fact in cast("list[str]", content["recovery"]):
        lines.append(f"  {fact}")
    lines.extend(("", "See also:"))
    for ref in cast("list[str]", content["see_also"]):
        lines.append(f"  {ref}")
    return "\n".join(lines)


def _catalog_content() -> dict[str, object]:
    return {
        "summary": "Analysis-side semantic catalog consumption.",
        "discovery": [
            'session.catalog.list("domain").show()',
            'session.catalog.list("metric", scope="domain.<domain>").show()',
            'session.catalog.list("dimension", scope="entity.<domain>.<entity>").show()',
        ],
        "drilldown": [
            'session.catalog.get("metric.<domain>.<metric>").details().show()',
        ],
        "help_hooks": [
            "mv.help(metric)",
            "mv.help(metric.ref)",
        ],
        "note": (
            "catalog.list(...) discovers refs; catalog.get(...).details().show() "
            "reads business_definition, guardrails, instructions, and other ai_context "
            "before analysis. Always pass an explicit kind and scope to catalog.list(); "
            "the no-argument form is not supported on the analysis side."
        ),
    }


def _catalog_text(content: dict[str, object]) -> str:
    discovery = cast("list[str]", content["discovery"])
    drilldown = cast("list[str]", content["drilldown"])
    hooks = cast("list[str]", content["help_hooks"])
    lines = ["Analysis-side catalog consumption:", "", "Discovery:"]
    for step in discovery:
        lines.append(f"  {step}")
    lines.extend(("", "Drilldown:"))
    for step in drilldown:
        lines.append(f"  {step}")
    lines.extend(("", "Help hooks:"))
    for hook in hooks:
        lines.append(f"  {hook}")
    lines.extend(("", f"Note: {content['note']}"))
    return "\n".join(lines)


def _artifacts_content() -> dict[str, object]:
    return {
        "summary": "Artifact read protocol: show, contract, meta status, terminal to_pandas.",
        "read_order": [
            "artifact.show()",
            "artifact.contract()",
            "artifact.to_pandas()",
        ],
        "inspection_surfaces": [
            "artifact.meta.evidence_status",
            "artifact.meta.blocking_issues",
            "artifact.meta.confidence_scope",
            "artifact.meta.quality_summary",
        ],
        "affordance_boundary": (
            "artifact.contract().affordances are mechanical compatibility facts, "
            "not advisory endorsements from Marivo."
        ),
        "note": (
            "artifact.meta is an inspection/status surface, not a data exit. "
            "Other methods on artifact objects are not default exits; "
            "use artifact.contract() to inspect available actions."
        ),
    }


def _artifacts_text(content: dict[str, object]) -> str:
    lines = ["Artifact read protocol:", "", "Read order:"]
    for step in cast("list[str]", content["read_order"]):
        lines.append(f"  {step}")
    lines.extend(("", "Inspection/status surface:"))
    for field in cast("list[str]", content["inspection_surfaces"]):
        lines.append(f"  {field}")
    lines.extend(("", f"  {content['affordance_boundary']}"))
    lines.extend(("", f"Note: {content['note']}"))
    return "\n".join(lines)


def _recovery_content() -> dict[str, object]:
    return {
        "summary": "Cross-script frame and job recovery helpers.",
        "steps": [
            "session.frame_summaries()",
            "session.recent_jobs(limit=5)",
            "session.get_frame(ref)",
        ],
        "audit": [
            "session.knowledge().observations()",
            "session.knowledge().facts()",
            "session.knowledge().open_items()",
            "session.evidence",
        ],
        "note": (
            "knowledge() and evidence are audit/recovery tools, not the default authoring path."
        ),
    }


def _recovery_text(content: dict[str, object]) -> str:
    lines = ["Recovery steps:"]
    for step in cast("list[str]", content["steps"]):
        lines.append(f"  {step}")
    lines.extend(("", "Audit tools:"))
    for tool in cast("list[str]", content["audit"]):
        lines.append(f"  {tool}")
    lines.extend(("", f"Note: {content['note']}"))
    return "\n".join(lines)


def _advanced_content() -> dict[str, object]:
    return {
        "summary": "Non-default surfaces: transform, select, cumulative_frame, contract DTO, lineage.",
        "surfaces": [
            {
                "name": "transform",
                "summary": "family-preserving reshape of a MetricFrame or DeltaFrame",
            },
            {
                "name": "select",
                "summary": "read typed fields from a CandidateSet row",
            },
            {
                "name": "cumulative_frame",
                "summary": "running-total caveats and intent gates",
            },
            {
                "name": "contract DTO",
                "summary": "ArtifactContract, ArtifactAffordance, ArtifactSchema descriptors",
            },
            {
                "name": "lineage",
                "summary": "Lineage and LineageStep provenance chain",
            },
        ],
        "disclaimer": "These are not default workflow surfaces.",
    }


def _advanced_text(content: dict[str, object]) -> str:
    surfaces = cast("list[dict[str, str]]", content["surfaces"])
    lines = ["Advanced surfaces (not default workflow):", ""]
    for surface in surfaces:
        lines.append(f"  {surface['name']:<20}{surface['summary']}")
    lines.extend(("", f"Disclaimer: {content['disclaimer']}"))
    lines.extend(("", 'Reach via mv.help("transform"), mv.help("select"), etc.'))
    return "\n".join(lines)


def _alignment_content() -> dict[str, object]:
    return {
        "summary": "mv.AlignmentPolicy variants and calendar-backed alignment columns.",
        "helpers": [
            "mv.window_bucket()",
            "mv.dow_aligned(calendar=mv.CalendarRef(...))",
            "mv.holiday_aligned(calendar=mv.CalendarRef(...))",
            "mv.holiday_and_dow_aligned(calendar=mv.CalendarRef(...))",
        ],
        "variants": [
            {
                "kind": "window_bucket",
                "calendar_required": False,
                "notes": [
                    "window_bucket default -> align by ordinal bucket position",
                    "window_bucket mode='calendar_bucket' -> outer join absolute bucket keys",
                    "strict_lengths=True -> require equal ordinal bucket counts",
                    "sparse observed buckets become NaN values rather than alignment failures",
                    "there is no separate kind='ordinal'",
                ],
            },
            {
                "kind": "dow_aligned",
                "calendar_required": True,
                "calendar_arg": "calendar=mv.CalendarRef(...)",
            },
            {
                "kind": "holiday_aligned",
                "calendar_required": True,
                "calendar_arg": "calendar=mv.CalendarRef(...)",
            },
            {
                "kind": "holiday_and_dow_aligned",
                "calendar_required": True,
                "calendar_arg": "calendar=mv.CalendarRef(...)",
            },
        ],
        "output_columns": {
            "align_key": "compact JSON object string; fields depend on kind",
            "align_quality": "exact or fallback",
            "bucket_start_a": "paired current bucket date",
            "bucket_start_b": "paired baseline bucket date",
        },
        "align_key_examples": [
            {"kind": "dow", "iso_weekday": 2, "period_week_offset": 0},
            {"kind": "holiday", "holiday_id": "labor-day", "holiday_ordinal": 1},
            {"kind": "workday", "workday_ordinal": 1},
            {"kind": "fallback_workday", "baseline_date": "2026-04-03"},
        ],
        "example": "mv.dow_aligned(calendar=mv.CalendarRef('cn_holidays'), period='month')",
    }


def _alignment_text(content: dict[str, object]) -> str:
    variants = cast("list[dict[str, object]]", content["variants"])
    examples = cast("list[dict[str, object]]", content["align_key_examples"])
    helpers = cast("list[str]", content["helpers"])
    lines = ["mv.AlignmentPolicy variants:", "", "Valid kind values:"]
    for variant in variants:
        calendar = (
            "calendar=mv.CalendarRef(...) required"
            if variant["calendar_required"]
            else "no calendar argument"
        )
        lines.append(f"  kind='{variant['kind']}' {calendar}")
    lines.extend(("", "Helper constructors:"))
    for helper in helpers:
        lines.append(f"  {helper}")
    lines.extend(("", "window_bucket behavior:"))
    for note in cast("list[str]", variants[0]["notes"]):
        lines.append(f"  {note}")
    lines.extend(("", "Calendar alignment output columns:"))
    lines.append("  align_key is a compact JSON object string; fields depend on kind")
    for example in examples:
        if example["kind"] == "dow":
            lines.append('  dow: {"kind":"dow","iso_weekday":2,"period_week_offset":0}')
        elif example["kind"] == "holiday":
            lines.append(
                '  holiday: {"kind":"holiday","holiday_id":"labor-day","holiday_ordinal":1}'
            )
        elif example["kind"] == "workday":
            lines.append('  workday: {"kind":"workday","workday_ordinal":1}')
        elif example["kind"] == "fallback_workday":
            lines.append(
                '  fallback_workday: {"kind":"fallback_workday","baseline_date":"2026-04-03"}'
            )
    lines.append("  align_quality is 'exact' or 'fallback'; bucket_start_a/b show paired dates")
    lines.append("")
    lines.append(f"Example: {content['example']}")
    return "\n".join(lines)


def _calendar_content() -> dict[str, object]:
    return {
        "summary": "project-local calendar JSON schema.",
        "location": ".marivo/calendar/<name>.json",
        "schema": {
            "name": "string matching the file stem",
            "holidays": "list[CalendarEntry]",
            "adjusted_workdays": "optional list[CalendarEntry], defaults to []",
        },
        "entry_schema": {
            "date": "ISO date string, YYYY-MM-DD",
            "holiday_id": "optional string used to match same holiday across years",
        },
        "rules": [
            "Calendar files define dates only; extra top-level fields are rejected.",
            "Extra entry fields are rejected; use holiday_id rather than name/label.",
        ],
        "example": {
            "name": "cn_holidays",
            "holidays": [{"date": "2026-05-01", "holiday_id": "labor-day"}],
            "adjusted_workdays": [{"date": "2026-05-02"}],
        },
    }


def _calendar_text(content: dict[str, object]) -> str:
    example = cast("dict[str, object]", content["example"])
    lines = ["project-local calendar JSON schema:", "", "Location:"]
    lines.append(f"  {content['location']}")
    lines.append("  The directory is created when an analysis session is created or attached.")
    lines.extend(("", "Top-level object:"))
    lines.append('  "name": string matching the file stem')
    lines.append('  "holidays": list[CalendarEntry]')
    lines.append('  "adjusted_workdays": optional list[CalendarEntry], defaults to []')
    lines.append("  Calendar files define dates only; extra top-level fields are rejected.")
    lines.extend(("", "CalendarEntry:"))
    lines.append('  "date": ISO date string, YYYY-MM-DD')
    lines.append('  "holiday_id": optional string used to match same holiday across years')
    lines.append("  Extra fields are rejected; use holiday_id rather than name/label.")
    lines.extend(("", "Example:", "{"))
    lines.append(f'  "name": "{example["name"]}",')
    lines.append('  "holidays": [')
    lines.append('    {"date": "2026-05-01", "holiday_id": "labor-day"}')
    lines.append("  ],")
    lines.append('  "adjusted_workdays": [')
    lines.append('    {"date": "2026-05-02"}')
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines)


def _topic(
    symbol: str,
    content: dict[str, object],
    doc: str,
    *,
    constraints: tuple[Constraint, ...] = (),
    signature: str | None = None,
    method_doc: str | None = None,
) -> Descriptor:
    full_doc = f"{method_doc}\n\n{doc}" if method_doc else doc
    return Descriptor(
        surface="marivo.analysis",
        kind="topic",
        symbol=symbol,
        summary=cast("str", content["summary"]),
        content=content,
        doc=full_doc,
        constraints=constraints,
        signature=signature,
    )


def _resolve(symbol: str) -> object | None:
    import marivo.analysis as mv
    import marivo.analysis.errors as errors_mod

    if hasattr(mv, symbol):
        return cast("object", getattr(mv, symbol))
    if hasattr(errors_mod, symbol):
        return cast("object", getattr(errors_mod, symbol))
    if symbol == "observe":
        from marivo.analysis.session.core import Session

        return Session.observe
    if symbol == "compare":
        from marivo.analysis.session.core import Session

        return Session.compare
    if symbol == "attribute":
        from marivo.analysis.session.core import Session

        return Session.attribute
    if symbol == "discover":
        from marivo.analysis.intents.discover import discover

        return discover
    if symbol == "transform":
        from marivo.analysis.frames.transforms import MetricFrameTransforms

        return MetricFrameTransforms
    if symbol == "select":
        from marivo.analysis.intents.select import select

        return select
    if symbol == "correlate":
        from marivo.analysis.session.core import Session

        return Session.correlate
    if symbol == "forecast":
        from marivo.analysis.session.core import Session

        return Session.forecast
    if symbol == "assess_quality":
        from marivo.analysis.session.core import Session

        return Session.assess_quality
    if symbol == "hypothesis_test":
        from marivo.analysis.session.core import Session

        return Session.hypothesis_test
    if symbol == "derive_metric_frame":
        from marivo.analysis.session.core import Session

        return Session.derive_metric_frame
    return None


def _intent_method_info(symbol: str) -> tuple[str | None, str | None]:
    obj = _resolve(symbol)
    if obj is None or not callable(obj):
        return None, None
    from marivo.introspection.describe import own_doc, signature_for

    return signature_for(symbol, obj), own_doc(obj) or None


@lru_cache(maxsize=1)
def _surface() -> Surface:
    import marivo.analysis as mv

    all_names = tuple(dict.fromkeys((*mv.__all__, *_HELP_ONLY_ENTRIES)))
    summaries = {name: _SUMMARIES.get(name, "") for name in all_names}
    catalog = {constraint.id: constraint for constraint in iter_constraints()}
    discover_content = _discover_content()
    select_content = _select_content()
    transform_content = _transform_content()
    alignment_content = _alignment_content()
    calendar_content = _calendar_content()
    workflow_content = _workflow_content()
    catalog_topic_content = _catalog_content()
    artifacts_content = _artifacts_content()
    recovery_content = _recovery_content()
    advanced_content = _advanced_content()
    observe_content = _observe_content()
    cumulative_frame_content = _cumulative_frame_content()
    observe_sig, observe_doc = _intent_method_info("observe")
    select_sig, select_doc = _intent_method_info("select")
    transform_sig, transform_doc = _intent_method_info("transform")
    session_constraints = constraints_for_symbol("session")
    session_content = _session_content(session_constraints)
    return Surface(
        name="marivo.analysis",
        all_names=all_names,
        summaries=summaries,
        resolve=_resolve,
        catalog=catalog,
        topics={
            "workflow": _topic(
                "workflow",
                workflow_content,
                _workflow_text(workflow_content),
            ),
            "catalog": _topic(
                "catalog",
                catalog_topic_content,
                _catalog_text(catalog_topic_content),
            ),
            "artifacts": _topic(
                "artifacts",
                artifacts_content,
                _artifacts_text(artifacts_content),
            ),
            "recovery": _topic(
                "recovery",
                recovery_content,
                _recovery_text(recovery_content),
            ),
            "advanced": _topic(
                "advanced",
                advanced_content,
                _advanced_text(advanced_content),
            ),
            "observe": _topic(
                "observe",
                observe_content,
                _observe_text(observe_content),
                signature=observe_sig,
                method_doc=observe_doc,
            ),
            "discover": _topic("discover", discover_content, _discover_text(discover_content)),
            "select": _topic(
                "select",
                select_content,
                _select_text(select_content),
                signature=select_sig,
                method_doc=select_doc,
            ),
            "transform": _topic(
                "transform",
                transform_content,
                _transform_text(transform_content),
                signature=transform_sig,
                method_doc=transform_doc,
            ),
            "alignment": _topic(
                "alignment",
                alignment_content,
                _alignment_text(alignment_content),
            ),
            "calendar": _topic("calendar", calendar_content, _calendar_text(calendar_content)),
            "cumulative_frame": _topic(
                "cumulative_frame",
                cumulative_frame_content,
                _cumulative_frame_text(cumulative_frame_content),
            ),
            "session": _topic(
                "session",
                session_content,
                _session_text(session_content),
                constraints=session_constraints,
            ),
        },
        frame_symbols=_FRAME_SYMBOLS,
        type_aliases=_TYPE_ALIASES,
        constructed_by=_CONSTRUCTED_BY,
        see_also=_SEE_ALSO,
        aliases=_SESSION_HELP_ALIASES,
        pinned_entries=("Session",),
    )


def _format_top_level_text() -> str:
    data = cast("dict[str, object]", render(_surface(), None, "json"))
    entries = cast("list[dict[str, str]]", data["entries"])
    lines = ["marivo.analysis - top-level entries:", ""]
    for entry in entries:
        name = entry["name"]
        label = f"help:{name}" if name in _HELP_ONLY_ENTRIES else f"mv.{name}"
        lines.append(f"  {label:<27} [{entry['kind']}]  {entry['summary']}")
    lines.extend(format_family_block(top_level_families(_surface()), help_call="mv.help"))
    lines.append("")
    lines.append('Call mv.help("<name>") for detail on any entry.')
    return "\n".join(lines)


def help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    normalized = None if symbol == "" else symbol
    if normalized is None:
        return _format_top_level_text()
    return cast("str", render(_surface(), normalized, "text"))


def help(
    target: str | SemanticRef | None = None,
    *,
    project: SemanticProject | None = None,
) -> None:
    """Print bounded help text for a Marivo analysis symbol or semantic ref.

    Args:
        target: One of:

            - None -- print top-level analysis surface help.
            - str -- print help for a named symbol or topic (e.g. "observe",
              "MetricFrame", "session").
            - SemanticRef -- print semantic-object help for an already-defined
              Python semantic authoring ref (metric, entity, etc.).
        project: Explicit SemanticProject for semantic ref resolution.
            Required when ``target`` is a ``SemanticRef`` and no project can be
            inferred from the current working directory.

    Returns:
        None

    Raises:
        SemanticError: When target is a SemanticRef and the project cannot be
            resolved (no loaded project found; pass ``project=project``).
        TypeError: When called with ``format=``, ``json=``, or other
            unsupported keyword arguments.

    Example:
        >>> mv.help()                       # top-level analysis help
        >>> mv.help("observe")              # intent help
        >>> mv.help("MetricFrame")          # frame type help
        >>> mv.help(revenue.ref, project=p) # semantic-object help
    """
    from marivo.refs import SemanticRef
    from marivo.semantic.catalog import SemanticObject

    if isinstance(target, SemanticObject):
        _help_catalog_ref(target.ref, project=project)
        return
    if isinstance(target, SemanticRef):
        _help_catalog_ref(target, project=project)
        return

    # Route "semantic.<topic>" to the semantic help surface
    if isinstance(target, str) and target.startswith("semantic."):
        semantic_symbol = target[len("semantic.") :]
        from marivo.semantic.help import help_text as ms_help_text

        print(ms_help_text(semantic_symbol or None))
        return

    normalized = None if target == "" else target
    print(help_text(normalized))


def _help_catalog_ref(
    ref: object,
    *,
    project: SemanticProject | None = None,
) -> None:
    """Resolve project and print bounded semantic-object help for a catalog ref."""
    from marivo.refs import SemanticRef
    from marivo.semantic.catalog import SemanticKind
    from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise

    if not isinstance(ref, SemanticRef):
        _raise(
            ErrorKind.INVALID_REF,
            f"mv.help expected SemanticRef or SemanticObject, got {type(ref).__name__}.",
            cls=SemanticRuntimeError,
        )

    resolved_project = project
    if resolved_project is None:
        try:
            from marivo.semantic.loader import find_project

            resolved_project = find_project()
            if resolved_project is not None:
                resolved_project.load()
        except Exception:
            resolved_project = None

    if resolved_project is None:
        _raise(
            ErrorKind.INVALID_REF,
            (
                f"Cannot resolve project for mv.help({ref.id!r}). "
                "No loaded semantic project found. "
                "Pass project=project explicitly: mv.help(ref, project=project)."
            ),
            cls=SemanticRuntimeError,
        )

    reg = getattr(resolved_project, "_registry", None)
    if reg is None:
        _raise(
            ErrorKind.INVALID_REF,
            f"Call ms.load() to load the semantic project before mv.help({ref.id!r}).",
            cls=SemanticRuntimeError,
        )

    ir = None
    if ref.kind == SemanticKind.METRIC:
        ir = reg.metrics.get(ref.id)
    elif ref.kind == SemanticKind.ENTITY:
        ir = reg.entities.get(ref.id)
    elif ref.kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
        ir = reg.dimensions.get(ref.id)
    elif ref.kind == SemanticKind.MEASURE:
        ir = reg.measures.get(ref.id)
    elif ref.kind == SemanticKind.RELATIONSHIP:
        ir = reg.relationships.get(ref.id)

    if ir is None:
        _raise(
            ErrorKind.INVALID_REF,
            (
                f"{ref.kind} {ref.id!r} not found in loaded project. "
                'Call catalog.list("metric").ids() to see available ids.'
            ),
            cls=SemanticRuntimeError,
        )

    lines = _semantic_ir_help_lines(ir, kind=str(ref.kind))
    print("\n".join(lines))


def _semantic_ir_help_lines(ir: object, *, kind: str) -> list[str]:
    typed_ir = cast("_SemanticHelpIR", ir)
    semantic_id = str(typed_ir.semantic_id)
    lines: list[str] = [
        f"{kind}: {semantic_id}",
    ]
    unit = getattr(ir, "unit", None)
    if unit:
        lines.append(f"unit: {unit}")
    ai = getattr(ir, "ai_context", None)
    if ai is not None:
        if getattr(ai, "business_definition", None):
            lines.append(f"business_definition: {ai.business_definition}")
        if getattr(ai, "guardrails", None):
            lines.append("guardrails:")
            for g in list(ai.guardrails)[:3]:
                lines.append(f"  - {g}")
        if getattr(ai, "examples", None):
            lines.append("examples:")
            for ex in list(ai.examples)[:3]:
                lines.append(f"  - {ex}")
    composition = getattr(ir, "composition", None)
    comp_kind = getattr(composition, "kind", None)
    if comp_kind == "cumulative":
        lines.extend(_cumulative_composition_briefing(composition))
    lines.append("")
    lines.append(
        'use: catalog.list("metric").ids() to enumerate; '
        f"pass catalog.get('{kind}.{semantic_id}') to session.observe(...)"
    )
    return lines


def _cumulative_composition_briefing(composition: object) -> list[str]:
    """Anchor-aware briefing lines for a CumulativeComposition IR."""
    anchor = getattr(composition, "anchor", "all_history")
    if isinstance(anchor, tuple) and anchor and anchor[0] == "trailing":
        return [
            "cumulative: trailing rolling-window",
            (
                "note: trailing values are a rolling window; rolling-series "
                "autocorrelation can pollute correlation and hypothesis-test "
                "interpretation. compare requires an identical anchor payload."
            ),
        ]
    if isinstance(anchor, tuple) and anchor and anchor[0] == "grain_to_date":
        grain = anchor[1] if len(anchor) > 1 else "?"
        return [
            f"cumulative: grain_to_date reset grain={grain}",
            (
                "note: grain_to_date values reset at period boundaries; "
                "non-stationary within and across periods. compare is conditional "
                "(single-period, boundary-anchored windows)."
            ),
        ]
    return [
        "cumulative: all_history running total",
        (
            "note: cumulative values are running totals anchored to all history; "
            "shared monotonic trend can pollute correlation and hypothesis-test "
            "interpretation. compare is gated; observe the base flow metric instead."
        ),
    ]
