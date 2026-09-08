"""Pure exact contracts shared by independent comparison arithmetic checks."""

from dataclasses import replace

from marivo.analysis import grain
from marivo.analysis.datasets.descriptors import _CORE_TOKEN, _deferred_type, _make_schema
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.contracts import ComparePayload, CompareSpecV1
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources

REVENUE = ref.metric("sales.revenue")
REGION = ref.dimension("sales.customers.region")
DAY = ref.time_dimension("sales.orders.order_time")


def comparison_spec(
    *, shape: str = "dimension", numeric: str = "float64", zero: bool = True
) -> CompareSpecV1:
    sources = make_sources()
    metric = sources.observe(REVENUE)
    if "dimension" in shape:
        metric = metric.with_dimensions(REGION)
    if "time" in shape:
        metric = metric.with_time_axis(DAY, grain=grain("day"))
    metric = metric.aggregate()
    delta = metric.compare(metric)
    assert isinstance(delta._root, LogicalRootHandle) and isinstance(
        delta._root.payload, ComparePayload
    )
    spec = delta._root.payload.spec
    fields = tuple(
        replace(
            field,
            _token=_CORE_TOKEN,
            logical_type_id=numeric,
            physical_type_state=_deferred_type(numeric, ids=metric._registration.ids),
        )
        if field.name in ("current_value", "baseline_value", "delta")
        else field
        for field in spec.output_row.schema.columns
    )
    return replace(
        spec,
        promoted_type=numeric,
        exact_empty_zero=zero,
        output_row=replace(spec.output_row, _token=_CORE_TOKEN, schema=_make_schema(fields)),
    )
