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
    "agent_surface",
    "observe",
    "compare",
    "attribute",
    "discover",
    "transform",
    "correlate",
    "forecast",
    "derive_metric_frame",
    "assess_quality",
    "hypothesis_test",
    "alignment",
    "calendar",
    "select",
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
    "agent_surface": "Phase 3 default agent-facing analysis surface and artifact read protocol",
    "session": "analysis session lifecycle and persistence helpers",
    "datasources": "DEPRECATED: use marivo.datasource (md.*) for datasource registration, validation, and runtime lookup",
    "evidence": "analysis evidence DTOs and session knowledge helpers",
    "errors": "AnalysisError hierarchy and analysis error kinds",
    "frames": "analysis frame and frame metadata types",
    "observe": "build a MetricFrame from a metric and window",
    "compare": "compare two MetricFrames into a DeltaFrame",
    "attribute": "attribute a DeltaFrame into an AttributionFrame over explicit axes",
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
    "Session": "live analysis session object with execution and artifact methods",
    "SessionSummary": "lightweight row returned by mv.session.list()",
    "JobSummary": "lightweight row returned by Session.jobs() and recent_jobs()",
    "ReportRegistration": "immutable result of Session.save_report() with report id, path, entrypoint, and hash",
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
    "AbsoluteWindow": "half-open time interval [start, end) for observe timescope",
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
    "SlicePredicate": "typed dict for transform slice predicates",
    "SlicePredicateOp": "literal values for SlicePredicate.op field",
    "SliceScalar": "scalar types allowed in slice values",
    "SliceValue": "accepted value types for transform slice",
    "TimeScope": "half-open time interval model for observe",
    "TimeScopeInput": "accepted timescope input types",
    "publish": "report packaging and publishing sub-surface",
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
    "MetricFrame": ("mv.help('observe')", "mv.help('MetricFrame.components')"),
    "DeltaFrame": ("mv.help('compare')", "mv.help('attribute')"),
    "CandidateSet": ("mv.help('discover')", "mv.help('select')"),
    "AlignmentPolicy": ("mv.help('alignment')", "mv.help('calendar')"),
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
            "session.discover.driver_axes(delta, search_space=[region])"
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
        "slice": ("where",),
        "rollup": ("drop_axes",),
        "topk": ("by", "limit"),
        "bottomk": ("by", "limit"),
        "rank": ("by",),
        "normalize": ("mode",),
        "window": ("window",),
    }
    return {
        "summary": "session.transform op helper matrix (v1).",
        "ops": [
            {
                "op": op,
                "helper": f"session.transform.{op}",
                "required_kwargs": list(required_args.get(op, ())),
            }
            for op in _SUPPORTED_OPS
        ],
        "notes": [
            "normalize is MetricFrame-only in v1; DeltaFrame normalize is reserved.",
        ],
        "example": 'session.transform.topk(delta, by="delta", limit=3, order="decrease")',
    }


def _transform_text(content: dict[str, object]) -> str:
    ops = cast("list[dict[str, object]]", content["ops"])
    lines = ["session.transform op helper matrix (v1):", ""]
    for op in ops:
        required = ", ".join(cast("list[str]", op["required_kwargs"])) or "-"
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
        "summary": "materialize a semantic metric as a MetricFrame",
    },
    {
        "name": "compare",
        "group": "intents",
        "summary": "align two MetricFrames and produce a DeltaFrame",
    },
    {
        "name": "attribute",
        "group": "intents",
        "summary": "attribute a DeltaFrame over explicit deterministic axes",
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
        "name": "transform",
        "group": "expert",
        "summary": "family-preserving frame transforms for exact-control workflows",
    },
    {
        "name": "evidence",
        "group": "namespaces/evidence",
        "summary": "audit iterators for persisted findings, propositions, and assessments",
    },
    {
        "name": "knowledge",
        "group": "namespaces/evidence",
        "summary": "project-local knowledge and evidence recall helpers",
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
        "example": (
            "session = mv.session.get_or_create(name='analysis')\n"
            "revenue = session.catalog.get('metric.orders.revenue')\n"
            "metric = session.observe(revenue, "
            "timescope={'start': '2026-01-01', 'end': '2026-01-31'})"
        ),
    }


def _session_text(content: dict[str, object]) -> str:
    identity_fields = cast("list[dict[str, str]]", content["identity_fields"])
    lifecycle = cast("list[dict[str, str]]", content["lifecycle"])
    methods = cast("list[dict[str, str]]", content["methods"])
    lines = ["Identity fields:"]
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
    lines.extend(("", "Example:", cast("str", content["example"])))
    return "\n".join(lines)


def _observe_content() -> dict[str, object]:
    return {
        "summary": "Build a MetricFrame from a metric and window.",
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
    }


def _observe_text(content: dict[str, object]) -> str:
    lines = ["observe: build a MetricFrame from a metric and window", ""]
    sampled = cast("dict[str, object]", content["sampled_semi_additive"])
    lines.append("Sampled semi-additive metrics:")
    lines.append(f"  fold axis: {sampled['fold']}")
    lines.append(f"  coverage: {sampled['coverage']}")
    lines.append(f"  reaggregation: {sampled['reaggregation']}")
    lines.extend(("", "Notes:"))
    for note in cast("list[str]", content["notes"]):
        lines.append(f"  - {note}")
    return "\n".join(lines)


