"""Semantic runtime type, field and consumption contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import fields
from types import MappingProxyType

from marivo._authoring.model import AuthoringCapability
from marivo._kernel.ai_context import AiContextValue
from marivo._kernel.data_render import _display_help
from marivo._kernel.refs import SemanticKind
from marivo._kernel.render import RenderableResult
from marivo._kernel.symbolic import symbol_factory as ref_factory
from marivo.datasource.spi import PreviewResult
from marivo.introspection.capabilities.registry import TypeContractSpec
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.targets import semantic_target
from marivo.semantic._expression_values import MetricExpression, RowExpression
from marivo.semantic._help_specs.builders import _canonical_link, _target
from marivo.semantic._help_specs.manifest import REGISTRY
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.catalog_entries import (
    CatalogFilters,
    CatalogPage,
    CatalogScope,
    DatasourceDefinition,
    DomainDefinition,
    EntityDefinition,
    EntryProvenance,
    FieldDefinition,
    MetricContributionClockBinding,
    MetricContributionTimeRole,
    MetricDefinition,
    MetricNoContributionClockBinding,
    RelationshipDefinition,
)
from marivo.semantic.catalog_model.objects import (
    Datasource,
    Dimension,
    Domain,
    Entity,
    Measure,
    Metric,
    Relationship,
    TimeDimension,
)
from marivo.semantic.dtos import PreviewBatchResult
from marivo.semantic.expressions.objects import ExpressionContract
from marivo.semantic.ir import DateParse, JoinKey
from marivo.semantic.readiness import ReadinessInputSummary, ReadinessIssue, ReadinessReport
from marivo.semantic.runtime_check.results import (
    ValidationCheckResult,
    ValidationReport,
)
from marivo.semantic.runtime_check.source_checks import SourceCheckNamespace

show_render = ("show", "render")
_SHARED_DISPLAY = {
    "show": (
        RenderableResult.show,
        LiveHelpTarget(surface="analysis", canonical_id="runtime.values.show"),
    ),
    "render": (
        RenderableResult.render,
        LiveHelpTarget(surface="analysis", canonical_id="runtime.values.render"),
    ),
}


def add(
    cls: type,
    name: str,
    producers: tuple[str | LiveHelpTarget, ...],
    *,
    properties: tuple[str, ...] = (),
    methods: tuple[str, ...] = (),
    type_page_methods: tuple[str, ...] = (),
    consumers: tuple[str | LiveHelpTarget, ...] = (),
    guidance: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
    example: str | None = None,
    target: str | None = None,
) -> tuple[type, TypeContractSpec]:
    def targets(values: tuple[str | LiveHelpTarget, ...]) -> tuple[LiveHelpTarget, ...]:
        return tuple(
            _canonical_link(value) if isinstance(value, LiveHelpTarget) else _target(value)
            for value in values
        )

    return cls, TypeContractSpec(
        name=name,
        target=target,
        producers=targets(producers),
        public_properties=properties,
        public_methods=methods,
        type_page_methods=type_page_methods,
        guidance=guidance,
        consumers=targets(consumers),
        notes=notes,
        example=example,
        method_links=tuple(
            (method, target)
            for method, (implementation, target) in _SHARED_DISPLAY.items()
            if method in methods
            and method not in type_page_methods
            and getattr(cls, method) is implementation
        ),
    )


def factory_methods(canonical_id: str) -> tuple[str, ...]:
    descriptor = REGISTRY.by_canonical_id(canonical_id)
    if not isinstance(descriptor, AuthoringCapability):
        raise TypeError(f"semantic factory contract is not exact: {canonical_id}")
    return tuple((target.canonical_id or "").rsplit(".", 1)[-1] for target in descriptor.see_also)


def _entry_types() -> Iterator[tuple[type, TypeContractSpec]]:
    for entry_type in (
        Entity,
        Dimension,
        TimeDimension,
        Measure,
        Metric,
        Relationship,
    ):
        yield add(
            entry_type,
            entry_type.__name__,
            ("SemanticCatalog.get", "SemanticCatalog.search"),
            target=semantic_target(entry_type.__name__) + "_object",
            properties=(
                "id",
                "kind",
                "path",
                "name",
                "domain",
                "summary",
                "definition",
                "provenance",
            ),
            methods=(
                "related",
                "to_dict",
                "to_json",
                "show",
                "render",
                *(("count",) if entry_type is Entity else ()),
            ),
            type_page_methods=("render",),
            guidance=(
                "Use this object directly in operations allowed by its kind; .id is its canonical kind:path identity.",
            ),
        )


time_definition_guidance = {
    EntityDefinition: "default_time is the canonical TimeDimension identity declared on the Entity, or None.",
    MetricDefinition: "formula is display notation. explanation describes the captured calculation in English. time_bindings retains each contributing branch's clock or the reason it has none.",
    MetricContributionClockBinding: "source is explicit or entity_default. field, path and anchor capture the bound TimeDimension, relationship route and optional exact version anchor.",
    MetricNoContributionClockBinding: "reason identifies a source reduction with no Entity default or a fixed member count. root is the contribution Entity when there is one.",
}


def _definition_types() -> Iterator[tuple[type, TypeContractSpec]]:
    for record_type in (
        CatalogScope,
        CatalogFilters,
        EntryProvenance,
        DomainDefinition,
        DatasourceDefinition,
        EntityDefinition,
        FieldDefinition,
        MetricDefinition,
        MetricNoContributionClockBinding,
        MetricContributionTimeRole,
        MetricContributionClockBinding,
        RelationshipDefinition,
    ):
        yield add(
            record_type,
            record_type.__name__,
            ("SemanticCatalog.get",),
            properties=tuple(field.name for field in fields(record_type)),
            guidance=(
                time_definition_guidance.get(
                    record_type,
                    "Immutable captured definition data, independent of display budgets and query matching metadata.",
                ),
            ),
            example=(
                "metric = catalog.get('metric:sales.revenue')\n"
                "assert metric.kind == 'metric'\n"
                "print(metric.definition.formula)\n"
                "print(metric.definition.explanation)"
                if record_type is MetricDefinition
                else None
            ),
        )


TYPE_CONTRACTS: Mapping[type, TypeContractSpec] = MappingProxyType(
    dict(
        (
            add(
                DateParse,
                "DateParse",
                ("date",),
                properties=("timezone", "kind"),
                consumers=("time_dimension", "time_dimension_column"),
                guidance=(
                    "Immutable native DATE parser. timezone supplies business-day source authority; None uses Datasource wall_clock_timezone.",
                ),
                example="parse = ms.date(timezone='UTC')",
            ),
            add(
                SemanticCatalog,
                "SemanticCatalog",
                ("load",),
                properties=(
                    "definition_fingerprint",
                    "semantic_root",
                    "workspace_dir",
                    "load_errors",
                    "excluded_refs",
                ),
                methods=(
                    "get",
                    "search",
                    "preview",
                    "preview_many",
                    "validate",
                    "readiness",
                    "render",
                    "show",
                ),
            ),
            add(
                CatalogPage,
                "CatalogPage",
                ("SemanticCatalog.search",),
                properties=(
                    "items",
                    "candidate_count",
                    "scope",
                    "filters",
                    "retrieval",
                    "matches",
                    "next_cursor",
                    "outside_scope",
                ),
                methods=("to_dict", "to_json", "show", "render"),
                guidance=(
                    "candidate_count is the filtered total for browsing or the fixed candidate pool size for text search. next_cursor resumes API pages, independent of display truncation.",
                    "print(page) displays a bounded current-page card with Entity datasource bindings and displayed/omitted counts. show(max_output_bytes=None) displays all current-page rows. items and to_json() retain the complete captured page.",
                ),
            ),
            *_entry_types(),
            *_definition_types(),
            add(
                Domain,
                "Domain",
                ("SemanticCatalog.get", "SemanticCatalog.search"),
                target="domain_object",
                properties=(
                    "id",
                    "kind",
                    "path",
                    "name",
                    "domain",
                    "summary",
                    "definition",
                    "provenance",
                ),
                methods=("related", "to_dict", "to_json", "show", "render"),
            ),
            add(
                Datasource,
                "Datasource",
                ("SemanticCatalog.get", "SemanticCatalog.search"),
                target="datasource_object",
                properties=(
                    "id",
                    "kind",
                    "path",
                    "name",
                    "domain",
                    "summary",
                    "definition",
                    "provenance",
                ),
                methods=("related", "to_dict", "to_json", "show", "render"),
            ),
            add(
                PreviewResult,
                "PreviewResult",
                ("preview",),
                properties=(
                    "kind",
                    "columns",
                    "types",
                    "rows",
                    "requested_limit",
                    "returned_row_count",
                    "is_truncated",
                    "status",
                    "coverage",
                    "warnings",
                    "timezones",
                    "sample_policy",
                    "execution_limits",
                ),
                methods=show_render,
                type_page_methods=show_render,
                guidance=_display_help(),
            ),
            add(
                PreviewBatchResult,
                "PreviewBatchResult",
                ("preview_many",),
                properties=("status", "refs", "results"),
                methods=("show", "render"),
            ),
            add(
                ReadinessReport,
                "ReadinessReport",
                ("readiness",),
                properties=(
                    "scope",
                    "status",
                    "analysis_ready_inputs",
                    "blockers",
                    "warnings",
                    "input_summary",
                    "checked_at",
                    "catalog_definition_fingerprint",
                ),
                methods=("show", "render", "to_dict"),
            ),
            add(
                ValidationReport,
                "ValidationReport",
                ("validate",),
                properties=(
                    "status",
                    "checks",
                    "checked_at",
                    "catalog_definition_fingerprint",
                    "affected_refs",
                ),
                methods=("show", "render", "to_dict"),
            ),
            add(
                ValidationCheckResult,
                "ValidationCheckResult",
                ("validate",),
                guidance=("Read report.checks from the online validation report.",),
                properties=(
                    "kind",
                    "status",
                    "datasource",
                    "source",
                    "target_refs",
                    "affected_refs",
                    "checked_at",
                    "observed_schema_fingerprint",
                    "observed_capability_fingerprint",
                    "observed",
                    "repair",
                    "user_data_queried",
                    "scopes",
                ),
                methods=("show", "render", "to_dict"),
            ),
            add(
                SourceCheckNamespace,
                "SourceCheckNamespace",
                ("source_check",),
                guidance=(
                    "Ordinary analysis trusts semantic primary_key with nullable physical metadata; "
                    "no full-source NULL/uniqueness precheck. Existing identity-output and operation "
                    "checks remain. Use explicit source checks when those declarations need an audit.",
                ),
                methods=factory_methods("source_check"),
                consumers=("validate",),
            ),
            add(
                ReadinessInputSummary,
                "ReadinessInputSummary",
                (),
                guidance=("Read report.input_summary from the readiness report.",),
                properties=("datasources", "refs", "tables"),
                methods=("to_dict",),
            ),
            add(
                ReadinessIssue,
                "ReadinessIssue",
                (),
                guidance=("Read report.blockers and report.warnings from the readiness report.",),
                properties=(
                    "kind",
                    "severity",
                    "refs",
                    "message",
                    "repair",
                    "catalog_definition_fingerprint",
                ),
                methods=("to_dict",),
            ),
            add(
                RowExpression,
                "RowExpression",
                ("measure",),
                methods=("show", "render"),
                type_page_methods=("show", "render"),
                guidance=(
                    "Role is dimension, time, numeric or boolean. Named fields and anonymous expressions share stage-checked operations.",
                    'Use marivo.help("semantic.field"), marivo.help("semantic.row") and marivo.help("semantic.predicate") for the owning grammar.',
                    "No source reads occur during capture or display. Anonymous expressions have no Catalog identity.",
                ),
            ),
            add(
                MetricExpression,
                "MetricExpression",
                ("metric",),
                methods=("show", "render"),
                type_page_methods=("show", "render"),
                guidance=(
                    "Role is numeric or boolean. Only completed numeric Metrics can be observed; anonymous outputs require a Mapping name.",
                    'Use marivo.help("semantic.metric"), marivo.help("semantic.metric_scalar") and marivo.help("semantic.predicate") for the owning grammar.',
                    "per creates internal samples that require a reducer. Capture and display do not read sources.",
                ),
            ),
            add(
                ExpressionContract,
                "ExpressionContract",
                (),
                methods=("show",),
                notes=(
                    "Bounded source-free definition facts; analysis computation belongs to AnalysisFrame.compute()/persist().",
                ),
            ),
            add(
                type(ref_factory),
                "ref",
                (),
                methods=factory_methods("ref"),
            ),
            add(
                JoinKey,
                "JoinKey",
                ("join_on",),
                properties=("from_key", "to_key"),
            ),
            add(
                SemanticKind,
                "SemanticKind",
                (),
            ),
            add(
                AiContextValue,
                "AiContextValue",
                ("ai_context",),
                guidance=(
                    "Construct with ms.ai_context(...); annotate with ms.typing.AiContextValue.",
                ),
            ),
        )
    )
)
