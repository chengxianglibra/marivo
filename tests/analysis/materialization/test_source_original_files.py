"""Typed file originals reduce at the source without input stability gates."""

from pathlib import Path

import pytest

from marivo.datasource.adapters import SourceSession
from tests.analysis.graph.physical_workloads import workload
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("profile", ("csv", "parquet", "local-json"))
def test_file_original_rollup_returns_compact_result(
    profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", profile, 1000, "baseline", tmp_path, monkeypatch) as work:
        amount = catalog_value(work.session.catalog, "cost.facts.amount", kind="field")
        owner = catalog_value(work.session.catalog, "cost.facts.owner", kind="field")
        fine = work.session.observe({"value": amount.sum()}, by=(owner,))
        trace = capture_source(monkeypatch)
        frame = work.session.observe({"value": amount.sum()}).compute().to_pandas()
        expected = sum(row["amount"] for row in work.rows if isinstance(row["amount"], int))
        assert frame["value"].tolist() == [expected]
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        assert not any("__original_value" in names for names in trace.returned_columns)


def test_file_original_retains_default_collation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "parquet", 1000, "baseline", tmp_path, monkeypatch) as work:
        amount = catalog_value(work.session.catalog, "cost.facts.amount", kind="field")
        owner = catalog_value(work.session.catalog, "cost.facts.owner", kind="field")
        fine = work.session.observe({"value": amount.sum()}, by=(owner,))
        trace = capture_source(monkeypatch)
        enter = SourceSession.__enter__

        def entered(source: SourceSession) -> SourceSession:
            result = enter(source)
            result._backend.raw_sql("SET default_collation='nocase'")
            return result

        monkeypatch.setattr(SourceSession, "__enter__", entered)
        frame = work.session.observe({"value": amount.sum().per(owner).sum()}).compute().to_pandas()
        expected = sum(row["amount"] for row in work.rows if isinstance(row["amount"], int))
        assert frame["value"].tolist() == [expected]
        assert trace.returned_rows
        assert trace.qualifications


def test_view_original_retains_unknown_inherited_collation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "view", 1000, "baseline", tmp_path, monkeypatch) as work:
        amount = catalog_value(work.session.catalog, "cost.facts.amount", kind="field")
        owner = catalog_value(work.session.catalog, "cost.facts.owner", kind="field")
        fine = work.session.observe({"value": amount.sum()}, by=(owner,))
        trace = capture_source(monkeypatch)
        frame = work.session.observe({"value": amount.sum().per(owner).sum()}).compute().to_pandas()
        expected = sum(row["amount"] for row in work.rows if isinstance(row["amount"], int))
        assert frame["value"].tolist() == [expected]
        assert trace.returned_rows
        assert trace.qualifications
