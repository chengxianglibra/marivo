"""Single-call retained reads preserve projections and independent recovery."""

import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import FindingNotFoundError
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.materialization import graph_dataset, graph_findings, graph_storage
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.graph_dataset import GraphDataset
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.analysis.materialization.graph_protocol import Descriptor, ValidatedDescriptor
from marivo.analysis.materialization.store import SessionStore
from tests.shared_fixtures import DslCase, DslCaseFactory

_Kind = Literal["association", "forecast", "runs", "deviation"]


def _capture(case: DslCase, kind: _Kind) -> tuple[GraphDataset, tuple[GraphDataset, ...]]:
    if kind == "deviation":
        orders = case.session.members(ms.ref.entity("sales.order"))
        deviation = (
            orders.read(ms.ref.measure("sales.order.amount")).deviation(method="zscore").execute()
        )
        assert deviation._dataset is not None
        dataset = deviation._dataset
        return dataset, (
            replace(dataset, projection="observed"),
            replace(dataset, projection="reference"),
            replace(dataset, projection="deviation"),
            replace(dataset, projection="score"),
        )

    members = case.session.members(ms.ref.entity("sales.customer"))
    via = ms.ref.relationship("sales." + case.names.buyer)
    if kind == "association":
        during = mv.time_scope(start="2026-08-01", end="2026-09-01")
        revenue = members.observe(
            ms.ref.metric("sales.revenue"),
            during=during,
            via=via,
            by=(ms.ref.entity("sales.customer"),),
        )
        count = members.observe(
            ms.ref.metric("sales.order_count"),
            during=during,
            via=via,
            by=(ms.ref.entity("sales.customer"),),
        )
        association = revenue.correlate(count, method="pearson").execute()
        assert association._dataset is not None
        dataset = association._dataset
        return dataset, (
            replace(dataset, projection="coefficient"),
            replace(dataset, projection="selected"),
        )

    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    history = members.observe(
        ms.ref.metric("sales.order_count"),
        during=grid,
        via=via,
        by=(ms.ref.entity("sales.customer"),),
    )
    if kind == "forecast":
        forecast = history.forecast(horizon=mv.periods(2), model=mv.naive()).execute()
        assert forecast._dataset is not None
        dataset = forecast._dataset
        return dataset, (
            replace(dataset, projection="prediction"),
            replace(dataset, projection="lower"),
            replace(dataset, projection="upper"),
        )
    runs = history.runs(where=history.value.gt(-1)).execute()
    assert runs._dataset is not None
    dataset = runs._dataset
    return dataset, (
        replace(dataset, projection="start"),
        replace(dataset, projection="end"),
        replace(dataset, projection="count"),
        replace(dataset, projection="duration"),
    )


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ("association", "forecast", "runs", "deviation"))
def test_projection_reads_each_receipt_once_per_call(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, kind: _Kind
) -> None:
    case = analysis_dsl_case_factory("j4" if kind == "association" else "j2")
    dataset, projections = _capture(case, kind)
    expected = tuple(projected.to_pandas() for projected in projections)
    receipts = (
        dataset.artifact.descriptor.primary_receipt.local,
        *(part.local for part in dataset.artifact.descriptor.parts),
    )
    wanted = Counter(receipt.project_relative_path for receipt in receipts)
    reads: list[Descriptor] = []
    tables: list[str] = []
    read_result = graph_storage.read_result
    read_table = graph_storage.read_table

    def counted_result(
        root: Path, descriptor: Descriptor, *, _validated: ValidatedDescriptor | None = None
    ) -> ExchangeResult:
        reads.append(descriptor)
        return read_result(root, descriptor, _validated=_validated)

    def counted_table(root: Path, receipt: LocalReceipt) -> pa.Table:
        tables.append(receipt.project_relative_path)
        return read_table(root, receipt)

    monkeypatch.setattr(graph_dataset, "read_result", counted_result)
    monkeypatch.setattr(graph_storage, "read_table", counted_table)
    before = case.session.runs().items
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    for projected, frame in zip(projections, expected, strict=True):
        for _ in range(2):
            reads.clear()
            tables.clear()
            pd.testing.assert_frame_equal(projected.to_pandas(), frame)
            assert len(reads) == 1
            assert Counter(tables) == wanted
    assert case.session.runs().items == before

    for receipt in (receipts[0], *receipts[1:2]):
        path = case.root / receipt.project_relative_path / "data.parquet"
        original = path.read_bytes()
        try:
            path.write_bytes(b"damaged retained projection")
            with pytest.raises(IntegrityError):
                projections[0].to_pandas()
        finally:
            path.write_bytes(original)
    other = dataset.runtime.store.create_session("foreign-reader")
    foreign = DatasetRuntime(dataset.runtime.store, other.session_ref)
    with pytest.raises(IntegrityError, match="another Session"):
        replace(projections[0], runtime=foreign).to_pandas()
    assert case.session.runs().items == before


@pytest.mark.runtime
def test_multiple_findings_read_once_and_keep_owned_paging(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    dataset, _ = _capture(case, "forecast")
    collection = graph_findings.collection
    calls = 0

    def counted_collection(
        store: SessionStore,
        connection: sqlite3.Connection,
        descriptor: Descriptor,
        artifact_ref: str,
        *,
        _validated: ValidatedDescriptor | None = None,
    ) -> tuple[tuple[t.Finding, ...], t.ArtifactDigest]:
        nonlocal calls
        calls += 1
        return collection(store, connection, descriptor, artifact_ref, _validated=_validated)

    monkeypatch.setattr(graph_findings, "collection", counted_collection)
    digest = dataset.evidence_digest()
    assert calls == 1 and digest.finding_count == 8
    calls = 0
    first = dataset.findings(limit=3)
    assert calls == 1 and len(first.items) == 3 and first.has_more
    assert first.next_cursor is not None
    calls = 0
    second = dataset.findings(limit=3, cursor=first.next_cursor)
    assert calls == 1 and len(second.items) == 3
    assert not {item.finding_id for item in first.items}.intersection(
        item.finding_id for item in second.items
    )
    calls = 0
    assert dataset.finding(first.items[0].finding_id) == first.items[0]
    assert calls == 1
    calls = 0
    with pytest.raises(FindingNotFoundError):
        dataset.finding("foreign-finding")
    assert calls == 1
    calls = 0
    with pytest.raises(IntegrityError, match="cursor"):
        dataset.findings(cursor="invalid")
    assert calls == 1


@pytest.mark.runtime
def test_cold_projection_reads_once_without_source(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    dataset, projections = _capture(case, "forecast")
    expected = projections[0].to_pandas().to_json(orient="split", date_format="iso")
    before = case.session.runs().items
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    code = """
import sys
from pathlib import Path
import marivo.analysis as mv
from marivo.analysis.materialization import graph_dataset, graph_storage
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.analysis.materialization.graph_protocol import Descriptor, ValidatedDescriptor

session = mv.session.resume(sys.argv[1], by="id")
result = session.artifact(sys.argv[2])
reads = []
original = graph_storage.read_result
def counted(root: Path, descriptor: Descriptor, *, _validated: ValidatedDescriptor | None = None) -> ExchangeResult:
    reads.append(descriptor)
    return original(root, descriptor, _validated=_validated)
graph_dataset.read_result = counted
for _ in range(2):
    reads.clear()
    frame = result.prediction.to_pandas()
    assert len(reads) == 1
print(frame.to_json(orient="split", date_format="iso"))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, case.session.id, dataset.artifact.artifact_ref],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == expected
    assert case.session.runs().items == before
