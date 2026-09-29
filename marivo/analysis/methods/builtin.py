"""Precisely bounded R3.4 consumers; declarations are not invocation evidence."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.model import CheckId, DomainKind, PartRole
from marivo.analysis.core.rules import (
    AssociationScore,
    AttachCategory,
    BindProject,
    CellDerive,
    CompleteGroups,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrenceCombine,
    OriginalRatio,
    OriginalReduce,
    PartsTransport,
    RowState,
    RuleParameters,
)
from marivo.analysis.methods.errors import reject
from marivo.analysis.methods.physical import (
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
    "subject",
    "original_state",
    "row_state",
    "coverage",
    "coordinate_state",
)
CHECKS: tuple[CheckId, ...] = (
    "source.unique_key@v1",
    "source.exact_pairing@v1",
    "source.cell_policy@v1",
    "source.group_mapping@v1",
    "source.contribution_partition@v1",
    "source.complete_coverage@v1",
)
NUMERIC_CHECKS: tuple[CheckId, ...] = (
    *CHECKS,
    "source.finite_numeric@v1",
    "source.single_value@v1",
)


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
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
        "state_rollup",
        "state_rollup.count",
        "state_rollup.sum_zero",
        "state_rollup.linear",
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
        "metric.observe",
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
                    f"r45.{method}.{form}.{key_type}@v1",
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
                        (*PARTS, "current_endpoint", "baseline_endpoint")
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
                ("source.exact_pairing@v1", "source.finite_numeric@v1"),
                ("subject", "current_endpoint", "baseline_endpoint"),
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
                    (*PARTS, "current_endpoint", "baseline_endpoint", "pair_counts"),
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


def admit(implementation: Implementation, params: RuleParameters) -> None:
    """Resolve a real consumer and reject parameter variants outside its evidence."""
    if implementation not in tuple(
        specialize_arity(candidate, len(implementation.key.input_types))
        for candidate in implementations(implementation.key.method)
    ):
        reject(
            "an implemented R3.4 consumer",
            repr(implementation.key),
            "Qualify a real lowerer and all required checkers for this exact key.",
        )
    if isinstance(params, (AttachCategory, CompleteGroups)):
        return
    if isinstance(params, ObserveWeightedMean):
        if params.amount_type != "int64":
            reject(
                "int64 paired observation",
                repr(params),
                "Use the qualified value/weight types.",
            )
    elif isinstance(params, ObserveCount):
        if len(params.path) not in (1, 2):
            reject(
                "one qualified count route", repr(params.path), "Use a direct member relationship."
            )
    elif isinstance(params, ObserveMetric):
        if len(params.path) not in (1, 2) or params.amount_type not in ("int64", "float64"):
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
        if params.method != "difference" or (
            params.pairing_check_id != "source.exact_pairing@v1"
            or params.numeric_check_id != "source.finite_numeric@v1"
        ):
            reject(
                "strict paired absolute difference with exact checks",
                repr(params),
                "Bind the ordered comparable endpoints and registered checks.",
            )
    elif isinstance(params, PartsTransport):
        if params.mode not in ("where", "projection", "view") or (
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
