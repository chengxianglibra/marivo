"""Context Metrics with explicit identity binding at observation time."""

from ibis.common.deferred import Deferred

from marivo._definition.deferred import ContextMetricResolver


def member_count() -> Deferred:
    """Define the count of the complete identity domain supplied to observe.members.

    Returns: A native Ibis Deferred Metric, without capturing a Population.
    Example: ``session.observe({'people': mv.member_count()}, members=people)``.
    Constraints: Explicit single-Entity identity membership is required; this
    counts declared references rather than contribution rows or calendar cells.
    """
    return Deferred(ContextMetricResolver())
