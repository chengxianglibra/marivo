"""Native source value types survive Frame kernels and durable completion."""

import math
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_types import DataType, DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value, terminal_runs


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "effective-types.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, big BIGINT, amount DECIMAL(18,2));"
            "INSERT INTO facts VALUES (1,9007199254740993,1.25),(2,NULL,NULL),"
            "(3,4,2.50),(4,NULL,NULL)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            + "ms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + "facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),"
            + "source=md.table('facts'),primary_key=['id'])\n"
            + "big=ms.measure_column(name='big',entity=facts,column='big',"
            + "additivity=ms.additive_all(),unit='USD')\n"
            + "amount=ms.measure_column(name='amount',entity=facts,column='amount',"
            + "additivity=ms.additive_all(),unit='USD')\n",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("frame-effective-types", timezone="UTC")


def _read(session: Session, name: str = "big") -> AnalysisFrame:
    return session.read(
        session.members(session.catalog.get("entity:sales.facts").value),
        {name: catalog_value(session.catalog, "sales.facts." + name, kind="field")},
    )


def _native_float(
    monkeypatch: pytest.MonkeyPatch, *, invalid_key: bool = False, nonfinite: bool = False
) -> list[int]:
    """Emulate native nullable pandas carriers after a real compiled source read."""
    execute = SourceSession.execute
    calls: list[int] = []

    def native(self: SourceSession, read: CompiledRead) -> pd.DataFrame:
        frame = execute(self, read)
        calls.append(len(frame))
        for name in frame.columns:
            series = frame[name]
            if (
                invalid_key
                and str(name).startswith("key.")
                and pa.types.is_integer(pa.array(series).type)
                and not series.isna().any()
            ):
                frame[name] = pd.Series([float(2**53)] * len(series), dtype="float64")
            elif (
                str(name).startswith("value.")
                and series.isna().any()
                and (
                    pa.types.is_integer(pa.array(series).type)
                    or pa.types.is_decimal(pa.array(series).type)
                )
            ):
                # This models the admitted provider handoff, including its rounding.
                frame[name] = pd.Series(
                    [float(value) if not pd.isna(value) else None for value in series],
                    dtype="float64",
                )
                if nonfinite:
                    frame.loc[0, name] = float("inf")
        return frame

    monkeypatch.setattr(SourceSession, "execute", native)
    return calls


def _schema(frame: AnalysisFrame) -> tuple[DataType, ...]:
    assert isinstance(frame._backing, MemoryFrameBacking)
    return tuple(column.dtype for column in frame._backing.data.schema.values)


def _chain(frame: AnalysisFrame) -> AnalysisFrame:
    selected = frame.select({"shifted": -frame["big"] + 2, "original": frame["big"]})
    filtered = selected.where(~selected["original"].is_null())
    return filtered.top(2, by=filtered["original"])


