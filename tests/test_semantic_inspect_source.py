from __future__ import annotations

import ibis
import pandas as pd

from marivo.analysis.datasources.metadata import (
    ColumnMetadata,
    MetadataWarning,
    PartitionMetadata,
    TableMetadata,
)
from marivo.semantic.evidence import (
    BoundedProfilePolicy,
    MetadataOnlyPolicy,
    SelectedColumnsPolicy,
    TableSource,
)
from marivo.semantic.evidence_store import EvidenceStore
from marivo.semantic.inspect import collect_column_evidence, collect_source_evidence


def _fake_inspect_source(datasource, *, source, include_partitions=True):
    return TableMetadata(
        datasource=datasource,
        table=source.table,
        database=source.database,
        backend_type="duckdb",
        comment="orders fact",
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, "Primary id", 1),
            ColumnMetadata("status", "VARCHAR", True, "Order status", 2),
            ColumnMetadata("amount", "DOUBLE", True, "Gross amount", 3),
        ),
        partitions=(PartitionMetadata("dt", type="DATE"),),
        warnings=(MetadataWarning(kind="nullable_unavailable", message="n/a"),),
    )


def _backend_factory(_name):
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (order_id INT, status VARCHAR, amount DOUBLE)")
    con.con.execute("INSERT INTO orders VALUES (1,'paid',10.0),(2,'paid',20.0),(3,'refunded',NULL)")
    return con


def test_metadata_only_collects_facts_without_profiles(tmp_path):
    store = EvidenceStore(tmp_path)
    pack = collect_source_evidence(
        datasource="warehouse",
        source=TableSource(table="orders"),
        inspect_source=_fake_inspect_source,
        backend_factory=_backend_factory,
        sample_policy=MetadataOnlyPolicy(),
        store=store,
    )
    assert dict(pack.schema)["amount"] == "DOUBLE"
    assert pack.table_comment == "orders fact"
    assert pack.partition_hints == ("dt",)
    assert pack.column_profiles == ()
    assert isinstance(pack.sample_policy, MetadataOnlyPolicy)
    assert pack.metadata_warnings  # carries the nullable_unavailable warning
    # persisted and retrievable
    assert store.read_pack(pack.evidence_refs[0].id) is not None


def test_bounded_profile_collects_sample_scoped_profiles(tmp_path):
    pack = collect_source_evidence(
        datasource="warehouse",
        source=TableSource(table="orders"),
        inspect_source=_fake_inspect_source,
        backend_factory=_backend_factory,
        sample_policy=BoundedProfilePolicy(limit=100),
        store=EvidenceStore(tmp_path),
    )
    by_col = {p.column: p for p in pack.column_profiles}
    assert by_col["amount"].null_count == 1
    assert by_col["status"].distinct_count == 2
    assert ("paid", 2) in by_col["status"].top_values
    assert by_col["amount"].sample_scope == "bounded_sample"
    assert by_col["amount"].approximate is True


def test_max_profiled_columns_skips_extra_columns_with_warning(tmp_path):
    pack = collect_source_evidence(
        datasource="warehouse",
        source=TableSource(table="orders"),
        inspect_source=_fake_inspect_source,
        backend_factory=_backend_factory,
        sample_policy=BoundedProfilePolicy(limit=100, max_profiled_columns=1),
        store=EvidenceStore(tmp_path),
    )
    assert len(pack.column_profiles) == 1
    assert any("skipped" in w for w in pack.metadata_warnings)


def test_source_max_profiled_columns_selects_only_profiled_columns(tmp_path):
    selected_columns = []

    class FakeTable:
        def select(self, *columns):
            selected_columns.extend(columns)
            return self

        def limit(self, _limit):
            return self

        def execute(self):
            return pd.DataFrame({"order_id": [1, 2, 3]})

    class FakeBackend:
        def table(self, _table):
            return FakeTable()

    pack = collect_source_evidence(
        datasource="warehouse",
        source=TableSource(table="orders"),
        inspect_source=_fake_inspect_source,
        backend_factory=lambda _name: FakeBackend(),
        sample_policy=BoundedProfilePolicy(limit=2, max_profiled_columns=1),
        store=EvidenceStore(tmp_path),
    )

    assert selected_columns == ["order_id"]
    assert [profile.column for profile in pack.column_profiles] == ["order_id"]
    assert pack.truncated is True
    assert any("skipped" in w for w in pack.metadata_warnings)


def test_source_timeout_budget_skips_backend_and_persists_partial_pack(tmp_path):
    def backend_factory(_name):
        raise AssertionError("backend_factory should not be called")

    store = EvidenceStore(tmp_path)
    pack = collect_source_evidence(
        datasource="warehouse",
        source=TableSource(table="orders"),
        inspect_source=_fake_inspect_source,
        backend_factory=backend_factory,
        sample_policy=BoundedProfilePolicy(limit=100, timeout_seconds=0),
        store=store,
    )

    assert pack.column_profiles == ()
    assert any("timeout" in warning for warning in pack.metadata_warnings)
    assert store.read_pack(pack.evidence_refs[0].id) is not None


def test_column_max_profiled_columns_skips_later_columns_without_reading_them(tmp_path):
    selected_columns = []

    class FakeTable:
        def select(self, *columns):
            selected_columns.extend(columns)
            return self

        def limit(self, _limit):
            return self

        def execute(self):
            return pd.DataFrame({"order_id": [1, 2, 3]})

    class FakeBackend:
        def table(self, _table):
            return FakeTable()

    evidence = collect_column_evidence(
        datasource="warehouse",
        source=TableSource(table="orders"),
        columns=("order_id", "status", "amount"),
        inspect_source=_fake_inspect_source,
        backend_factory=lambda _name: FakeBackend(),
        sample_policy=SelectedColumnsPolicy(
            limit=100,
            columns=("order_id", "status", "amount"),
            max_profiled_columns=1,
        ),
        store=EvidenceStore(tmp_path),
    )

    assert selected_columns == ["order_id"]
    assert [item.column for item in evidence] == ["order_id", "status", "amount"]
    assert evidence[0].profile.sample_scope == "bounded_sample"
    for item in evidence[1:]:
        assert item.profile.sample_scope == "none"
        assert any("skipped" in warning for warning in item.profile.warnings)
        assert any("max_profiled_columns" in warning for warning in item.profile.warnings)
