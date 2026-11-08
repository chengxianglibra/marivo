"""Frozen graph identities retain exact calendar contents and object-local reuse."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

import marivo.analysis as mv
from marivo._definition.observations import CoordinateBinding, ObservationBinding
from marivo._definition.query import LiteralValues, ResultQuery
from marivo._definition.types import Owner
from marivo.analysis.compiler.optimization import InvocationMemo
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundSignature, ResultQuantity
from marivo.analysis.dsl.temporal import exact_scope


def _graph(*, calendar: bool = True, value: int = 3) -> BoundGraph:
    owner = Owner("session", "owner")
    axes = (
        (exact_scope("2026-01-01", "2026-01-03", "UTC").by(mv.grain("day"))._binding,)
        if calendar
        else ()
    )
    coordinates = (datetime(2026, 1, 1, tzinfo=timezone.utc),) if calendar else ()
    observation = (
        ObservationBinding(None, None, CoordinateBinding(axes, "calendar"), ())
        if calendar
        else None
    )
    return BoundGraph(
        ResultQuery(LiteralValues(("amount",), ((coordinates, (value,)),)), axes, owner),
        BoundSignature(owner, (ResultQuantity("amount", "USD"),), axes, observation=observation),
    )


@pytest.mark.parametrize("calendar", (False, True))
def test_fingerprint_tracks_literal_values_without_mutating_the_graph(calendar: bool) -> None:
    graph = _graph(calendar=calendar)
    memo = InvocationMemo()
    original = memo.fingerprint(graph)
    before = hash(graph)
    assert memo.fingerprint(replace(graph)) == original
    assert memo.fingerprint(_graph(calendar=calendar, value=7)) != original
    assert memo.fingerprint(graph) == original
    assert hash(graph) == before


def test_fingerprint_retains_calendar_cells_beyond_authority_identity() -> None:
    graph = _graph()
    memo = InvocationMemo()
    original = memo.fingerprint(graph)
    assert graph.signature.observation is not None
    calendar = exact_scope("2026-01-01", "2026-01-03", "UTC").by(mv.grain("day"))._binding
    changed_calendar = replace(
        calendar, cells=calendar.cells[:1], civil_bounds=calendar.civil_bounds[:1]
    )
    axes = (changed_calendar,)
    changed = replace(
        graph,
        root=replace(graph.root, by=axes),
        signature=replace(
            graph.signature,
            axes=axes,
            observation=replace(
                graph.signature.observation,
                coordinates=replace(graph.signature.observation.coordinates, axes=axes),
            ),
        ),
    )
    assert changed_calendar.identity == calendar.identity
    assert memo.fingerprint(changed) != original
    assert memo.fingerprint(graph) == original


def test_plain_encoding_skips_node_dispatch_but_preserves_subclass_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date, timedelta
    from decimal import Decimal
    from enum import Enum
    from typing import TypeGuard

    from marivo._definition import graph
    from marivo._definition.graph import DefinitionNode
    from marivo._temporal import anchor_from_datetime

    calls = 0
    original = graph._is_node

    def is_node(value: object) -> TypeGuard[DefinitionNode]:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(graph, "_is_node", is_node)
    instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = (
        None,
        True,
        1,
        1.0,
        Decimal("1.00"),
        "naïve",
        date(2026, 1, 1),
        instant,
        anchor_from_datetime(instant),
    )
    assert graph._encode(payload, {}) == [
        ["none", ""],
        ["bool", "true"],
        ["int", "0x1"],
        ["float", "1.0"],
        ["decimal", "1E0"],
        ["str", "naïve"],
        ["date", "2026-01-01"],
        ["datetime", "2026-01-01T00:00:00+00:00"],
        ["datetime", "2026-01-01T00:00:00+00:00"],
    ]
    assert calls == 0

    class Label(str, Enum):
        VALUE = "label"

    assert graph._encode(Label.VALUE, {}) == ["str", Label.VALUE]
    assert graph._encode(timedelta(days=2, seconds=3), {}) == ["timedelta", 2, 3, 0]
    assert calls == 2
