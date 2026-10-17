"""Explicit complete-key transport of a lazy value reference."""

from marivo._definition.query import ResultQuery, SelectValues, ValueBroadcast
from marivo._definition.relations import QueryKeys
from marivo.analysis.core.graph import capture_query
from marivo.analysis.core.model import reject
from marivo.analysis.dsl.column import Column
from marivo.analysis.dsl.query import Query


def broadcast(value: Column, *, to: Query) -> Column:
    """Transport a reference value onto a target's complete coordinate domain.

    Args: value: A lazy Column. to: Same-Session target Query.
    Returns: A Column bound to the target, using unique full-axis projection.
    Example: ``q.select({'ratio':q['sales']/mv.broadcast(total['sales'],to=q)})``.
    Constraints: Scalar references have one row; grouped references cover every target key.
    """
    if not isinstance(value, Column) or value._query is None or not isinstance(to, Query):
        reject(
            "a lazy Column and target Query",
            type(value).__name__,
            "Locate a Column on its source Query and pass to=query.",
            "broadcast",
        )
    source = value._query
    owner = to._graph.signature.owner
    if source.query.owner != owner:
        reject(
            "one Session owner",
            str(source.query.owner),
            "Build the reference and target in the same Session.",
            "broadcast",
        )
    root = ResultQuery(
        (source,), SelectValues((("reference", value._expression),)), source.axes, owner
    )
    selected = QueryKeys(
        root, source.axes, units=(value._unit,), names=("reference",), kinds=(value._kind,)
    )
    return Column(
        to._graph.fingerprint,
        ValueBroadcast(selected),
        value._unit,
        value._kind,
        capture_query(to._graph),
    )
