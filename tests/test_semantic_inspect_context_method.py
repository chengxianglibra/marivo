from __future__ import annotations

from pathlib import Path

import ibis

import marivo.datasource as md
import marivo.semantic as ms
from marivo.semantic.dtos import ColumnContext, TableContext
from marivo.semantic.ledger import LedgerStore
from marivo.semantic.reader import SemanticProject


def _seed_project(tmp_path: Path) -> SemanticProject:
    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    try:
        con.con.execute(
            "CREATE TABLE orders (order_id INT, status VARCHAR, amount DOUBLE, note VARCHAR)"
        )
        con.con.execute(
            "INSERT INTO orders VALUES "
            "(1, 'paid', 10.0, ''),"
            "(2, 'paid', 20.0, 'vip'),"
            "(3, 'refunded', NULL, 'late'),"
            "(4, 'pending', 40.0, NULL),"
            "(5, 'paid', 50.0, 'gift'),"
            "(6, 'paid', 60.0, 'bulk'),"
            "(7, 'paid', 999.0, 'outside-sample')"
        )
        con.con.execute("COMMENT ON TABLE orders IS 'orders fact'")
        con.con.execute("COMMENT ON COLUMN orders.amount IS 'Gross amount'")
    finally:
        con.disconnect()

    datasource_dir = tmp_path / ".marivo" / "datasource"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "warehouse = md.DatasourceSpec("
        "name='warehouse', backend_type='duckdb', path="
        f"{str(db_path)!r})\n"
        "md.datasource(warehouse)\n"
    )
    (tmp_path / ".marivo" / "semantic").mkdir(parents=True, exist_ok=True)
    return SemanticProject(workspace_dir=tmp_path)


def test_inspect_table_returns_basic_metadata_without_preview_side_effect(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)

    result = project.inspect_table(md.ref("warehouse"), ms.table("orders"))

    assert isinstance(result, TableContext)
    assert result.datasource == "warehouse"
    assert result.table == ms.table("orders")
    assert result.table_comment == "orders fact"
    assert result.columns == ("order_id", "status", "amount", "note")
    assert result.column_comments["amount"] == "Gross amount"
    assert LedgerStore(project.semantic_root).read_raw_previews() == ()


def test_inspect_table_accepts_datasource_name_string(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)

    result = project.inspect_table("warehouse", ms.table("orders"))

    assert result.datasource == "warehouse"
    assert result.columns[0] == "order_id"


def test_inspect_columns_defaults_to_all_columns_and_samples_five_rows(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)

    result = project.inspect_columns("warehouse", ms.table("orders"))

    assert all(isinstance(item, ColumnContext) for item in result)
    assert [item.column for item in result] == ["order_id", "status", "amount", "note"]
    by_column = {item.column: item for item in result}
    assert by_column["status"].sample_values == (
        "paid",
        "paid",
        "refunded",
        "pending",
        "paid",
    )
    assert by_column["amount"].null_count == 1
    assert by_column["amount"].min_value == 10.0
    assert by_column["amount"].max_value == 50.0
    assert not hasattr(by_column["status"], "distinct_count")
    assert not hasattr(by_column["status"], "top_values")
    assert not hasattr(by_column["status"], "empty_count")
    assert not hasattr(by_column["status"], "sample_row_count")
    assert not hasattr(by_column["status"], "approximate")


def test_inspect_columns_can_select_columns_in_requested_order(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)

    result = project.inspect_columns(
        "warehouse",
        ms.table("orders"),
        columns=("amount", "status"),
    )

    assert [item.column for item in result] == ["amount", "status"]
    assert result[0].comment == "Gross amount"


def test_inspect_columns_returns_warning_context_for_missing_columns(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)

    result = project.inspect_columns(
        "warehouse",
        ms.table("orders"),
        columns=("missing_column",),
    )

    assert len(result) == 1
    missing = result[0]
    assert missing.column == "missing_column"
    assert missing.data_type == "UNKNOWN"
    assert missing.sample_values == ()
    assert missing.null_count is None
    assert missing.min_value is None
    assert missing.max_value is None
    assert missing.warnings == ("column absent from source schema",)
