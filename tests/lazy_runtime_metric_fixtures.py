"""Closed Runtime Metric forest shared by private acceptance journeys."""

from marivo.analysis import runtime_metric as rm
from marivo.refs import ref

AMOUNT = ref.measure("sales.orders.amount")
WEIGHT = ref.measure("sales.orders.weight")
REVENUE = ref.metric("sales.revenue")
CUSTOMERS = ref.entity("sales.customers")


def expressions() -> tuple[rm.RuntimeMetricExpr, ...]:
    total = rm.aggregate(AMOUNT, agg="sum", label="total")
    mean = rm.aggregate(AMOUNT, agg="mean", label="average")
    weighted = rm.weighted_mean(AMOUNT, WEIGHT, label="weighted")
    sliced = rm.slice(total, by={ref.dimension("sales.customers.region"): "EU"}, label="europe")
    ratio = rm.ratio(total, mean, label="ratio")
    linear = rm.linear(add=(REVENUE, total), subtract=(mean,), label="linear")
    share = rm.ratio(sliced, total, label="share")
    weighted_europe = rm.slice(
        weighted, by={ref.dimension("sales.customers.region"): "EU"}, label="weighted_europe"
    )
    return total, mean, weighted, sliced, ratio, share, weighted_europe, linear
