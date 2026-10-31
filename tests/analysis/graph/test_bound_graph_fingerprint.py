"""Frozen graph identities retain exact calendar contents and object-local reuse."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

import pytest

import marivo.analysis as mv
from marivo._definition.graph import computation_fingerprint
from marivo._definition.observations import CoordinateBinding, ObservationBinding
from marivo._definition.query import LiteralValues, ResultQuery
from marivo._definition.relations import QueryNode
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
def test_fingerprint_computes_once_without_affecting_value_semantics(
    monkeypatch: pytest.MonkeyPatch, calendar: bool
) -> None:
    graph = _graph(calendar=calendar)
    equal = replace(graph)
    before_hash, before_repr = hash(graph), repr(graph)
    calls = 0
    original = computation_fingerprint

    def fingerprint(node: QueryNode) -> str:
        nonlocal calls
        calls += 1
        return original(node)

    monkeypatch.setattr("marivo.analysis.core.graph.computation_fingerprint", fingerprint)
    memo = InvocationMemo()
    first = memo.fingerprint(graph)
    assert first == (
        "71f0a254fccc7eafd9e9e9cb2c10e797e3a2e35de5a1614caa373b96cac101bb"
        if calendar
        else "7dea064422d12e293c2ceb73ed8fc4464994adb3219e3d15730e046e3b5c2ba4"
    )
    assert memo.fingerprint(graph) == first
    assert calls == 1
    assert hash(graph) == before_hash and repr(graph) == before_repr
    assert graph == equal
    assert memo.fingerprint(equal) == first
    assert calls == 2
    changed = _graph(calendar=calendar, value=7)
    assert memo.fingerprint(changed) != first
    assert memo.fingerprint(graph) == first
    before_fresh = calls
    assert InvocationMemo().fingerprint(graph) == first
    assert calls == before_fresh + 1
    with pytest.raises(FrozenInstanceError):
        graph.__setattr__("root", changed.root)


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


@pytest.mark.parametrize("calendar", (False, True))
def test_encoded_digest_preserves_existing_canonical_bytes(calendar: bool) -> None:
    from marivo._definition import graph as definition_graph

    graph = _graph(calendar=calendar)
    values = (graph.root, graph.signature.observation)
    for value in values:
        encoded = definition_graph._encode(value, {})
        assert definition_graph._encoded_fingerprint(encoded) == definition_graph.fingerprint(
            encoded
        )


def test_encoding_shares_calendar_traversal_only_inside_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo._definition import graph
    from marivo._definition.types import LiteralValue, literal_token

    calendar = exact_scope("2026-01-01", "2026-01-03", "UTC").by(mv.grain("day"))._binding
    calls = 0
    original = literal_token

    def token(value: LiteralValue) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(graph, "literal_token", token)
    once = graph._encode(calendar, {})
    once_calls = calls
    calls = 0
    shared = graph._encode((calendar, calendar, replace(calendar)), {})
    assert isinstance(shared, list)
    assert shared == [once, once, once]
    assert (
        graph.fingerprint(shared)
        == "0e5c22944fc065ceb2ebbda93992b68c86a6151ed21c17776cb12de7eeeb977f"
    )
    assert calls == 2 * once_calls
    calls = 0
    assert graph._encode(calendar, {}) == once
    assert calls == once_calls


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
