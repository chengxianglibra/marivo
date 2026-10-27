"""Exact source identities and UInt64 quantities through public Frame execution."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo._help.render import render_help_text
from marivo.analysis.core.data_types import DataColumn, DecimalDataType
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import admit_source_type
from marivo.datasource.adapters import _require_decimal_transport
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value

DOMAIN = "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"


@pytest.mark.parametrize("unit,ticks", [("ns", 1761442200000000001), ("us", 1761442200000001)])
def test_naive_parquet_identity_keeps_ticks_independent_of_time_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
    unit: str,
    ticks: int,
) -> None:
    path = tmp_path / "events.parquet"
    # Adjacent nanoseconds must remain different identities, including in a DST fold.
    stamps = pa.array([ticks, ticks + 1], type=pa.timestamp(unit))
    pq.write_table(pa.table({"tenant": [1, 1], "ts": stamps, "amount": [7, 11]}), path)
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
            "sales/_domain.py": DOMAIN,
            "sales/events.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"events=ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.parquet({str(path)!r}), primary_key=['tenant','ts'])\n"
            + (
                "ts=ms.time_dimension_column(name='ts', entity=events, column='ts', parse=ms.timestamp(timezone='Europe/Berlin'), is_default=True)\n"
                if unit == "us"
                else ""
            )
            + "amount=ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())\n"
            + f"@ms.entity(datasource=ms.ref.datasource('warehouse'), source=md.parquet({str(path)!r}), primary_key=['tenant','ts'])\ndef aware(raw):\n    return raw.mutate(ts=raw.ts.cast(\"timestamp('UTC')\"))\n",
        }
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("naive-identity", timezone="America/New_York")
    entity = session.catalog.get("entity:sales.events").value
    amount = catalog_value(session.catalog, "sales.events.amount", kind="field")
    members = session.members(entity)
    fixed = session.read(members, {"amount": amount}).compute()
    rows = fixed.to_pandas()
    key = rows.columns[0]
    actual = [coordinate[1] for coordinate in rows[key]]
    assert all(value.type == pa.timestamp(unit) for value in actual)
    assert [value.cast(pa.int64()).as_py() for value in actual] == stamps.cast(
        pa.int64()
    ).to_pylist()
    if unit == "us":
        time = catalog_value(session.catalog, "sales.events.ts", kind="field")
        scope = session.time_scope(start="2025-10-25T23:00:00Z", end="2025-10-26T23:00:00Z")
        assert session.observe({"total": amount.sum(time=time)}, by=(scope,)).compute().to_pandas()[
            "total"
        ].tolist() == [18]
    saved = fixed.persist()
    restored = session.persisted_frame(saved.artifact_id).compute()
    assert restored.to_pandas()[key].tolist() == rows[key].tolist()
    selected = session.population_from(restored, on=((restored.axes[0], entity),))
    assert session.observe(
        {"total": amount.sum()}, where=mv.in_population(selected)
    ).compute().to_pandas()["total"].tolist() == [18]
    aware = session.catalog.get("entity:sales.aware").value
    incompatible = session.population_from(restored, on=((restored.axes[0], aware),))
    with pytest.raises(CoreRuleError, match="naive and aware"):
        session.observe({"count": aware.count()}, where=mv.in_population(incompatible)).compute()


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("clickhouse", "mergetree")])
)
def test_uint64_identity_quantity_and_arithmetic_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
    backend: str,
    profile: str,
) -> None:
    values = [0, 2**53 + 1, 2**63, 2**64 - 1]
    data = SourceData(
        "id UBIGINT, amount UBIGINT, encoded_day UBIGINT",
        ",".join(f"({value},{value},20260101)" for value in values) + ",(1,NULL,20260101)",
        "id UInt64, amount Nullable(UInt64), encoded_day UInt64",
        [{"id": value, "amount": value, "encoded_day": 20260101} for value in values]
        + [{"id": 1, "amount": None, "encoded_day": 20260101}],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": DOMAIN,
                "sales/events.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"events=ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + "amount=ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all())\n"
                + "day=ms.time_dimension_column(name='day', entity=events, column='encoded_day', parse=ms.strptime('%Y%m%d'))\n",
            }
        )
        session = mv.session.get_or_create("unsigned", timezone="UTC")
        entity = session.catalog.get("entity:sales.events").value
        amount = catalog_value(session.catalog, "sales.events.amount", kind="field")
        members = session.members(entity)
        fixed = session.read(members, {"amount": amount}).compute()
        rows = fixed.to_pandas()
        assert set(rows.iloc[:, 0]) == {Decimal(value) for value in [*values, 1]}
        assert sorted(value for value in rows["amount"] if not pd.isna(value)) == [
            Decimal(value) for value in values
        ]
        assert rows["amount"].isna().sum() == 1
        expected = Decimal(27679123309819068416)
        totals = (
            session.observe(
                {
                    "total": amount.sum(),
                    "negative": (amount - amount - 1).sum(),
                    "double": (amount * 2).sum(),
                    "wide": (amount * 100000).max(),
                }
            )
            .compute()
            .to_pandas()
        )
        assert totals["total"].tolist() == [expected]
        day = catalog_value(session.catalog, "sales.events.day", kind="field")
        scope = session.time_scope(start="2026-01-01", end="2026-01-02")
        assert session.observe({"total": amount.sum(time=day)}, by=(scope,)).compute().to_pandas()[
            "total"
        ].tolist() == [expected]
        assert totals["negative"].tolist() == [Decimal(-4)]
        assert totals["double"].tolist() == [expected * 2]
        assert totals["wide"].tolist() == [Decimal(1844674407370955161500000)]
        assert session.observe({"large": amount.sum()}, where=amount > 2**63).compute().to_pandas()[
            "large"
        ].tolist() == [Decimal(2**64 - 1)]
        overflow = session.observe({"overflow": (amount * amount).max()})
        if backend == "clickhouse":
            assert overflow.compute().to_pandas()["overflow"].tolist() == [
                Decimal(85070591730234615865843651857942052864)
            ]
        else:
            with pytest.raises(MaterializationError):
                overflow.compute()
        saved = fixed.persist()
        reopened = session.persisted_frame(saved.artifact_id).compute()
        selected = session.population_from(reopened, on=((reopened.axes[0], entity),))
        assert session.observe(
            {"total": amount.sum()}, where=mv.in_population(selected)
        ).compute().to_pandas()["total"].tolist() == [expected]


def test_decimal_transport_rejects_float_and_accepts_exact_unsigned() -> None:
    columns = (DataColumn("value", "value", DecimalDataType(20, 0), True),)
    source = pd.DataFrame(
        {
            "value": pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array([2**64 - 1, None], type=pa.uint64()))
            )
        }
    )
    result = StageOutput.from_source_frame(source, columns, ())
    assert result.table["value"].iloc[0] == Decimal(2**64 - 1)
    assert result.table["value"].isna().sum() == 1
    with pytest.raises(DatasourceSourceCapabilityError, match="floating transport") as caught:
        _require_decimal_transport(
            pd.DataFrame({"value": [float(2**64 - 1)]}),
            pa.schema([pa.field("value", pa.decimal128(20, 0))]),
        )
    assert "precision cannot be recovered" in caught.value.repair.action


def test_source_type_repairs_and_help_explain_exact_conversions() -> None:
    with pytest.raises(MaterializationError) as caught:
        admit_source_type(pa.field("payload", pa.binary()), "sales.events.payload")
    assert "Entity body" in caught.value.repair.action
    assert ".cast('string')" in caught.value.repair.action
    with pytest.raises(MaterializationError) as caught:
        admit_source_type(pa.field("amount", pa.uint64()), "sales.events.amount")
    assert ".cast('decimal(20,0)')" in caught.value.repair.action
    assert "unchecked int64" in caught.value.repair.action
    for target, required in (
        ("semantic.entity.define", ("UInt64", "ibis.dtype", "submodules", "primary_key")),
        ("semantic.entity.primary_key", ("Naive", "precision", "aware")),
        ("semantic.timestamp", ("does not rewrite Entity primary_key",)),
        ("analysis.analysis_frame.compute", ("UInt64", "naive", "semantic.entity.define")),
    ):
        text, _, _ = render_help_text(target)
        assert all(fragment in text for fragment in required), target
