"""Exact semantic expression and receiver operations."""

from __future__ import annotations

from marivo._authoring.model import AuthoringCapability, AuthoringInputRequirement
from marivo.introspection.live.model import LiveHelpTarget
from marivo.semantic._help_specs.builders import (
    _NONE,
    _capability,
    _parameter_input,
    _target,
)
from marivo.semantic._help_specs.reduction_specs import _reduction_inputs, _reduction_summary


def reducer_specs() -> tuple[AuthoringCapability, ...]:
    rows: list[AuthoringCapability] = []
    for prefix, family, methods in (
        (
            "row",
            "RowExpression",
            (
                "sum",
                "mean",
                "min",
                "max",
                "median",
                "quantile",
                "weighted_mean",
                "count",
                "count_distinct",
                "approx_nunique",
                "approx_median",
                "approx_quantile",
            ),
        ),
        ("Metric", "NumericMetric", ("where", "per", "window")),
        (
            "MetricSamples",
            "MetricSamples",
            (
                "sum",
                "mean",
                "min",
                "max",
                "median",
                "quantile",
                "weighted_mean",
                "count",
                "count_distinct",
                "approx_nunique",
                "approx_median",
                "approx_quantile",
                "first",
                "last",
            ),
        ),
        ("Entity", "Entity", ("count",)),
    ):
        for method in methods:
            inputs, example = _reduction_inputs(prefix, method, family)
            summary = _reduction_summary(prefix, method)
            rows.append(
                _capability(
                    f"{prefix}.{method}",
                    f"marivo.semantic.catalog_model.objects.Entity.{method}"
                    if prefix == "Entity"
                    else f"marivo.semantic._expression_values.{'RowExpression' if prefix == 'row' else 'MetricExpression' if prefix == 'Metric' else '_MetricSamples'}.{method}",
                    summary,
                    kind="method",
                    output="MetricSamples"
                    if prefix == "Metric" and method == "per"
                    else "NumericMetric",
                    inputs=tuple(inputs),
                    effects=_NONE,
                    example=example,
                    public_entrypoint=f"{'amount' if prefix == 'row' else 'samples' if prefix == 'MetricSamples' else 'revenue' if prefix == 'Metric' else 'orders' if prefix == 'Entity' else 'payment_time'}.{method}",
                    see_also=(
                        _target("predicate"),
                        _target("expressions"),
                        LiveHelpTarget(surface="analysis", canonical_id="analysis_frame.where"),
                    )
                    if prefix == "Metric" and method == "where"
                    else (
                        (
                            _target("expressions.row"),
                            _target("metric"),
                        )
                        if prefix in {"row", "Entity"}
                        else (_target("expressions.row"),)
                        if prefix in {"row", "MetricSamples", "Entity"}
                        else (_target("expressions"),)
                    ),
                )
            )
    return tuple(rows)


