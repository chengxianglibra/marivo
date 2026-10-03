"""Precisely bounded R3.4 consumers; declarations are not invocation evidence."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.model import CheckId, DomainKind, PartRole
from marivo.analysis.core.rules import (
    AnchorBind,
    AnchorObserve,
    AnchorRetention,
    AssociationScore,
    AttachCategory,
    AttributionDerive,
    BindProject,
    CellDerive,
    CompleteGroups,
    DisplayRank,
    DisplayTable,
    FunnelAttribute,
    FunnelAxesPrepare,
    FunnelCompare,
    FunnelRead,
    FunnelReduce,
    HistoryAxesPrepare,
    HistoryRead,
    HistoryReplay,
    HistoryView,
    JourneyCompleted,
    JourneyDuration,
    JourneyMatch,
    JourneyRead,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrenceCombine,
    OccurrencePrepare,
    OriginalRatio,
    OriginalReduce,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
    RetentionBySubject,
    RowState,
    RuleParameters,
    TimeProduct,
)
from marivo.analysis.methods.errors import reject
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarName,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey

PARTS: tuple[PartRole, ...] = (
    "retention",
    "anchor",
    "history",
    "history_view",
    "funnel_state",
    "finding_policy",
    "occurrences",
    "basis",
    "allocation",
    "reconciliation",
    "selection_scope",
    "values",
    "ranks",
    "ranking_domain",
    "partitions",
    "ordering",
    "columns",
    "column_bindings",
    "fixed_reference",
    "reference_proof",
    "strata",
    "stratum_values",
    "subject",
    "original_state",
    "row_state",
    "coverage",
    "allocation_state",
    "coordinate_state",
)
CHECKS: tuple[CheckId, ...] = (
    "source.unique_key@v1",
    "source.exact_pairing@v1",
    "source.cell_policy@v1",
    "source.group_mapping@v1",
    "source.contribution_partition@v1",
    "source.complete_coverage@v1",
    "source.calendar_members@v1",
    "source.calendar_contributions@v1",
)
NUMERIC_CHECKS: tuple[CheckId, ...] = (
    *CHECKS,
    "source.finite_numeric@v1",
    "source.single_value@v1",
)


def _shape_implementations(method: MethodKey) -> tuple[Implementation, ...]:
    if method.name.startswith("attribution."):
        return tuple(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"),) * 2,
                    (domain,) * 2,
                    shape,
                    "artifact_python" if isinstance(shape, FixedShape) else "ibis_python",
                ),
                NUMERIC_CHECKS,
                (*PARTS, "current_endpoint", "baseline_endpoint", "correspondence"),
                "exact",
                ResourceRequirements(
                    "complete", "caller" if isinstance(shape, FixedShape) else "producer", None
                ),
                Qualified(
                    f"r66.{method}.{domain}.{shape}",
                    "analysis.materialization.graph_attribution",
                    "tests/test_analysis_attribution_runtime_r66.py",
                ),
            )
            for domain in ("entity", "group", "singleton")
            for shape in (
                SourceShape("duckdb", "table", "native", NoTime()),
                SourceShape("duckdb", "parquet", "parquet", NoTime()),
                SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
                SourceShape("duckdb", "parquet", "parquet", TimeShape("instant", "us", "UTC")),
                FixedShape(NoTime()),
            )
        )
    if method.name.startswith("display."):
        return tuple(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"),),
                    (domain,),
                    shape,
                    "artifact_python" if isinstance(shape, FixedShape) else "ibis_python",
                ),
                NUMERIC_CHECKS,
                (*PARTS, "current_endpoint", "baseline_endpoint", "correspondence", "pair_counts"),
                "exact",
                ResourceRequirements(
                    "complete", "caller" if isinstance(shape, FixedShape) else "producer", None
                ),
                Qualified(
                    f"r65.{method}.{domain}.{shape}",
                    "analysis.materialization.graph_display",
                    "tests/test_analysis_display_r65.py",
                ),
            )
            for domain in ("entity", "group", "singleton")
            for shape in (
                SourceShape("duckdb", "table", "native", NoTime()),
                SourceShape("duckdb", "parquet", "parquet", NoTime()),
                SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
                SourceShape("duckdb", "parquet", "parquet", TimeShape("instant", "us", "UTC")),
                FixedShape(NoTime()),
            )
        )
    if method.name.startswith("reference."):
        shapes: tuple[SourceShape | FixedShape, ...] = (
            SourceShape("duckdb", "table", "native", NoTime()),
            SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
            SourceShape("duckdb", "parquet", "parquet", NoTime()),
            SourceShape("duckdb", "parquet", "parquet", TimeShape("instant", "us", "UTC")),
            FixedShape(NoTime()),
            FixedShape(TimeShape("instant", "us", "UTC")),
        )
        domains: tuple[tuple[DomainKind, ...], ...] = (
            (
                ("entity", "singleton", "entity"),
                ("group", "singleton", "group"),
                ("singleton", "singleton", "singleton"),
            )
            if method.name == "reference.share"
            else (("entity", "entity"),)
            if method.name == "reference.penetration"
            else (("group", "group"),)
        )
        return tuple(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"),) * len(domain),
                    domain,
                    shape,
                    "artifact_python" if isinstance(shape, FixedShape) else "ibis_python",
                ),
                NUMERIC_CHECKS,
                ("fixed_reference", "reference_proof", "stratum_values", "strata"),
                "exact",
                ResourceRequirements(
                    "complete", "caller" if isinstance(shape, FixedShape) else "producer", None
                ),
                Qualified(
                    f"r64.{method}.{shape}.{domain}@v1",
                    "analysis.materialization.graph_reference",
                    "tests/test_analysis_references_r64.py",
                ),
                contract_version=1,
            )
            for shape in shapes
            for domain in domains
        )
    if method.name == "domain.cohort":
        return tuple(
            replace(
                item,
                key=replace(item.key, method=method),
                parts=("subject", "cohort_decision"),
                contract_version=1,
                qualification=Qualified(
                    f"r63.{item.key.route}.domain.cohort.{item.key.shape}@v1",
                    "analysis.compiler.graph_lowering"
                    if item.key.route == "ibis"
                    else "analysis.materialization.graph_local_execution",
                    "tests/test_analysis_cohort_r63.py",
                ),
            )
            for item in _shape_implementations(MethodKey("parts_transport"))
            if item.key.input_types == (ScalarType("int64"),)
            and item.key.input_domains == ("entity",)
            and (isinstance(item.key.shape, FixedShape) or item.key.shape.backend == "duckdb")
        )
    declarations = _implementations(method)
    if method.name not in (
        "parts_transport",
        "bind_project",
        "time.product",
        "metric.min",
        "metric.max",
        "metric.observe",
        "metric.fold",
        "metric.sum_zero",
        "metric.count",
        "state_rollup.min",
        "state_rollup.max",
        "state_rollup",
        "state_rollup.sum_zero",
        "state_rollup.count",
    ):
        return declarations
    sqlite = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="sqlite")),
            qualification=Qualified(
                f"r55.sqlite.{method}.{item.key}",
                "analysis.compiler.graph_lowering",
                "tests/test_sqlite_semantic_integration.py",
            ),
        )
        for item in declarations
        if isinstance(item.key.shape, SourceShape)
        and item.key.shape.backend == "duckdb"
        and item.key.shape.form == "table"
        and item.key.route == "ibis"
    )
    return (*declarations, *sqlite)


def _implementations(method: MethodKey) -> tuple[Implementation, ...]:
    if method.name == "time.product":
        time_forms: tuple[tuple[Literal["table", "parquet"], str], ...] = (
            ("table", "native"),
            ("parquet", "parquet"),
        )
        return tuple(
            Implementation(
                QualificationKey(method, (ScalarType(value),), ("entity",), shape, "ibis"),
                CHECKS,
                PARTS,
                "exact",
                ResourceRequirements("stream", "producer", None),
                Qualified(
                    f"r55.time.product.{value}.{shape}",
                    "analysis.compiler.graph_lowering",
                    "tests/test_analysis_temporal_r55.py",
                ),
            )
            for value in ("string", "int64")
            for form, source_kind in time_forms
            for temporal in (NoTime(), TimeShape("instant", "us", "UTC"))
            for shape in (SourceShape("duckdb", form, source_kind, temporal),)
        )
    if method.name in ("group.attach", "group.complete"):
        classification_forms: tuple[tuple[Literal["table", "parquet"], str], ...] = (
            ("table", "native"),
            ("parquet", "parquet"),
        )
        classification_shapes: tuple[SourceShape | FixedShape, ...] = (
            *(
                SourceShape("duckdb", form, kind, time)
                for form, kind in classification_forms
                for time in (NoTime(), TimeShape("instant", "us", "UTC"))
            ),
            FixedShape(NoTime()),
            FixedShape(TimeShape("instant", "us", "UTC")),
        )
        return tuple(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType(value), ScalarType(category_type)),
                    (domain, target_domain),
                    shape,
                    "artifact_python" if isinstance(shape, FixedShape) else "ibis",
                ),
                NUMERIC_CHECKS,
                PARTS,
                "exact",
                ResourceRequirements("complete", "caller", None)
                if isinstance(shape, FixedShape)
                else ResourceRequirements("stream", "producer", None),
                Qualified(
                    f"r54.{method}.{value}.{category_type}.{domain}.{target_domain}.{shape}",
                    "analysis.materialization.graph_local_execution"
                    if isinstance(shape, FixedShape)
                    else "analysis.compiler.graph_lowering",
                    "tests/test_analysis_coordinates_r54.py",
                ),
            )
            for value in ("int64", "float64", "string", "boolean", "date", "timestamp")
            for category_type in ("string", "int64")
            for domain in (
                ("entity", "group", "singleton")
                if method.name == "group.complete"
                else ("entity", "group")
            )
            for target_domain in (
                ("entity", "group", "singleton") if method.name == "group.complete" else ("entity",)
            )
            for shape in classification_shapes
        )
    if method.name in (
        "metric.ratio",
        "state_rollup.ratio",
        "state_rollup.weighted_mean",
        "state_rollup.mean",
    ):
        ratio_shapes: tuple[SourceShape | FixedShape, ...] = (
            SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
            SourceShape("duckdb", "parquet", "parquet", TimeShape("instant", "us", "UTC")),
        )
        if method.name in ("state_rollup.ratio", "state_rollup.weighted_mean", "state_rollup.mean"):
            ratio_shapes += (FixedShape(NoTime()), FixedShape(TimeShape("instant", "us", "UTC")))
        return tuple(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"), ScalarType("int64"))
                    if method.name == "metric.ratio"
                    else (ScalarType("float64"),),
                    (domain, domain) if method.name == "metric.ratio" else (domain,),
                    shape,
                    "artifact_python" if isinstance(shape, FixedShape) else "ibis",
                ),
                NUMERIC_CHECKS,
                PARTS,
                "finite_float64",
                ResourceRequirements("complete", "caller", None)
                if isinstance(shape, FixedShape)
                else ResourceRequirements("stream", "producer", None),
                Qualified(
                    f"r45.{method}.{shape}.{domain}@v1",
                    "analysis.materialization.graph_local_execution"
                    if isinstance(shape, FixedShape)
                    else "analysis.compiler.graph_lowering",
                    "tests/test_analysis_graph_preflight_r45.py",
                ),
            )
            for shape in ratio_shapes
            for domain in ("entity", "group")
        )
    if method.name == "metric.linear":
        linear_shapes: tuple[SourceShape | FixedShape, ...] = (
            SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
            SourceShape("duckdb", "parquet", "parquet", TimeShape("instant", "us", "UTC")),
        )
        return tuple(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"),) * 2,
                    (domain,) * 2,
                    shape,
                    "artifact_python" if isinstance(shape, FixedShape) else "ibis",
                ),
                NUMERIC_CHECKS,
                PARTS,
                "checked_int64",
                ResourceRequirements("complete", "caller", None)
                if isinstance(shape, FixedShape)
                else ResourceRequirements("stream", "producer", None),
                Qualified(
                    f"r53.{method}.{shape}.{domain}@v1",
                    "analysis.materialization.graph_local_execution"
                    if isinstance(shape, FixedShape)
                    else "analysis.compiler.graph_lowering",
                    "tests/test_analysis_observation_r53.py",
                ),
            )
            for shape in linear_shapes
            for domain in ("entity", "group")
        )
    if method.name in (
        "state_rollup.min",
        "state_rollup.max",
        "state_rollup",
        "state_rollup.count",
        "state_rollup.sum_zero",
        "state_rollup.linear",
        "state_rollup.fold",
    ):
        rollup_types: tuple[Literal["int64", "float64"], ...] = (
            ("int64",)
            if method.name in ("state_rollup.count", "state_rollup.linear")
            else ("int64", "float64")
        )
        rollup_shapes: tuple[SourceShape | FixedShape, ...] = (
            SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC")),
            SourceShape("duckdb", "parquet", "parquet", TimeShape("instant", "us", "UTC")),
            FixedShape(NoTime()),
            FixedShape(TimeShape("instant", "us", "UTC")),
        )
        return tuple(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType(value_type),),
                    (domain,),
                    shape,
                    "artifact_python" if isinstance(shape, FixedShape) else "ibis",
                ),
                NUMERIC_CHECKS,
                PARTS,
                "checked_int64" if value_type == "int64" else "finite_float64",
                ResourceRequirements("complete", "caller", None)
                if isinstance(shape, FixedShape)
                else ResourceRequirements("stream", "producer", None),
                Qualified(
                    f"r45.rollup.{shape}.{value_type}.{domain}@v1",
                    "analysis.materialization.graph_local_execution"
                    if isinstance(shape, FixedShape)
                    else "analysis.compiler.graph_lowering",
                    "tests/test_analysis_graph_preflight_r45.py",
                ),
            )
            for shape in rollup_shapes
            for value_type in rollup_types
            for domain in ("entity", "group", "singleton")
        )
    if method.name in (
        "metric.distinct",
        "metric.approx_distinct",
        "metric.quantile",
        "metric.approx_quantile",
        "metric.min",
        "metric.max",
        "metric.observe",
        "metric.fold",
        "metric.mean",
        "metric.count",
        "metric.sum_zero",
        "metric.weighted_mean",
    ):
        observation_shapes: tuple[tuple[Literal["table", "parquet"], str], ...] = (
            ("table", "native"),
            ("parquet", "parquet"),
        )
        return tuple(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType(key_type),),
                    ("entity",),
                    SourceShape("duckdb", form, table_kind, TimeShape("instant", "us", "UTC")),
                    "ibis",
                ),
                NUMERIC_CHECKS,
                PARTS,
                "exact",
                ResourceRequirements("stream", "producer", None),
                Qualified(
                    f"r56.{method}.duckdb_native_sql.{form}.{key_type}@v1"
                    if method.name
                    in (
                        "metric.distinct",
                        "metric.approx_distinct",
                        "metric.quantile",
                        "metric.approx_quantile",
                        "metric.min",
                        "metric.max",
                    )
                    else f"r45.{method}.{form}.{key_type}@v1",
                    "analysis.compiler.graph_lowering",
                    "tests/test_analysis_graph_preflight_r45.py",
                ),
            )
            for form, table_kind in observation_shapes
            for key_type in ("int64", "string")
        )
    if method.name == "association.spearman":
        pair_checks: tuple[CheckId, ...] = (*NUMERIC_CHECKS,)
        pair_parts: tuple[PartRole, ...] = (*PARTS, "pair_counts")
        pair_declarations: list[Implementation] = []
        pair_shapes: tuple[tuple[Literal["table", "parquet"], str], ...] = (
            ("table", "native"),
            ("parquet", "parquet"),
        )
        scalar_names: tuple[Literal["int64", "float64"], ...] = ("int64", "float64")
        for form, table_kind in pair_shapes:
            shape = SourceShape("duckdb", form, table_kind, NoTime())
            for left in scalar_names:
                for right in scalar_names:
                    for route in ("ibis", "ibis_python"):
                        pair_declarations.append(
                            Implementation(
                                QualificationKey(
                                    method,
                                    (ScalarType(left), ScalarType(right)),
                                    ("entity", "entity"),
                                    shape,
                                    route,
                                ),
                                pair_checks,
                                pair_parts,
                                "finite_float64",
                                ResourceRequirements(
                                    "complete" if route == "ibis_python" else "stream",
                                    "producer",
                                    None,
                                ),
                                Qualified(
                                    f"r43.spearman.{route}.{left}.{right}@v1",
                                    "analysis.compiler.graph_lowering",
                                    "tests/test_analysis_lowering_r34.py",
                                ),
                            )
                        )
        for left in scalar_names:
            for right in scalar_names:
                pair_declarations.append(
                    Implementation(
                        QualificationKey(
                            method,
                            (ScalarType(left), ScalarType(right)),
                            ("entity", "entity"),
                            FixedShape(NoTime()),
                            "artifact_python",
                        ),
                        ("source.exact_pairing@v1", "source.finite_numeric@v1"),
                        pair_parts,
                        "finite_float64",
                        ResourceRequirements("complete", "caller", None),
                        Qualified(
                            f"r43.spearman.fixed.{left}.{right}@v1",
                            "analysis.methods.local",
                            "tests/test_analysis_lowering_r34.py",
                        ),
                    )
                )
        temporal_pairs = tuple(
            replace(
                item,
                key=replace(
                    item.key, shape=replace(item.key.shape, time=TimeShape("instant", "us", "UTC"))
                ),
            )
            for item in pair_declarations
            if isinstance(item.key.shape, SourceShape)
        )
        return (*pair_declarations, *temporal_pairs)
    if method.name not in (
        "bind_project",
        "cell.difference",
        "cell.relative_change",
        "cell.ratio",
        "parts_transport",
        "map_correspond",
        "row.count",
        "row.count_defined",
        "row.sum",
        "row.mean",
        "row.min",
        "row.max",
    ):
        return ()
    declarations: list[Implementation] = []
    shapes: tuple[tuple[Literal["table", "parquet"], str], ...] = (
        ("table", "native"),
        ("parquet", "parquet"),
    )
    domain: DomainKind = "entity"
    for form, table_kind in shapes:
        shape = SourceShape("duckdb", form, table_kind, NoTime())
        for arity in (
            (2,)
            if method.name == "cell.difference"
            else (1, 2)
            if method.name == "map_correspond"
            else (1,)
        ):
            key_types: tuple[ScalarName, ...] = (
                ("int64", "string", "float64", "boolean", "date", "timestamp")
                if method.name in ("bind_project", "parts_transport", "map_correspond")
                else ("int64",)
            )
            for key_type in key_types:
                declarations.append(
                    Implementation(
                        QualificationKey(
                            method,
                            (ScalarType(key_type),) * arity,
                            (domain,) * arity,
                            shape,
                            "ibis",
                        ),
                        NUMERIC_CHECKS,
                        (*PARTS, "current_endpoint", "baseline_endpoint", "correspondence")
                        if method.name in ("cell.difference", "parts_transport")
                        else PARTS,
                        "finite_float64"
                        if method.name == "row.mean"
                        else "checked_int64"
                        if method.name.startswith("row.") or method.name == "cell.difference"
                        else "exact",
                        ResourceRequirements("stream", "producer", None),
                        Qualified(
                            f"r52.ibis.{method}.{key_type}"
                            if method.name == "bind_project" or key_type not in ("int64", "string")
                            else f"r45.ibis.{method}.int64@v1"
                            if method.name == "cell.difference"
                            else f"r34.ibis.{method}"
                            if key_type == "int64"
                            else f"r45.ibis.{method}.{key_type}@v1",
                            "analysis.compiler.graph_lowering",
                            "tests/test_analysis_members_r52.py"
                            if method.name == "bind_project" or key_type not in ("int64", "string")
                            else "tests/test_analysis_graph_preflight_r45.py"
                            if method.name == "cell.difference"
                            else "tests/test_analysis_lowering_r34.py"
                            if key_type == "int64"
                            else "tests/test_analysis_graph_preflight_r45.py",
                        ),
                    )
                )
    if method.name in (
        "row.count",
        "row.count_defined",
        "row.sum",
        "row.mean",
        "row.min",
        "row.max",
    ):
        declarations.append(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"),),
                    ("entity",),
                    FixedShape(NoTime()),
                    "artifact_python",
                ),
                NUMERIC_CHECKS,
                ("row_state",),
                "finite_float64" if method.name == "row.mean" else "checked_int64",
                ResourceRequirements(
                    "complete", "caller", 100_000 if method.name == "row.count" else None
                ),
                Qualified(
                    "r34.local.row.count@v1"
                    if method.name == "row.count"
                    else f"r43.local.{method}@v1",
                    "analysis.methods.local",
                    "tests/test_analysis_lowering_r34.py",
                ),
            )
        )
    if method.name == "cell.difference":
        declarations.append(
            Implementation(
                QualificationKey(
                    method,
                    (ScalarType("int64"), ScalarType("int64")),
                    ("entity", "entity"),
                    FixedShape(NoTime()),
                    "artifact_python",
                ),
                ("source.unique_key@v1", "source.exact_pairing@v1", "source.finite_numeric@v1"),
                ("subject", "current_endpoint", "baseline_endpoint", "correspondence"),
                "checked_int64",
                ResourceRequirements("complete", "caller", None),
                Qualified(
                    "r45.local.cell.difference.int64@v1",
                    "analysis.materialization.graph_local_execution",
                    "tests/test_analysis_graph_publication_r44.py",
                ),
            )
        )
    if method.name in ("parts_transport", "map_correspond"):
        fixed_shapes: tuple[tuple[ScalarName, DomainKind], ...] = (
            ("int64", "entity"),
            ("string", "entity"),
            ("float64", "entity"),
            ("boolean", "entity"),
            ("date", "entity"),
            ("timestamp", "entity"),
            ("float64", "singleton"),
        )
        for name, domain_kind in fixed_shapes:
            if method.name == "map_correspond" and domain_kind != "entity":
                continue
            declarations.append(
                Implementation(
                    QualificationKey(
                        method,
                        (ScalarType(name),),
                        (domain_kind,),
                        FixedShape(NoTime()),
                        "artifact_python",
                    ),
                    NUMERIC_CHECKS,
                    (
                        *PARTS,
                        "current_endpoint",
                        "baseline_endpoint",
                        "correspondence",
                        "pair_counts",
                    ),
                    "exact",
                    ResourceRequirements("complete", "caller", None),
                    Qualified(
                        f"r52.local.{method}.{name}.{domain_kind}"
                        if method.name == "map_correspond"
                        or name in ("boolean", "date", "timestamp")
                        else f"r45.local.parts_transport.{name}.{domain_kind}@v1",
                        "analysis.materialization.graph_local_execution",
                        "tests/test_analysis_members_r52.py"
                        if method.name == "map_correspond"
                        or name in ("boolean", "date", "timestamp")
                        else "tests/test_analysis_graph_publication_r44.py",
                    ),
                )
            )
    if method.name in (
        "row.count",
        "row.count_defined",
        "row.sum",
        "row.mean",
        "row.min",
        "row.max",
    ):
        numeric_types: tuple[ScalarName, ...] = (
            ("int64", "float64", "string", "boolean", "date", "timestamp")
            if method.name in ("row.count", "row.count_defined")
            else ("int64", "float64")
        )
        row_domains: tuple[DomainKind, ...] = ("entity", "group", "singleton")
        templates = tuple(declarations)
        for template in templates:
            for numeric_type in numeric_types:
                for row_domain in row_domains:
                    key = replace(
                        template.key,
                        input_types=(ScalarType(numeric_type),),
                        input_domains=(row_domain,),
                    )
                    if any(existing.key == key for existing in declarations):
                        continue
                    declarations.append(
                        replace(
                            template,
                            key=key,
                            precision="finite_float64"
                            if method.name == "row.mean"
                            or (
                                method.name in ("row.sum", "row.min", "row.max")
                                and numeric_type == "float64"
                            )
                            else "checked_int64",
                            qualification=Qualified(
                                f"r45.{method}.{key.shape}.{numeric_type}.{row_domain}@v1",
                                "analysis.materialization.graph_local_execution"
                                if isinstance(key.shape, FixedShape)
                                else "analysis.compiler.graph_lowering",
                                "tests/test_analysis_graph_preflight_r45.py",
                            ),
                        )
                    )
    if method.name == "parts_transport":
        unary = tuple(declarations)
        for item in unary:
            if item.key.input_types[0] not in (ScalarType("int64"), ScalarType("float64")):
                continue
            for predicate_type in ("int64", "float64"):
                declarations.append(
                    replace(
                        item,
                        key=replace(
                            item.key,
                            input_types=(*item.key.input_types, ScalarType(predicate_type)),
                            input_domains=(*item.key.input_domains, *item.key.input_domains),
                        ),
                        qualification=Qualified(
                            f"r54.where.corresponding.{item.key}.{predicate_type}",
                            "analysis.materialization.graph_local_execution"
                            if isinstance(item.key.shape, FixedShape)
                            else "analysis.compiler.graph_lowering",
                            "tests/test_analysis_coordinates_r54.py",
                        ),
                    )
                )
    if method.name == "parts_transport":
        transport_shapes: tuple[tuple[Literal["int64", "float64"], DomainKind], ...] = (
            ("float64", "entity"),
            ("float64", "group"),
            ("float64", "singleton"),
            ("int64", "group"),
            ("int64", "singleton"),
        )
        for template in tuple(declarations):
            if template.key.input_types != (ScalarType("int64"),):
                continue
            for numeric_type, target_domain in transport_shapes:
                key = replace(
                    template.key,
                    input_types=(ScalarType(numeric_type),),
                    input_domains=(target_domain,),
                )
                if any(existing.key == key for existing in declarations):
                    continue
                declarations.append(
                    replace(
                        template,
                        key=key,
                        qualification=Qualified(
                            f"r45.transport.{key.shape}.{numeric_type}.{target_domain}@v1",
                            "analysis.materialization.graph_local_execution"
                            if isinstance(key.shape, FixedShape)
                            else "analysis.compiler.graph_lowering",
                            "tests/test_analysis_graph_preflight_r45.py",
                        ),
                    )
                )
    temporal = tuple(
        replace(
            implementation,
            key=replace(
                implementation.key,
                shape=replace(implementation.key.shape, time=TimeShape("instant", "us", "UTC")),
            ),
        )
        for implementation in declarations
        if isinstance(implementation.key.shape, SourceShape)
    )
    return (*declarations, *temporal)


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    """Version typed folds and once-rounded numeric consumers in Store 7."""
    if method.name in ("anchor.retention", "retention.by_subject"):
        from marivo.analysis.methods.retention_physical import implementations as retention

        return retention(method)
    if method.name.startswith("anchor."):
        from marivo.analysis.methods.anchor_physical import implementations as anchors

        return anchors(method)
    if method.name.startswith("funnel.") or method.name == "funnel_ratio_mix":
        from marivo.analysis.methods.funnel_physical import (
            implementations as funnel_implementations,
        )

        return funnel_implementations(method)
    if method.name.startswith("history.") and method.name != "history.replay":
        from marivo.analysis.methods.history_view_physical import implementations as history_views

        return history_views(method)
    if method.name == "history.replay":
        from marivo.analysis.methods.history_physical import (
            implementations as history_implementations,
        )

        return history_implementations(method)
    if method.name.startswith("journey."):
        from marivo.analysis.methods.journey_physical import (
            implementations as journey_implementations,
        )

        return journey_implementations(method)
    if method.name == "occurrence.prepare":
        from marivo.analysis.methods.domain_preparation import (
            implementations as domain_implementations,
        )

        return domain_implementations(method)
    if method.name in ("cell.relative_change", "cell.ratio"):
        return tuple(
            replace(
                item,
                key=replace(
                    item.key,
                    method=method,
                    route="ibis_python" if item.key.route == "ibis" else item.key.route,
                ),
                precision="finite_float64",
                qualification=Qualified(
                    f"r62.{method}.{item.key.shape}@v1",
                    "analysis.materialization.graph_source_execution"
                    if item.key.route == "ibis"
                    else "analysis.materialization.graph_local_execution",
                    "tests/test_analysis_comparison_runtime_r62.py",
                ),
            )
            for item in implementations(MethodKey("cell.difference"))
        )
    from marivo.analysis.methods.anchor_physical import consumers as anchor_consumers
    from marivo.analysis.methods.domain_preparation import consumers
    from marivo.analysis.methods.history_view_physical import consumers as history_consumers
    from marivo.analysis.methods.journey_physical import consumers as journey_consumers
    from marivo.analysis.methods.retention_physical import consumers as retention_consumers

    declarations = (
        *retention_consumers(method),
        *anchor_consumers(method),
        *consumers(method),
        *journey_consumers(method),
        *tuple(
            replace(item, contract_version=4)
            if method.name.startswith("state_rollup")
            or method.name.startswith("row.")
            or method.name
            in (
                "bind_project",
                "group.attach",
                "parts_transport",
                "metric.mean",
                "metric.ratio",
                "metric.linear",
                "metric.weighted_mean",
            )
            else replace(item, contract_version=3)
            if method.name
            in (
                "metric.observe",
                "metric.sum_zero",
                "state_rollup",
                "state_rollup.sum_zero",
                "metric.fold",
                "state_rollup.fold",
                "metric.mean",
                "metric.weighted_mean",
                "metric.ratio",
                "metric.linear",
                "state_rollup.mean",
                "state_rollup.weighted_mean",
                "state_rollup.ratio",
                "state_rollup.linear",
                "state_rollup.min",
                "state_rollup.max",
            )
            else replace(
                item,
                contract_version=4,
                qualification=Qualified(
                    f"r62.{item.key.route}.cell.difference.{item.key.shape}@v1",
                    "analysis.compiler.graph_lowering"
                    if item.key.route == "ibis"
                    else "analysis.materialization.graph_local_execution",
                    "tests/test_analysis_comparison_runtime_r62.py",
                ),
            )
            if method.name == "cell.difference"
            else item
            for item in (
                tuple(
                    replace(
                        declaration, key=replace(declaration.key, input_domains=(domain, domain))
                    )
                    for declaration in _shape_implementations(method)
                    for domain in ("entity", "group", "singleton")
                )
                if method.name == "cell.difference"
                else _shape_implementations(method)
            )
        ),
    )

    keys = {item.key for item in declarations}
    return (*declarations, *(item for item in history_consumers(method) if item.key not in keys))


def specialize_arity(implementation: Implementation, arity: int) -> Implementation:
    """Expand only the homogeneous linear consumer's ordered component arity."""
    if implementation.key.method.name != "metric.linear" or arity < 2:
        return implementation
    key = implementation.key
    if (
        key.input_types != (key.input_types[0],) * 2
        or key.input_domains != (key.input_domains[0],) * 2
    ):
        return implementation
    return replace(
        implementation,
        key=replace(
            key,
            input_types=(key.input_types[0],) * arity,
            input_domains=(key.input_domains[0],) * arity,
        ),
    )


