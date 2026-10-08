"""Opt-in snapshot measurements: run this file explicitly with pytest -m runtime -s."""

from __future__ import annotations

import json
import os
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import topology
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import graph_protocol as protocol
from tests.shared_fixtures import DslCaseFactory

T = TypeVar("T")


def _median(call: Callable[[], T]) -> tuple[T, float]:
    result = call()
    elapsed = []
    for _ in range(3):
        start = time.perf_counter()
        result = call()
        elapsed.append((time.perf_counter() - start) * 1000)
    return result, statistics.median(elapsed)


@pytest.mark.runtime
def test_measure_snapshot(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                (f"june_{member}", member, "web", "paid", "2026-06-10T12:00:00+00:00", 0)
                for member in ("A", "B", "C", "D")
            ],
        )
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))

    def observe(month: int) -> mv.LogicalNumericRelation:
        value = members.observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        assert isinstance(value, mv.LogicalNumericRelation)
        return value

    current, baseline = observe(8), observe(7)
    results = []

    def measure(name: str, build: Callable[[], mv.LogicalNumericRelation]) -> None:
        relation, construction = _median(build)
        root = relation._node.definition
        frozen, encoding = _median(lambda: protocol.freeze_graph(root))
        restored, decoding = _median(lambda: protocol.thaw_graph(frozen))
        _, validation = _median(lambda: topology(restored))
        document = protocol.graph_document(root)
        raw = protocol.encode(document, protocol.GRAPH)
        unique = len(document.nodes)
        edges = sum(
            len(n.inputs) + len(n.sources) + len(n.retained_endpoints)
            for n in document.nodes
            if n.kind == "method"
        )
        endpoints = 0
        occurrences = unique
        snapshot = protocol.Continuation(
            "marivo.analysis.continuation/v5",
            frozen,
            (),
            (),
            (),
            (),
            "input",
        )
        publication_error: str | None = None
        published_bytes: int | None = None
        try:
            published = relation.execute()
            assert published._dataset is not None
            published_bytes = len(
                published._dataset.artifact.descriptor.continuation_snapshot.encode()
            )
        except AnalysisError as error:
            publication_error = str(error)

        results.append(
            {
                "case": name,
                "definition_bytes": len(raw.encode()),
                "endpoint_bytes": endpoints,
                "continuation_bytes": len(protocol.encode(snapshot, protocol.SNAPSHOT).encode()),
                "published_continuation_bytes": published_bytes,
                "publication_error": publication_error,
                "unique_nodes": unique,
                "references": edges,
                "encoded_occurrences": occurrences,
                "construction_ms": construction,
                "encode_ms": encoding,
                "decode_ms": decoding,
                "validation_ms": validation,
            }
        )

        output = os.environ.get("MARIVO_SNAPSHOT_BENCHMARK")
        if output:
            Path(output).write_text(json.dumps(results, indent=2) + "\n")

    measure("observation", lambda: current)
    measure("difference", lambda: current.compare(baseline))
    measure(
        "nested_difference",
        lambda: current.compare(baseline).compare(observe(7).compare(observe(6))),
    )
    for depth in (1, 3, 5):

        def shared(depth: int = depth) -> mv.LogicalNumericRelation:
            node = current.compare(baseline)
            for _ in range(depth):
                node = node.ratio(node)
            return node

        measure(f"shared_{depth}", shared)
    base = ms.ref.metric("sales.revenue")
    for name, metric in (
        ("ratio", mv.runtime_metric.ratio(base, base, label="ratio")),
        ("linear", mv.runtime_metric.linear(add=[base, base], label="linear")),
    ):
        left = members.observe(
            metric,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        right = members.observe(
            metric,
            during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        measure(name, lambda left=left, right=right: left.compare(right))
    first, second = current.execute(), baseline.execute()
    measure("fixed_difference", lambda: first.compare(second))
    payload = json.dumps(results, indent=2)
    print(payload)
    output = os.environ.get("MARIVO_SNAPSHOT_BENCHMARK")
    if output:
        Path(output).write_text(payload + "\n")
