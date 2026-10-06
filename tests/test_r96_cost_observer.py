"""Independent checks of R9.6 native, buffer and receipt measurement boundaries."""

import json
import os
import sys
from contextlib import closing
from pathlib import Path

import ibis
import numpy as np
import pyarrow as pa
import pytest

import marivo.datasource.adapters as adapters
from devtools.r96_cost_observer import CostObserver, _ranges, _shared_bytes, _union_size
from marivo.analysis.materialization.graph_storage import read_table, write_table
from marivo.datasource.adapters import PhysicalRequirement, SourceSession, provider_for
from marivo.datasource.runtime import _disconnect
from tests.r9_source_cases import datasource, source_case


def test_observes_actual_submissions_batches_fetches_and_owner_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with (
        CostObserver("duckdb") as observer,
        source_case("duckdb", "table", tmp_path, monkeypatch) as case,
    ):
        binding = case.session.bind(case.source, source_identity="cost_facts")
        qualified = case.session.qualify(
            binding, PhysicalRequirement("cost_observation", 1, frozenset({"scan"}))
        )
        expression = binding.relation.order_by("id", "tenant")
        read = case.session.compile(
            qualified,
            expression,
            purpose="cost_observation",
            expected_schema=expression.schema().to_pyarrow(),
        )
        with closing(case.session.batches(read, chunk_size=2)) as stream:
            batches = list(stream)
    observed = observer.snapshot()
    assert len(batches) == 2 and sum(batch.num_rows for batch in batches) == 3
    assert [item["rows"] for item in observer.batches] == [2, 1]
    assert all(
        item["arrow_nbytes"] == batch.nbytes
        for item, batch in zip(observer.batches, batches, strict=True)
    )
    assert [item["rows"] for item in observer.fetches] == [2, 1, 0]
    assert observer._audit is not None
    business = [item for item in observer._audit.submissions if item.purpose == "cost_observation"]
    assert len(business) == 1 and business[0].category == "governed_ibis"
    assert business[0].sql == read.sql
    assert observer.sessions and all(item["closed"] for item in observer.sessions)
    assert all(item["active_readers"] == 0 for item in observer.sessions)
    assert {item["phase"] for item in observer.phases} >= {"bind", "compile", "close"}
    assert observed["resource_closed"] is True
    json.dumps(observed)


def test_receipt_bytes_include_each_part_and_keep_arrow_bytes_separate(tmp_path: Path) -> None:
    tables = {
        "primary": pa.table({"value": [1, 2, 3]}),
        "required_state": pa.table({"member": ["one", "two"], "state": [4, 5]}),
    }
    with CostObserver("duckdb") as observer:
        for role, table in tables.items():
            receipt = write_table(tmp_path, tmp_path / (role + "_staging"), tmp_path / role, table)
            os.rename(tmp_path / (role + "_staging"), tmp_path / role)
            assert read_table(tmp_path, receipt).equals(table)
    writes = [item for item in observer.storage if item["operation"] == "write"]
    reads = [item for item in observer.storage if item["operation"] == "read"]
    assert len(writes) == len(reads) == 2
    for written, read in zip(writes, reads, strict=True):
        observed_receipt = written["receipt"]
        assert isinstance(observed_receipt, dict)
        role = observed_receipt["project_relative_path"]
        assert isinstance(role, str)
        assert written["arrow_nbytes"] == tables[role].nbytes
        assert written["parquet_bytes"] == (tmp_path / role / "data.parquet").stat().st_size
        assert observed_receipt["realized_byte_count"] == (
            (tmp_path / role / "data.parquet").stat().st_size
            + (tmp_path / role / "manifest.json").stat().st_size
        )
        assert written["receipt"] == read["receipt"]


def test_buffer_union_and_real_conversions_do_not_count_views_as_copies() -> None:
    array = np.arange(8, dtype=np.int64)
    table = pa.table({"value": array})
    sliced = table.slice(2, 3)
    assert _union_size((*_ranges(table), *_ranges(sliced), *_ranges(array))) == array.nbytes
    assert _shared_bytes(table, sliced) == array.nbytes
    with CostObserver("duckdb") as observer:
        frame = table.to_pandas()
        view = frame.to_numpy(copy=False)
        copied = frame.to_numpy(copy=True)
        isolated = frame.copy(deep=True)
        table.to_pylist()
    assert observer.conversions["arrow_to_pandas"] == 1
    observed_conversions = observer.snapshot()["conversions"]
    assert isinstance(observed_conversions, dict)
    assert observed_conversions["arrow_to_python_call_count"] is None
    assert observer.conversions["pandas_to_numpy"] == 2
    numpy_events = [item for item in observer.ownership if item["operation"] == "pandas_to_numpy"]
    assert numpy_events[0]["shared_buffer_bytes"] == view.nbytes
    assert numpy_events[1]["shared_buffer_bytes"] == 0
    assert not np.shares_memory(view, copied)
    assert _shared_bytes(frame, isolated) == 0


def test_failure_restores_profile_and_native_hook() -> None:
    original_profile = sys.getprofile()
    original_cursor = adapters._native_cursor
    observer = CostObserver("duckdb")
    with pytest.raises(RuntimeError, match="cost measurement failure"), observer:
        raise RuntimeError("cost measurement failure")
    assert sys.getprofile() is original_profile
    assert adapters._native_cursor is original_cursor
    assert observer._thread is not None and not observer._thread.is_alive()
    assert observer._elapsed is not None
    with pytest.raises(RuntimeError, match="single use"):
        observer.__enter__()


def test_external_backend_owner_disconnect_is_recorded_after_session_close() -> None:
    backend = ibis.duckdb.connect()
    owner = SourceSession(
        provider_for("duckdb"), datasource("duckdb", {}), backend, owns_backend=False
    )
    try:
        with CostObserver("duckdb") as observer:
            with owner:
                pass
            assert owner._closed and not owner._backend_disconnected
            assert _disconnect(backend) is True
    finally:
        backend.disconnect()
    assert observer.snapshot()["resource_closed"] is True
    assert observer.sessions[0]["backend_disconnected"] is True
    assert observer.sessions[0]["adapter_disconnection_acknowledged"] is False
    assert observer.sessions[0]["external_disconnect_observed"] is True