def specialize_numeric(implementation: Implementation, key: QualificationKey) -> Implementation:
    """Bind precise numeric families and closed typed predicate inputs."""
    if (
        key.method.name == "anchor.observe"
        and key.input_domains == implementation.key.input_domains == ("anchor", "entity")
        and key.input_types[0] == implementation.key.input_types[0]
        and len(key.input_types) == 2
        and key.input_types[1] in (ScalarType("int64"), ScalarType("string"))
    ):
        return replace(implementation, key=replace(implementation.key, input_types=key.input_types))
    if (
        key.input_domains == implementation.key.input_domains == ("anchor",)
        and len(key.input_types) == 1
        and (
            key.method.name == "map_correspond"
            or (key.method.name == "row.mean" and isinstance(key.input_types[0], DurationType))
        )
        and isinstance(key.input_types[0], (DecimalType, DurationType))
        and implementation.key.input_types
        == (ScalarType("int64" if key.method.name != "row.mean" else "float64"),)
    ):
        return replace(
            implementation,
            key=replace(implementation.key, input_types=key.input_types),
            precision="exact",
        )
    if (
        key.method.name == "occurrence.prepare"
        and key.input_domains == implementation.key.input_domains == ("entity",)
        and len(key.input_types) == 1
        and isinstance(implementation.key.shape, SourceShape)
    ):
        # Population projections carry an upstream scalar type but no scalar column.
        # Preparation consumes complete member keys, independently of that marker.
        return replace(implementation, key=replace(implementation.key, input_types=key.input_types))
    if (
        key.input_domains == implementation.key.input_domains == ("entity", "entity")
        and len(key.input_types) == 2
        and key.input_types[0] == implementation.key.input_types[0]
        and isinstance(implementation.qualification, Qualified)
        and implementation.qualification.consumer_id == "analysis.materialization.graph_preparation"
    ):
        # The original member envelope is also a key-only population projection.
        return replace(implementation, key=replace(implementation.key, input_types=key.input_types))
    if (
        key.method.name.startswith("attribution.")
        and key.input_domains == implementation.key.input_domains
        and len(key.input_types) == 2
        and key.input_types[0] == key.input_types[1]
    ):
        return replace(implementation, key=replace(implementation.key, input_types=key.input_types))
    if (
        key.method.name.startswith("display.")
        and key.input_domains == (implementation.key.input_domains[0],) * len(key.input_types)
        and len(key.input_types) >= 1
    ):
        if (
            key.method.name == "display.rank"
            and isinstance(key.input_types[0], ScalarType)
            and key.input_types[0].name not in ("int64", "float64")
        ):
            return implementation
        return replace(
            implementation,
            key=replace(
                implementation.key, input_types=key.input_types, input_domains=key.input_domains
            ),
        )
    if key.method.name == "group.attach":
        if (
            key.input_domains != implementation.key.input_domains
            or len(key.input_types) != 2
            or key.input_types[1] != implementation.key.input_types[1]
            or not isinstance(key.input_types[0], DecimalType)
            or implementation.key.input_types[0] != ScalarType("int64")
        ):
            return implementation
        return replace(
            implementation,
            key=replace(implementation.key, input_types=key.input_types),
            contract_version=4,
            qualification=Qualified(
                implementation.qualification.implementation_id,
                implementation.qualification.consumer_id,
                "tests/test_analysis_references_r64.py",
            )
            if isinstance(implementation.qualification, Qualified)
            else implementation.qualification,
        )
    if (
        key.method.name.startswith("reference.")
        and key.input_domains == implementation.key.input_domains
        and len(key.input_types) == len(implementation.key.input_types)
    ):
        from marivo.analysis.methods.references import reference_type

        kind = (
            "share"
            if key.method.name == "reference.share"
            else "penetration"
            if key.method.name == "reference.penetration"
            else "standardize"
        )
        try:
            reference_type(kind, key.input_types[0], key.input_types[1])
        except ValueError:
            return implementation
        return replace(implementation, key=replace(implementation.key, input_types=key.input_types))
    if (
        key.method.name == "domain.cohort"
        and implementation.key.input_domains == ("entity", "journey")
        and 2 <= len(key.input_types) <= 64
        and key.input_domains == ("entity", *("journey",) * (len(key.input_types) - 1))
        and key.input_types[0] == implementation.key.input_types[0]
        and all(
            value
            in (
                ScalarType("int64"),
                ScalarType("boolean"),
                ScalarType("string"),
                ScalarType("timestamp"),
                DurationType("us"),
            )
            for value in key.input_types[1:]
        )
    ):
        return replace(
            implementation,
            key=replace(
                implementation.key, input_types=key.input_types, input_domains=key.input_domains
            ),
        )
    if (
        key.method.name in ("parts_transport", "domain.cohort")
        and implementation.key.input_types == (ScalarType("int64"),)
        and key.input_domains == (implementation.key.input_domains[0],) * len(key.input_types)
        and 1 <= len(key.input_types) <= 64
    ):
        return replace(
            implementation,
            key=replace(
                implementation.key, input_types=key.input_types, input_domains=key.input_domains
            ),
        )
    allowed = (
        "cell.difference",
        "cell.relative_change",
        "cell.ratio",
        "parts_transport",
        "state_rollup",
        "state_rollup.sum_zero",
        "state_rollup.min",
        "state_rollup.max",
        "state_rollup.mean",
        "state_rollup.weighted_mean",
        "state_rollup.ratio",
        "state_rollup.linear",
        "state_rollup.fold",
        "metric.ratio",
        "metric.linear",
        "row.count",
        "row.count_defined",
    )
    if key.method.name not in allowed or len(key.input_types) != len(
        implementation.key.input_types
    ):
        return implementation
    decimal_inputs = all(isinstance(value, DecimalType) for value in key.input_types)
    duration_inputs = (
        all(isinstance(value, DurationType) for value in key.input_types)
        and len(set(key.input_types)) == 1
    )
    float_inputs = key.method.name in (
        "cell.difference",
        "cell.relative_change",
        "cell.ratio",
        "metric.ratio",
        "metric.linear",
        "state_rollup.linear",
    ) and key.input_types == (ScalarType("float64"),) * len(key.input_types)
    if not (decimal_inputs or duration_inputs or float_inputs):
        return implementation
    template = (
        ScalarType("float64")
        if key.method.name
        in ("state_rollup.mean", "state_rollup.weighted_mean", "state_rollup.ratio")
        else ScalarType("int64")
    )
    if implementation.key.input_types != (template,) * len(key.input_types):
        return implementation
    precision: Literal["checked_int64", "finite_float64", "exact"] = (
        "checked_int64"
        if key.method.name in ("row.count", "row.count_defined")
        else "finite_float64"
        if key.input_types[0] == ScalarType("float64")
        or (
            duration_inputs
            and key.method.name in ("cell.ratio", "cell.relative_change", "metric.ratio")
        )
        else "checked_int64"
        if isinstance(key.input_types[0], DurationType)
        else "exact"
    )
    return replace(
        implementation,
        key=replace(implementation.key, input_types=key.input_types),
        precision=precision,
    )