def _agent_surface_content() -> dict[str, object]:
    return {
        "summary": "Phase 3 default agent-facing analysis surface.",
        "core_operators": [
            {"operator": "observe", "output": "MetricFrame"},
            {"operator": "compare", "output": "DeltaFrame"},
            {"operator": "attribute", "output": "AttributionFrame"},
            {"operator": "discover.<objective>", "output": "CandidateSet"},
            {"operator": "correlate", "output": "AssociationResult"},
            {"operator": "hypothesis_test", "output": "HypothesisTestResult"},
            {"operator": "forecast", "output": "ForecastFrame"},
            {"operator": "derive_metric_frame", "output": "MetricFrame"},
            {"operator": "assess_quality", "output": "QualityReport"},
        ],
        "artifact_protocol": [
            "ref",
            "kind",
            "show()",
            "contract()",
            "quality_summary",
            "blocking_issues",
            "lineage",
            "state",
            "to_pandas()",
        ],
        "tabular_reads": ["to_pandas()"],
        "read_order": [
            "repr(artifact)",
            "artifact.show()",
            "artifact.contract()",
            "artifact.to_pandas()",
        ],
        "recovery": [
            "session.frame_summaries()",
            "session.recent_jobs(limit=5)",
            "session.get_frame(ref)",
        ],
        "derive_boundary": (
            "derive_metric_frame uses semantic refs for metric and axis bindings; "
            "query output columns are plain strings. Unbound columns in the build "
            "output are retained as-is; project within build to limit the column set."
        ),
        "quality_boundary": (
            "quality_summary is a cheap persisted metadata projection; "
            "session.assess_quality(artifact) runs an explicit auditable quality operator."
        ),
        "affordance_boundary": (
            "artifact.contract().affordances are mechanical compatibility facts, "
            "not advisory endorsements from Marivo."
        ),
        "expert_reference": ["transform", "select", "evidence", "knowledge"],
    }


def _agent_surface_text(content: dict[str, object]) -> str:
    core = cast("list[dict[str, str]]", content["core_operators"])
    lines = ["Default agent-facing operators:", ""]
    for item in core:
        lines.append(f"  session.{item['operator']:<24}-> {item['output']}")
    lines.extend(("", "Base artifact protocol:"))
    lines.append("  " + ", ".join(cast("list[str]", content["artifact_protocol"])))
    lines.extend(("", "Bounded read order:"))
    for step in cast("list[str]", content["read_order"]):
        lines.append(f"  {step}")
    lines.extend(("", "Terminal escape hatch:"))
    lines.append("  " + ", ".join(cast("list[str]", content["tabular_reads"])))
    lines.extend(("", "Quality boundary:"))
    lines.append(f"  {content['quality_boundary']}")
    lines.extend(("", "Derive boundary:"))
    lines.append(f"  {content['derive_boundary']}")
    lines.extend(("", "Affordances:"))
    lines.append(f"  {content['affordance_boundary']}")
    lines.extend(("", "Cross-script recovery facts:"))
    for fact in cast("list[str]", content["recovery"]):
        lines.append(f"  {fact}")
    lines.extend(("", "Advanced reference, not the default path:"))
    lines.append("  " + ", ".join(cast("list[str]", content["expert_reference"])))
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
) -> Descriptor:
    return Descriptor(
        surface="marivo.analysis",
        kind="topic",
        symbol=symbol,
        summary=cast("str", content["summary"]),
        content=content,
        doc=doc,
        constraints=constraints,
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
        from marivo.analysis.intents.transform import transform

        return transform
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
    agent_surface_content = _agent_surface_content()
    observe_content = _observe_content()
    session_constraints = constraints_for_symbol("session")
    session_content = _session_content(session_constraints)
    return Surface(
        name="marivo.analysis",
        all_names=all_names,
        summaries=summaries,
        resolve=_resolve,
        catalog=catalog,
        topics={
            "agent_surface": _topic(
                "agent_surface",
                agent_surface_content,
                _agent_surface_text(agent_surface_content),
            ),
            "observe": _topic("observe", observe_content, _observe_text(observe_content)),
            "discover": _topic("discover", discover_content, _discover_text(discover_content)),
            "select": _topic("select", select_content, _select_text(select_content)),
            "transform": _topic(
                "transform",
                transform_content,
                _transform_text(transform_content),
            ),
            "alignment": _topic(
                "alignment",
                alignment_content,
                _alignment_text(alignment_content),
            ),
            "calendar": _topic("calendar", calendar_content, _calendar_text(calendar_content)),
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
                "Call catalog.list(kind=ms.SemanticKind.METRIC).ids() to see available ids."
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
    lines.append("")
    lines.append(
        "use: catalog.list(kind=ms.SemanticKind.METRIC).ids() to enumerate; "
        f"pass catalog.get('{kind}.{semantic_id}') to session.observe(...)"
    )
    return lines