def expression_specs() -> tuple[AuthoringCapability, ...]:
    rows: list[AuthoringCapability] = [
        _capability(
            "Field.isin",
            "marivo.semantic._expression_values.RowExpression.isin",
            "Test membership in literal values on a resolved scalar field. The predicate retains "
            "the field's contribution root and version. Combine predicates with & and |; "
            "use ~ to negate. Pass the predicate to Metric.where or Session.observe(where=...).",
            kind="method",
            output="RowPredicate",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="Field"),
                _parameter_input("dependency", "SourceScalarSequence", "values"),
            ),
            effects=_NONE,
            example="region.isin(('US', 'CA'))",
            public_entrypoint="region.isin",
            see_also=(_target("predicate"), _target("Metric.where")),
        ),
        _capability(
            "Dimension.isnull",
            "marivo.semantic._expression_values.RowExpression.isnull",
            "Construct a missing-value predicate on a native categorical, time, or numeric field. "
            "The predicate retains the field's exact version and contribution root. "
            "Use it in Metric.where(...) or Session.observe(where=...); scalar paths and attribute anchors remain captured.",
            kind="method",
            output="RowPredicate",
            inputs=(AuthoringInputRequirement(role="receiver", family="Field"),),
            effects=_NONE,
            example="region.isnull()",
            public_entrypoint="region.isnull",
            see_also=(_target("predicate"),),
        ),
    ]
    rows.extend(reducer_specs())
    for target, _owner, _method, _output, parameter, family, example in (
        (
            "Field.via",
            "_FieldComparisons",
            "via",
            "Dimension | TimeField | Measure",
            "path",
            "Path",
            "region.via(mv.path(orders_to_customers))",
        ),
        (
            "Field.at",
            "_FieldComparisons",
            "at",
            "Dimension | TimeField | Measure",
            "anchor",
            "InstantAnchor | TimeExpression",
            "region.at(session.instant('2026-01-01'))",
        ),
    ):
        rows.append(
            _capability(
                target,
                f"marivo.semantic._expression_values.RowExpression.{_method}",
                "Bind a field to the target of a single-valued directed Path. A target anchor can come from the final step or field.at(...). Supply one declaration."
                if _method == "via"
                else "Select a fixed InstantAnchor or use a native TimeDimension on an unversioned or snapshot fact root for qualified same-source, single-root observe by/where on DuckDB/Trino. Row time excludes validity fact roots, read/classify, windows, internal samples and IANA local clocks. DATE control selects target business dates; timestamp validity needs timestamp control. Missing versions and NULL control retain NULL labels.",
                kind="method",
                output="RowExpression",
                inputs=(
                    AuthoringInputRequirement(role="receiver", family="Field"),
                    _parameter_input("dependency", family, parameter),
                ),
                effects=_NONE,
                example=example,
                public_entrypoint=example.split("(", 1)[0],
                see_also=(_target("Field.at"), _target("expressions"))
                if _method == "via"
                else (_target("Field.via"), _target("expressions")),
            )
        )
    rows.append(
        _capability(
            "Relationship.at",
            "marivo.semantic.catalog_model.objects.Relationship.at",
            "Capture an exact version anchor at this relationship step’s versioned target. The next step in mv.path(...) uses that same node version.",
            kind="method",
            output="RelationshipStep",
            inputs=(
                AuthoringInputRequirement(role="receiver", family="Relationship"),
                _parameter_input("dependency", "InstantAnchor", "anchor"),
            ),
            preconditions=("The consuming operation must read the target version row.",),
            example="relationship.at(session.instant('2026-01-01'))",
            public_entrypoint="relationship.at",
            see_also=(
                LiveHelpTarget(surface="analysis", canonical_id="path.define"),
                LiveHelpTarget(surface="analysis", canonical_id="relationship_step"),
            ),
        )
    )
    return (*rows, *_scalar_specs())


def _scalar_specs() -> tuple[AuthoringCapability, ...]:
    rows: list[AuthoringCapability] = []
    for stage, owner in (("row", "RowExpression"), ("Metric", "MetricExpression")):
        for method, example in (
            ("abs", "value.abs()"),
            ("sqrt", "value.sqrt()"),
            ("exp", "value.exp()"),
            ("round", "value.round(2)"),
            ("cast", "value.cast('float64')"),
            ("log", "value.log()"),
            ("clip", "value.clip(lower=0, upper=100)"),
            ("coalesce", "value.coalesce(0)"),
            ("least", "value.least(100)"),
            ("greatest", "value.greatest(0)"),
            ("nullif", "value.nullif(0)"),
            ("ifelse", "condition.ifelse(value, 0)"),
            ("cases", "value.cases((1, 10), else_=0)"),
            ("notnull", "value.notnull()"),
            *((("isnull", "value.isnull()"),) if stage == "Metric" else ()),
        ):
            is_predicate = method in {"isnull", "notnull"}
            rows.append(
                _capability(
                    f"{stage}.{method}",
                    f"marivo.semantic._expression_values.{owner}.{method}",
                    "Capture a stage-preserving scalar operation over compatible semantic inputs. Methods enforce their declared roles; ifelse requires a boolean receiver. Literals are frozen, and capture does not read sources.",
                    kind="method",
                    output=("RowPredicate" if stage == "row" else "MetricPredicate")
                    if is_predicate
                    else owner,
                    inputs=(AuthoringInputRequirement(role="receiver", family=owner),),
                    effects=_NONE,
                    example=example,
                    public_entrypoint=example.split("(", 1)[0],
                    see_also=(
                        _target(
                            "predicate" if is_predicate or method == "ifelse" else "expressions"
                        ),
                    ),
                )
            )
    return tuple(rows)