def admit(implementation: Implementation, params: RuleParameters) -> None:
    """Resolve a real consumer and reject parameter variants outside its evidence."""
    if isinstance(params, (ObserveMetric, ObserveCount)) and params.capture_versions:
        reject(
            "relative observation through anchor.observe",
            "a capture template submitted as an executable Metric node",
            "Use AnchorDomain.observe so bounded version resolution owns the candidate input.",
        )
    if implementation not in tuple(
        specialize_numeric(
            specialize_arity(candidate, len(implementation.key.input_types)), implementation.key
        )
        for candidate in implementations(implementation.key.method)
    ):
        reject(
            "an implemented R3.4 consumer",
            repr(implementation.key),
            "Qualify a real lowerer and all required checkers for this exact key.",
        )
    if isinstance(
        params,
        (
            AnchorBind,
            AnchorObserve,
            AnchorRetention,
            RetentionBySubject,
            OccurrencePrepare,
            PreparedObservation,
            FunnelAxesPrepare,
            FunnelReduce,
            FunnelCompare,
            FunnelRead,
            FunnelAttribute,
            HistoryReplay,
            HistoryView,
            HistoryRead,
            HistoryAxesPrepare,
            JourneyMatch,
            JourneyDuration,
            JourneyCompleted,
            JourneyRead,
        ),
    ):
        return
    if (
        isinstance(implementation.key.shape, SourceShape)
        and implementation.key.shape.backend == "sqlite"
        and isinstance(params, (ObserveMetric, ObserveCount))
        and params.coordinates
    ):
        reject(
            "a SQLite observation without nested contribution-coordinate state",
            repr(params.coordinates),
            "Omit contribution coordinates; SQLite nested state is not qualified.",
        )
    if isinstance(
        params,
        (
            AttributionDerive,
            AttachCategory,
            CompleteGroups,
            TimeProduct,
            ReferenceDerive,
            DisplayRank,
            DisplayTable,
        ),
    ):
        return
    if isinstance(params, ObserveWeightedMean):
        if params.amount_type not in ("int64", "float64") and not params.amount_type.startswith(
            ("decimal(", "interval(")
        ):
            reject(
                "qualified homogeneous numeric pairs or Duration/int64 pairs",
                repr(params),
                "Use the qualified value/weight types.",
            )
    elif isinstance(params, ObserveCount):
        if len(params.path) not in (0, 1, 2):
            reject(
                "one qualified count route", repr(params.path), "Use a direct member relationship."
            )
    elif isinstance(params, ObserveMetric):
        if len(params.path) not in (0, 1, 2) or (
            params.amount_type not in ("int64", "float64")
            and not (
                params.method
                in ("sum", "mean", "min", "max", "median", "percentile", "count_distinct")
                and params.amount_type.startswith(("decimal(", "interval("))
            )
            and not (
                params.method == "count_distinct"
                and params.amount_type in ("string", "boolean", "date", "timestamp")
            )
            and not (
                params.method in ("count_distinct", "approx_count_distinct")
                and params.distinct_columns
            )
        ):
            reject(
                "one qualified to-one observation route",
                repr(params.path),
                "Use the qualified direct contribution-to-member route.",
            )
    elif isinstance(params, BindProject):
        if (
            params.metric_contract is not None
            or params.field_contract is None
            or params.field_contract.logical_type
            not in ("int64", "string", "float64", "boolean", "date", "timestamp")
        ):
            reject(
                "direct int64 or string field binding without parsing",
                repr(params.ref),
                "Qualify the relationship, Metric or temporal lowering separately.",
            )
    elif isinstance(params, OriginalRatio):
        pass
    elif isinstance(params, OccurrenceCombine):
        if len(params.terms) < 2 or any(sign not in (1, -1) for _quantity, sign in params.terms):
            reject(
                "at least two ordered signed terms",
                repr(params.terms),
                "Combine the named occurrences with an explicit sign.",
            )
    elif isinstance(params, OriginalReduce):
        if params.output_domain.kind not in ("singleton", "group") or (
            (params.output_domain.kind == "group") != bool(params.coordinates)
        ):
            reject(
                "a whole-domain or retained-coordinate original rollup",
                repr(params.output_domain),
                "Use the qualified singleton target.",
            )
    elif isinstance(params, MapCorrespond):
        if params.mode not in (
            "exact_keys",
            "one_to_one",
            "union_keys",
            "subjects",
            "group",
            "group_keys",
        ):
            reject(
                "qualified complete-key or single-string group correspondence",
                params.mode,
                "Bind a supported correspondence and its exact target domain.",
            )
        if params.mode == "group" and params.check_id != "source.group_mapping@v1":
            reject(
                "the registered Group mapping check",
                repr(params.check_id),
                "Bind source.group_mapping@v1 for the exact Group projection.",
            )
    elif isinstance(params, CellDerive):
        if params.method not in ("difference", "relative_change", "ratio") or (
            params.pairing_check_id
            != ("source.exact_pairing@v1" if params.pairing == "exact" else "source.unique_key@v1")
            or params.numeric_check_id != "source.finite_numeric@v1"
        ):
            reject(
                "strict paired absolute difference with exact checks",
                repr(params),
                "Bind the ordered comparable endpoints and registered checks.",
            )
    elif isinstance(params, PartsTransport):
        if params.mode not in ("where", "projection", "view", "cohort", "limit") or (
            params.mode == "where" and not params.predicates
        ):
            reject(
                "projection, view, or where with explicit predicates",
                params.mode,
                "Bind the selection in the definition graph; materialization belongs to R4.",
            )
    elif isinstance(params, AssociationScore):
        if (
            params.pairing_check_id != "source.exact_pairing@v1"
            or params.numeric_check_id != "source.finite_numeric@v1"
        ):
            reject(
                "registered pairing and numeric checks",
                repr(params),
                "Use the exact Association checks.",
            )
    elif not isinstance(params, RowState) or params.method not in (
        "count",
        "count_defined",
        "sum",
        "mean",
        "min",
        "max",
    ):
        reject("a connected row state consumer", repr(params), "Use the registered exact method.")