def test_native_float_read_pending_chain_and_reducers(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _native_float(monkeypatch)
    source = _read(session)
    captured = source._schema
    fixed = source.compute()
    assert calls == [4]
    values = fixed.to_pandas()["big"]
    assert values.dropna().tolist() == [9007199254740992.0, 4.0]
    assert values.isna().tolist() == [False, True, False, True]
    assert _schema(fixed) == (ScalarDataType("float64"),)
    assert fixed._schema.values[0].unit == "USD"
    assert fixed._schema.axes[0].columns[0].dtype == ScalarDataType("int64")
    assert source._schema is captured
    pending = _chain(source).compute()
    assert calls == [4, 4]
    completed = _chain(fixed).compute()
    pd.testing.assert_frame_equal(pending.to_pandas(), completed.to_pandas())
    assert _schema(pending) == (ScalarDataType("float64"),) * 2
    assert len(pending._backing.metadata.audit.source_facts) > 0
    assert calls == [4, 4]
    for frame in (source, fixed):
        result = frame.reduce(
            {"sum": frame["big"].sum(), "mean": frame["big"].mean(), "count": mv.row_count()}
        ).compute()
        assert result.to_pandas().iloc[0].tolist() == [9007199254740996.0, 4503599627370498.0, 4]
        assert _schema(result) == (
            ScalarDataType("float64"),
            ScalarDataType("float64"),
            ScalarDataType("int64"),
        )
    assert calls == [4, 4, 4]
    assert terminal_runs(session) == ()


@pytest.mark.parametrize("operation", ("align", "compare"))
def test_native_float_binary_pending_shared_input_matches_completed(
    session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    calls = _native_float(monkeypatch)
    source = _read(session)
    fixed = source.compute()

    def combine(frame: AnalysisFrame) -> AnalysisFrame:
        right = frame.where(~frame["big"].is_null())
        if operation == "compare":
            return frame.compare(
                {"reference": mv.baseline(right, on=((frame.axes[0], right.axes[0]),))}
            )
        return getattr(frame, operation)(right, on=((frame.axes[0], right.axes[0]),))

    pending = combine(source).compute()
    assert calls == [4, 4]  # The two branches consume one explicit source input.
    completed = combine(fixed).compute()
    pd.testing.assert_frame_equal(pending.to_pandas(), completed.to_pandas())
    assert all(dtype == ScalarDataType("float64") for dtype in _schema(pending))
    presence = (
        ("current.present", "reference.present")
        if operation == "compare"
        else ("left_present", "right_present")
    )
    assert pending.to_pandas()[presence[0]].tolist() == [True] * 4
    assert pending.to_pandas()[presence[1]].tolist() == [True, False, True, False]
    if operation == "compare":
        values = pending.to_pandas()
        assert values["reference.absolute.big"].dropna().tolist() == [0.0, 0.0]
        assert values["reference.relative.big"].dropna().tolist() == [0.0, 0.0]
        assert values["reference.absolute.big"].isna().tolist() == [
            False,
            True,
            False,
            True,
        ]
    selected = pending.select(
        {"left": pending["big" if operation == "compare" else "left.big"]}
    ).compute()
    assert selected.presence_names == pending.presence_names
    assert calls == [4, 4]


def test_native_types_empty_all_null_persist_and_reopen(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _native_float(monkeypatch)
    source = _read(session)
    for predicate in (source["big"].is_null(), source["big"] > 2**53):
        pending = source.where(predicate)
        fixed = pending.compute()
        assert _schema(fixed) == (ScalarDataType("float64"),)
        reduced = pending.reduce({"sum": pending["big"].sum()}).compute()
        actual = reduced.to_pandas()["sum"].iloc[0]
        assert actual == 0 if len(fixed.to_pandas()) == 0 else pd.isna(actual)
        assert _schema(reduced) == (ScalarDataType("float64"),)
    saved = source.persist()
    read_count = len(calls)
    reopened = session.persisted_frame(saved.artifact_id)
    assert reopened._schema.values[0].dtype == ScalarDataType("float64")
    pd.testing.assert_frame_equal(saved.to_pandas(), reopened.to_pandas())
    continued = _chain(reopened).compute()
    assert _schema(continued) == (ScalarDataType("float64"),) * 2
    assert len(calls) == read_count
    assert len(terminal_runs(session)) == 1


def test_duckdb_exact_integer_and_native_decimal_remain_exact(session: Session) -> None:
    integer = _read(session).compute()
    assert integer.to_pandas()["big"].dropna().tolist() == [9007199254740993, 4]
    assert _schema(integer) == (ScalarDataType("int64"),)
    decimal = _read(session, "amount").compute()
    assert decimal.to_pandas()["amount"].dropna().tolist() == [
        Decimal("1.25"),
        Decimal("2.50"),
    ]
    assert isinstance(_schema(decimal)[0], DecimalDataType)
    source = _read(session, "amount")
    pending = source.select({"negated": -source["amount"]}).compute()
    assert pending.to_pandas()["negated"].dropna().tolist() == [
        Decimal("-1.25"),
        Decimal("-2.50"),
    ]


def test_native_decimal_float_type_is_retained(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _native_float(monkeypatch)
    source = _read(session, "amount")
    fixed = source.compute()
    assert _schema(fixed) == (ScalarDataType("float64"),)
    assert fixed.to_pandas()["amount"].dropna().tolist() == [1.25, 2.5]
    reduced = source.reduce({"sum": source["amount"].sum()}).compute()
    assert reduced.to_pandas()["sum"].tolist() == [3.75]
    assert _schema(reduced) == (ScalarDataType("float64"),)


def test_native_all_null_and_empty_source_carriers(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _native_float(monkeypatch)
    big = catalog_value(session.catalog, "sales.facts.big", kind="field")
    for condition, count in ((big.isnull(), 2), (big < 0, 0)):
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value, where=condition),
            {"big": big},
        )
        fixed = source.compute()
        assert len(fixed.to_pandas()) == count
        assert fixed.to_pandas()["big"].isna().all()
        expected = ScalarDataType("float64")
        assert _schema(fixed) == (expected,)
        reduced = source.reduce({"sum": source["big"].sum()}).compute()
        actual = reduced.to_pandas()["sum"].iloc[0]
        assert actual == 0 if len(fixed.to_pandas()) == 0 else pd.isna(actual)
        assert _schema(reduced) == (expected,)


def test_native_invalid_integer_keys_and_strict_nulls_publish_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        calls = _native_float(patch, invalid_key=True)
        with pytest.raises(MaterializationError, match="integer keys") as failure:
            source = _read(session)
            # Corrupt a returned business key; Top-0 carries only diagnostics.
            source.top(1, by=source["big"]).compute()
        assert calls == [1] and failure.value.stage == "stage_output"
    assert terminal_runs(session) == ()
    big = catalog_value(session.catalog, "sales.facts.big", kind="field")
    source = session.observe({"strict": big.sum(nulls="error")})
    with pytest.raises(MaterializationError, match="non-NA"):
        source.top(0, by=source["strict"]).compute()
    assert terminal_runs(session) == ()
    _native_float(monkeypatch)
    source = _read(session)
    invalid = source.select({"bad": source["big"] * 1e308})
    with pytest.raises(MaterializationError, match="finite"):
        invalid.top(0, by=invalid["bad"]).compute()
    assert terminal_runs(session) == ()


def test_native_generated_infinity_nan_and_null_survive_public_roundtrip(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "effective-types.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER big TYPE DOUBLE")
        connection.execute("UPDATE facts SET big=CASE WHEN id <= 2 THEN 1e308 ELSE NULL END")
        oracle = connection.execute(
            "SELECT SUM(big), SUM(big)-SUM(big), SUM(big)/NULLIF(0,0) FROM facts"
        ).fetchone()
    assert oracle is not None and math.isinf(oracle[0]) and math.isnan(oracle[1])
    assert oracle[2] is None
    big = catalog_value(session.catalog, "sales.facts.big", kind="field")
    total = big.sum()
    fixed = session.observe(
        {"infinite": total, "not_a_number": total - total, "missing": total / 0}
    ).compute()
    saved = fixed.persist()
    assert saved is not None

    def offline(*args: object, **kwargs: object) -> pd.DataFrame:
        raise AssertionError("complete Frame continuation must not read a source")

    monkeypatch.setattr(SourceSession, "execute", offline)
    reopened = mv.session.resume(session.id).persisted_frame(saved.artifact_id)
    projected = reopened.select(
        {
            "not_a_number": reopened["not_a_number"],
            "missing": reopened["missing"],
            "infinite": reopened["infinite"],
        }
    ).compute()
    saved_projection = projected.persist()
    assert saved_projection is not None
    reopened_projection = mv.session.resume(session.id).persisted_frame(
        saved_projection.artifact_id
    )
    for frame in (
        fixed,
        saved,
        saved.compute(),
        reopened,
        projected,
        saved_projection,
        reopened_projection,
    ):
        exported = frame.to_pandas()
        infinite = pa.array(exported["infinite"])
        not_a_number = pa.array(exported["not_a_number"])
        missing = pa.array(exported["missing"])
        assert infinite.is_valid().to_pylist() == [True]
        assert not_a_number.is_valid().to_pylist() == [True]
        assert missing.is_valid().to_pylist() == [False]
        assert infinite[0].as_py() == oracle[0]
        assert math.isnan(not_a_number[0].as_py())
        # Export is an isolated copy, including the Arrow validity bitmap.
        exported.loc[:, "infinite"] = 0.0
        assert math.isinf(frame.to_pandas()["infinite"].iloc[0])
    with pytest.raises(MaterializationError, match="finite numeric inputs"):
        reopened.select({"computed": reopened["infinite"] + 1}).compute()
