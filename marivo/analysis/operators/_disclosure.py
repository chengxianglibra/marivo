"""Typed Operator owners supply native static inputs linked to execution consumers."""

from __future__ import annotations

from marivo.analysis._capabilities.dataset_model import (
    CONSTRUCTION_EFFECT,
    CONSTRUCTION_FAILURES,
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    family,
    operation,
    value_type,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.domains.event_attribution import FunnelAttributionSemantics
from marivo.analysis.domains.event_comparison import FunnelDeltaSemantics
from marivo.analysis.operators.association_contracts import AssociationSemantics
from marivo.analysis.operators.attribution_contracts import AttributionSemantics
from marivo.analysis.operators.candidate_contracts import CandidateSemantics
from marivo.analysis.operators.contracts import DeltaSemantics, WindowBucketAlignment, window_bucket
from marivo.analysis.operators.discovery import DeltaDiscovery, MetricDiscovery
from marivo.analysis.operators.forecast_contracts import (
    ForecastHorizon,
    ForecastModel,
    ForecastSemantics,
    drift,
    naive,
    periods,
    seasonal_naive,
)


def provider(registry: DatasetFamilyRegistry) -> DisclosureProvider:
    parameters: tuple[P, ...]
    requires: tuple[str, ...]
    registrations: tuple[str, ...]
    variants: tuple[type[object], ...]
    value: object
    descriptors: list[Descriptor] = []
    exports: list[ExportInput] = []
    for fid, summary, variants in (
        (
            "delta",
            "Paired exact current/baseline comparison, including a distinct funnel variant.",
            (DeltaSemantics, FunnelDeltaSemantics),
        ),
        (
            "attribution",
            "Governed additive, component-mix, distinct-membership, distribution-Shapley or funnel-loss-rate contributions.",
            (AttributionSemantics, FunnelAttributionSemantics),
        ),
        (
            "association",
            "Descriptive pair association with typed validity statuses; not causality.",
            (AssociationSemantics,),
        ),
        (
            "forecast",
            "Projection rows under an explicit model, horizon and interval assumption.",
            (ForecastSemantics,),
        ),
        (
            "candidate",
            "Descriptive screening leads with objective, score and reason codes; not conclusions.",
            (CandidateSemantics,),
        ),
    ):
        registration = registry.get(fid)
        target = fid + "_dataset"
        descriptors.append(
            family(
                target,
                registration,
                summary=summary,
                variants=variants,
                acquisition="Use the registered Dataset operator; execute() produces the paired committed state.",
                constraints=(
                    "Filtering, ranking and limiting preserve family semantics and return Logical state.",
                    summary,
                ),
            )
        )
        exports.extend(
            ExportInput(t.__name__, t, target)
            for t in (registration.logical_type, registration.materialized_type)
        )

    for name, parameters, code, requires, summary in (
        (
            "where",
            (
                P(
                    "predicates",
                    "Construct typed predicates using fields admitted by the receiver's family.",
                    ("filters",),
                ),
            ),
            "result = dimensioned.where(eq(region, 'north'))",
            ("dimensioned", "eq", "region"),
            "Filter rows; Population filters membership, while result filters do not redefine source membership.",
        ),
        (
            "rank",
            (
                P("by", "Select a current sortable field.", ("datasets.fields",)),
                P("order", "Choose ascending or descending."),
                P(
                    "ties",
                    "Choose ordinal, dense, min or max; ordinal uses a registered unique tie-breaker.",
                ),
                P("partition_by", "Select an ordered tuple of current grouping fields."),
            ),
            "result = dimensioned.aggregate().rank(dimensioned.aggregate().fields.metric(revenue))",
            ("dimensioned", "revenue"),
            "Add stable rank metadata without dropping rows; limit is a separate operation.",
        ),
        (
            "limit",
            (P("count", "Choose a positive row count after establishing total ordering."),),
            "ranked = dimensioned.aggregate().rank(dimensioned.aggregate().fields.metric(revenue))\nresult = ranked.limit(1)",
            ("dimensioned", "revenue"),
            "Keep a bounded prefix of an explicitly totally ordered Dataset.",
        ),
    ):
        admitted = tuple(
            f
            for f in registry.registrations
            if any(c.id == f.family_id + "." + name for c in f.consumers)
        )
        bindings = tuple(
            bind(getattr(t, name), t)
            for f in admitted
            for t in (f.logical_type, f.materialized_type)
        )
        descriptors.append(
            operation(
                "datasets." + name,
                "dataset." + name,
                bindings[0].implementation,
                bindings=bindings,
                summary=summary,
                discovery_group="methods.rows",
                parameters=parameters,
                output="Logical Dataset of the receiver family",
                constraints=(summary,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(code, requires, "result", "Logical Dataset"),
                registration_ids=tuple(f.family_id + "." + name for f in admitted),
            )
        )

    specs = (
        (
            "metric",
            "compare",
            "metric_dataset.compare",
            ("metric.compare",),
            (
                P(
                    "baseline",
                    "Construct an exact compatible one-Metric baseline in this Session.",
                    ("observe",),
                ),
                P(
                    "alignment",
                    "Use window_bucket() to pair equal-count time buckets by ordinal.",
                    ("window_bucket",),
                ),
            ),
            "LogicalDeltaDataset",
            "result = dimensioned.aggregate().compare(dimensioned.aggregate())",
            ("dimensioned",),
            "One Metric, compatible units/membership/coordinates and exact bucket pairing; one-sided sampling is rejected.",
        ),
        (
            "delta",
            "attribute",
            "delta_dataset.attribute",
            ("delta.attribute", "delta.attribute_expanded"),
            (
                P(
                    "axes",
                    "Select ordered governed Dimensions; missing axes require logical source operands.",
                ),
                P("mode", "Choose joint or hierarchy."),
                P(
                    "top_k",
                    "Optional positive retained-member count per mapped parent; None retains all members. Excluded members form governed Other, not discarded rows.",
                ),
                P(
                    "target",
                    "Leave None for Metric attribution; funnel loss uses its separate focused leaf.",
                    ("funnel_delta_dataset.attribute",),
                ),
            ),
            "LogicalAttributionDataset",
            "result = delta.attribute(axes=(region,))",
            ("delta", "region"),
            "Admits additive, component-mix, exact distinct-membership and distribution-Shapley comparisons under method-specific authority; retained inputs require complete sufficient statistics. Distribution-Shapley uses complete mapped players and coalitions and preserves the selected exact or approximate quantile method.",
        ),
        (
            "metric",
            "correlate",
            "metric_dataset.correlate",
            ("metric.correlate",),
            (
                P("method", "Choose pearson, spearman or kendall."),
                P(
                    "lag_range",
                    "Use a contiguous bounded integer range only on time-bearing input.",
                ),
            ),
            "LogicalAssociationDataset",
            "result = multi_metric.aggregate().correlate(method='pearson')",
            ("multi_metric",),
            "At least two ordered compatible Metrics; insufficient/constant pairs retain typed statuses, never zero coefficients.",
        ),
        (
            "metric",
            "forecast",
            "metric_dataset.forecast",
            ("metric.forecast",),
            (
                P(
                    "horizon",
                    "Construct periods(count) for the positive number of forecast buckets.",
                    ("periods",),
                ),
                P("model", "Choose an exact helper-produced model.", ("forecast_models",)),
                P("interval_level", "Choose a finite probability strictly between zero and one."),
            ),
            "LogicalForecastDataset",
            "result = time_metric.forecast(horizon=periods(2), model=naive())",
            ("time_metric", "periods", "naive"),
            "One time-bearing Metric; training data, dispersion and model-specific minimum length are validated at execution.",
        ),
    )
    for fid, method, target, registrations, parameters, output, code, requires, constraint in specs:
        f = registry.get(fid)
        bindings = tuple(bind(getattr(t, method), t) for t in (f.logical_type, f.materialized_type))
        descriptors.append(
            operation(
                target,
                "dataset." + method,
                bindings[0].implementation,
                bindings=bindings,
                summary=constraint,
                discovery_group="methods.forecast"
                if method == "forecast"
                else "methods.association"
                if method == "correlate"
                else "methods.compare",
                parameters=parameters,
                output=output,
                constraints=(constraint,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(code, requires, "result", output),
                registration_ids=registrations,
                unbound_default=method == "attribute",
            )
        )

    for namespace, name, constraint, receiver in (
        (
            MetricDiscovery,
            "point_anomalies",
            "Point z-score leads on one complete time Metric.",
            "time_metric",
        ),
        (
            MetricDiscovery,
            "interesting_windows",
            "Maximal contiguous unusual runs; gaps and nulls break runs.",
            "time_metric",
        ),
        (
            MetricDiscovery,
            "entity_outliers",
            "Robust Entity-level leads; source-owned identities and positive dispersion are required.",
            "metric",
        ),
        (
            DeltaDiscovery,
            "period_shifts",
            "Trailing complete Delta window means; gaps break windows.",
            "time_delta",
        ),
        (
            DeltaDiscovery,
            "driver_axes",
            "Exact additive axis concentration, including zero and permitted-null members; no inferential conclusion.",
            "delta",
        ),
    ):
        parameters = (
            P(
                "search_space",
                "Choose ordered unique non-time Dimensions for complete additive partitions.",
            )
            if name == "driver_axes"
            else P("threshold", "Choose a finite positive descriptive score cutoff."),
            P("limit", "Choose a maximum lead count from 1 through 1000."),
        )
        arguments = (
            "search_space=(region,), limit=10"
            if name == "driver_axes"
            else "threshold=2.0, limit=10"
        )
        descriptors.append(
            operation(
                "discovery." + name,
                "dataset.discover." + name,
                getattr(namespace, name),
                bindings=(bind(getattr(namespace, name), namespace),),
                summary=constraint,
                discovery_group="discovery",
                parameters=parameters,
                output="LogicalCandidateDataset",
                constraints=(constraint,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput(
                    f"result = {receiver}.discover.{name}({arguments})",
                    (receiver, "region") if name == "driver_axes" else (receiver,),
                    "result",
                    "LogicalCandidateDataset",
                ),
                registration_ids=("discover." + name,)
                + (("discover.driver_axes_expanded",) if name == "driver_axes" else ()),
            )
        )

    for name, target, value, parameters, code, output, constraint in (
        (
            "window_bucket",
            "window_bucket",
            window_bucket,
            (),
            "window_bucket()",
            "WindowBucketAlignment",
            "Pair complete equally sized bucket sequences by ordinal.",
        ),
        (
            "periods",
            "periods",
            periods,
            (P("count", "Choose a positive integer forecast horizon."),),
            "periods(2)",
            "ForecastHorizon",
            "The horizon counts forecast buckets, not elapsed seconds.",
        ),
        (
            "naive",
            "forecast_models.naive",
            naive,
            (),
            "naive()",
            "ForecastModel",
            "Repeat the last observed value.",
        ),
        (
            "drift",
            "forecast_models.drift",
            drift,
            (),
            "drift()",
            "ForecastModel",
            "Extrapolate the training endpoint drift.",
        ),
        (
            "seasonal_naive",
            "forecast_models.seasonal_naive",
            seasonal_naive,
            (
                P(
                    "periods",
                    "Choose a positive integer season length supported by the training history.",
                ),
            ),
            "seasonal_naive(periods=2)",
            "ForecastModel",
            "Repeat the latest complete seasonal pattern.",
        ),
    ):
        descriptors.append(
            operation(
                target,
                "mv." + name,
                value,
                summary=constraint,
                discovery_group="inputs.time"
                if name == "window_bucket"
                else "inputs.forecast"
                if name == "periods"
                else "forecast_models",
                parameters=parameters,
                output=output,
                constraints=(constraint,),
                effects=CONSTRUCTION_EFFECT,
                failures=CONSTRUCTION_FAILURES,
                example=ExampleInput("result = " + code, (name,), "result", output),
            )
        )
        exports.append(ExportInput(name, value, target))
    for type_value, producer in (
        (WindowBucketAlignment, "window_bucket"),
        (ForecastHorizon, "periods"),
        (ForecastModel, "forecast_models.naive"),
    ):
        descriptors.append(
            value_type(
                type_value.__name__,
                type_value,
                summary=f"Closed {type_value.__name__} type_value contract.",
                acquisition="Use its registered policy/model constructor.",
                producers=(producer,),
            )
        )
        exports.append(ExportInput(type_value.__name__, type_value, type_value.__name__))
    return DisclosureProvider("operators", tuple(descriptors), tuple(exports))
