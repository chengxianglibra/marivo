"""Final Attribute targets have exact or explicitly bounded numeric closure."""

from collections.abc import Callable, Sequence
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.core.attribute import (
    AttributeClosureWitness,
    AttributeInputError,
    AttributeParentFiber,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
)
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameLevelAdditiveMethod,
    FrameLevelLinearMethod,
    FrameLevelLinearTerm,
    FrameLevelRatioMethod,
    FrameLevelSupport,
    FrameMethodOutput,
    FrameMethodSupport,
    FrameMethodTarget,
    FrameMethodValue,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.methods.frame_attribute_contribution import _numeric_binary, _Value
from marivo.analysis.methods.frame_attribute_report import _closure, _report_value
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject

_INT = ScalarDataType("int64")
_FLOAT = ScalarDataType("float64")
_PARENT_COLUMNS = (
    DataColumn("account", "account", _INT, False),
    DataColumn("region", "region", ScalarDataType("string"), True),
)
_PARENT = AttributeParentFiber(
    ((_INT, 2**53 + 1), (ScalarDataType("string"), None)), _PARENT_COLUMNS
)


def _value(number: int | float | Decimal, dtype: DataType) -> FrameMethodValue:
    return FrameMethodValue(DataColumn("q", "q", dtype, False, "1"), number)


def _values(numbers: Sequence[int | float | Decimal], dtype: DataType) -> _Value:
    physical = stage_arrow_type(dtype)
    array = (
        pa.array(numbers, type=pa.int64()).cast(physical)
        if isinstance(dtype, DurationDataType)
        else pa.array(numbers, type=physical)
    )
    return _Value(
        pd.Series(pd.arrays.ArrowExtensionArray(array)),
        DataColumn("contribution", "contribution.q", dtype, False, "1"),
    )


def _target(kind: Literal["level", "absolute", "relative"] = "level") -> FrameMethodTarget:
    return FrameMethodTarget(
        ("q",),
        kind,
        tuple(
            FrameMethodOutput(role, (path, "q"))
            for role, path in (
                ("contribution", "contribution"),
                ("total", "total"),
                ("observed_total", "observed_total"),
                ("share", "contribution_share"),
            )
        ),
    )


def _level(number: int | float | Decimal, dtype: DataType) -> FrameLevelAdditiveMethod:
    return FrameLevelAdditiveMethod(
        "level_additive", _value(number, dtype), (FrameLevelSupport("facts", "rows", 2, 2),)
    )


def _witness(error: pytest.ExceptionInfo[AttributeInputError]) -> AttributeClosureWitness:
    assert error.value.reason == "closure_mismatch"
    context = error.value.attribute_context
    assert context is not None
    assert context.fiber == _PARENT
    assert isinstance(context.witness, AttributeClosureWitness)
    assert context.witness.target == ("q",)
    return context.witness


@pytest.mark.parametrize("parent", [Decimal(100), Decimal(2**53 + 1)])
def test_widened_integer_sum_rejects_one_unit_with_exact_typed_witness(parent: Decimal) -> None:
    dtype = DecimalDataType(38, 0)
    with pytest.raises(AttributeInputError) as error:
        _closure(
            _values([parent + 1], dtype),
            _value(parent, dtype),
            _level(parent, dtype),
            relative=False,
            exact_integer=True,
            parent=_PARENT,
            target=_target(),
        )
    witness = _witness(error)
    assert witness.total == parent
    assert witness.contribution_sum == parent + 1
    assert witness.residual == Decimal(1)
    assert witness.tolerance == Decimal(0)
    assert witness.policy == "exact_integer"
    assert witness.total_column.dtype == dtype
    assert type(witness.residual) is Decimal


@pytest.mark.parametrize("scale,inside,outside", [(0, "101", "102"), (2, "100.01", "100.02")])
def test_rounded_decimal_includes_boundary_without_treating_scale_zero_as_exact(
    scale: int, inside: str, outside: str
) -> None:
    dtype = DecimalDataType(38, scale)
    total = _value(Decimal(100), dtype)
    method = _level(Decimal(100), dtype)
    _closure(
        _values([Decimal(inside)], dtype),
        total,
        method,
        relative=False,
        parent=_PARENT,
        target=_target(),
    )
    with pytest.raises(AttributeInputError) as error:
        _closure(
            _values([Decimal(outside)], dtype),
            total,
            method,
            relative=False,
            parent=_PARENT,
            target=_target(),
        )
    witness = _witness(error)
    assert witness.policy == "decimal_rounding"
    assert witness.tolerance == (Decimal(1) if scale == 0 else Decimal("0.01"))


def test_decimal_closure_preserves_high_scale_and_ignores_caller_precision() -> None:
    dtype = DecimalDataType(38, 20)
    total = Decimal("123456789012345678.00000000000000000000")
    with localcontext() as context:
        context.prec = 6
        with pytest.raises(AttributeInputError) as error:
            _closure(
                _values([Decimal("123456789012345678.00000000000000000002")], dtype),
                _value(total, dtype),
                _level(total, dtype),
                relative=False,
                parent=_PARENT,
                target=_target(),
            )
    witness = _witness(error)
    assert witness.residual == Decimal("0.00000000000000000002")
    assert witness.tolerance == Decimal("0.00000000000000000001")


def test_decimal_common_scale_never_discards_parent_fraction() -> None:
    # A coarser fine carrier must not erase the independently returned fraction.
    with pytest.raises(AttributeInputError) as error:
        _closure(
            _values([Decimal(100)], DecimalDataType(38, 0)),
            _value(Decimal("100.02"), DecimalDataType(38, 2)),
            _level(Decimal("100.02"), DecimalDataType(38, 2)),
            relative=False,
            parent=_PARENT,
            target=_target(),
        )
    witness = _witness(error)
    assert witness.residual == Decimal("-0.02")
    assert witness.tolerance == Decimal("0.01")


def test_common_report_carrier_preserves_large_historical_decimal_value() -> None:
    historical = _values([Decimal("9007199254740993.01")], DecimalDataType(38, 2))
    column = DataColumn("observed", "observed_total.q", DecimalDataType(38, 4), False, "1")
    with localcontext() as context:
        context.prec = 6
        result = _report_value(historical, column)
    assert result.column == column
    assert pa.array(result.values).type == pa.decimal128(38, 4)
    assert result.values.tolist() == [Decimal("9007199254740993.0100")]


@pytest.mark.parametrize(
    "number,source,target",
    [
        ("99999999999999999999999999999999999999", DecimalDataType(38, 0), DecimalDataType(38, 4)),
        ("1.0001", DecimalDataType(38, 4), DecimalDataType(38, 2)),
    ],
)
def test_unrepresentable_common_report_carrier_fails_without_rounding(
    number: str, source: DecimalDataType, target: DecimalDataType
) -> None:
    column = DataColumn("observed", "observed_total.q", target, False, "1")
    with pytest.raises(SemanticExpressionError) as error:
        _report_value(_values([Decimal(number)], source), column)
    assert error.value.expected == "a lossless common Attribute report carrier"


def test_report_carrier_rejects_decimal_to_float_even_when_arrow_safe_cast_allows_it() -> None:
    historical = _values([Decimal("9007199254740993.01")], DecimalDataType(38, 2))
    column = DataColumn("observed", "observed_total.q", _FLOAT, False, "1")
    with pytest.raises(SemanticExpressionError) as error:
        _report_value(historical, column)
    assert error.value.expected == "a lossless common Attribute report carrier"


@pytest.mark.parametrize("ticks", ["1.5", "9223372036854775808"])
def test_duration_report_tick_mass_rejects_fraction_and_overflow(ticks: str) -> None:
    column = DataColumn("ticks", "contribution.q", DurationDataType("us"), False, "1")
    value = _Value(pd.Series([Decimal(ticks)], dtype=object), column)
    with pytest.raises(SemanticExpressionError) as error:
        _report_value(value, column)
    assert error.value.expected == "exact signed int64 Attribute Duration ticks"


def test_absolute_checks_final_difference_without_endpoint_equalities() -> None:
    dtype = DecimalDataType(38, 0)
    method = FrameAdditiveMethod(
        "additive",
        _value(Decimal(100), dtype),
        _value(Decimal(90), dtype),
        _value(Decimal(10), dtype),
        (FrameMethodSupport("facts", "rows", 2, 2, 2, 2),),
    )
    # Fine current 60+50=110 and baseline 55+45=100 both drift, but delta is 10.
    _closure(
        _values([Decimal(5), Decimal(5)], dtype),
        method.change,
        method,
        relative=False,
        exact_integer=True,
        parent=_PARENT,
        target=_target("absolute"),
    )
    with pytest.raises(AttributeInputError) as error:
        _closure(
            _values([Decimal(60), Decimal(50)], dtype),
            method.left,
            _level(Decimal(100), dtype),
            relative=False,
            exact_integer=True,
            parent=_PARENT,
            target=_target(),
        )
    assert _witness(error).residual == Decimal(10)


def test_duration_mean_finisher_ticks_still_require_exact_final_closure() -> None:
    dtype = DurationDataType("us")
    # Each 1/2 tick rounds to 0 ticks; the complete parent mean is 2/2 = 1 tick.
    contributions = _numeric_binary(
        "div", _values([1, 1], dtype), _values([2, 2], _INT), output=dtype
    )
    method = FrameLevelRatioMethod(
        "level_ratio_component",
        _value(1, dtype),
        _value(2, dtype),
        _value(2, _INT),
        (FrameLevelSupport("facts", "non_null_value", 2, 2),),
    )
    with pytest.raises(AttributeInputError) as error:
        _closure(
            contributions, method.value, method, relative=False, parent=_PARENT, target=_target()
        )
    witness = _witness(error)
    assert (witness.total, witness.contribution_sum, witness.residual, witness.tolerance) == (
        1,
        0,
        -1,
        0,
    )
    assert witness.policy == "duration_ticks"
    assert witness.total_column.dtype == dtype


def test_float_closure_uses_absolute_contribution_magnitude_under_cancellation() -> None:
    _closure(
        _values([1e16, -1e16], _FLOAT),
        _value(1.0, _FLOAT),
        _level(1.0, _FLOAT),
        relative=False,
        parent=_PARENT,
        target=_target(),
    )


def test_float_failure_witness_preserves_fsum_and_signed_residual() -> None:
    with pytest.raises(AttributeInputError) as error:
        _closure(
            _values([1e16, 1.0, -1e16], _FLOAT),
            _value(1000.0, _FLOAT),
            _level(1000.0, _FLOAT),
            relative=False,
            parent=_PARENT,
            target=_target(),
        )
    witness = _witness(error)
    assert witness.policy == "float64"
    assert witness.contribution_sum == 1.0
    assert witness.residual == -999.0
    assert 0 < witness.tolerance < 999


@pytest.mark.parametrize(
    "numbers,total",
    [([1e308, 1e308], 1e308), ([1e308, -1e308], 0.0), ([1e308], -1e308), ([float("inf")], 1.0)],
)
def test_nonfinite_sum_magnitude_residual_or_contribution_is_numeric_error(
    numbers: list[float], total: float
) -> None:
    with pytest.raises(SemanticExpressionError) as error:
        _closure(
            _values(numbers, _FLOAT),
            _value(total, _FLOAT),
            _level(total, _FLOAT),
            relative=False,
            parent=_PARENT,
            target=_target(),
        )
    assert (
        error.value.expected == "finite Attribute closure total, sum, residual and rounding budget"
    )


def test_nonfinite_linear_rounding_budget_cannot_accept_zero_residual() -> None:
    method = FrameLevelLinearMethod(
        "level_linear",
        _value(0.0, _FLOAT),
        (FrameLevelLinearTerm(1e308, _level(1e308, _FLOAT)),),
    )
    with pytest.raises(SemanticExpressionError) as error:
        _closure(
            _values([0.0], _FLOAT),
            method.value,
            method,
            relative=False,
            parent=_PARENT,
            target=_target(),
        )
    assert "rounding budget" in error.value.expected


def test_float_linear_budget_weights_child_endpoint_rounding() -> None:
    method = FrameLevelLinearMethod(
        "level_linear",
        _value(0.0, _FLOAT),
        (FrameLevelLinearTerm(2, _level(1e16, _FLOAT)),),
    )
    with pytest.raises(AttributeInputError) as error:
        _closure(
            _values([100.0], _FLOAT),
            method.value,
            method,
            relative=False,
            parent=_PARENT,
            target=_target(),
        )
    # ulp(1e16)=2, so the weighted half-ULP child budget is 2.
    # One contribution makes the factor 32; the magnitude ULP is small.
    witness = _witness(error)
    assert 64 < witness.tolerance < 65


@pytest.mark.parametrize("baseline", [-100.0, 100.0])
def test_relative_rounding_budget_uses_absolute_fresh_baseline(baseline: float) -> None:
    method = FrameAdditiveMethod(
        "additive",
        _value(baseline + 25.0, _FLOAT),
        _value(baseline, _FLOAT),
        _value(25.0, _FLOAT),
        (FrameMethodSupport("facts", "rows", 2, 2, 2, 2),),
    )
    _closure(
        _values([0.15, 0.1], _FLOAT),
        _value(0.25, _FLOAT),
        method,
        relative=True,
        parent=_PARENT,
        target=_target("relative"),
    )
    with pytest.raises(AttributeInputError) as error:
        _closure(
            _values([0.15, 0.11], _FLOAT),
            _value(0.25, _FLOAT),
            method,
            relative=True,
            parent=_PARENT,
            target=_target("relative"),
        )
    witness = _witness(error)
    assert witness.kind == "relative"
    assert witness.policy == "float64"
    assert 0 < witness.tolerance < 1e-10


@pytest.mark.parametrize(
    "dtype,exact,policy,tolerance",
    [
        (_INT, True, "exact_integer", 0),
        (DurationDataType("us"), False, "duration_ticks", 0),
        (DecimalDataType(38, 0), False, "decimal_rounding", Decimal("0.5")),
        (_FLOAT, False, "float64", None),
    ],
)
def test_empty_contributions_use_their_own_policy(
    dtype: DataType, exact: bool, policy: str, tolerance: int | Decimal | None
) -> None:
    total: int | float | Decimal = (
        Decimal(1) if isinstance(dtype, DecimalDataType) else 1.0 if dtype == _FLOAT else 1
    )
    with pytest.raises(AttributeInputError) as error:
        _closure(
            _values([], dtype),
            _value(total, dtype),
            _level(total, dtype),
            relative=False,
            exact_integer=exact,
            parent=_PARENT,
            target=_target(),
        )
    witness = _witness(error)
    assert witness.contribution_sum == 0
    assert witness.policy == policy
    if tolerance is not None:
        assert witness.tolerance == tolerance
    else:
        assert 0 < witness.tolerance < 1e-10


@pytest.fixture
def numeric_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "numeric.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, phase VARCHAR, channel VARCHAR, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES (1,'base','A',-45),(2,'base','B',-45),(3,'current','A',-40),(4,'current','B',-40)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table('facts'), primary_key=['id'])
phase = ms.dimension_column(name='phase', entity=facts, column='phase')
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("numeric", timezone="UTC")


def test_relative_report_uses_fresh_negative_baseline_and_preserves_historical_observation(
    numeric_session: Session, tmp_path: Path
) -> None:
    amount = numeric_session.catalog.fields.get("sales.facts.amount")
    phase = numeric_session.catalog.fields.get("sales.facts.phase")
    channel = numeric_session.catalog.fields.get("sales.facts.channel")
    current = numeric_session.observe({"q": amount.sum()}, where=phase == "current")
    baseline = numeric_session.observe({"q": amount.sum()}, where=phase == "base")
    comparison = current.compare({"base": baseline})
    selected = comparison.select({"q": comparison["base.relative.q"]}).compute()
    assert selected.to_pandas()[("value", "q")].tolist() == [10 / 90]
    with duckdb.connect(str(tmp_path / "numeric.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN -50 WHEN 2 THEN -50 WHEN 3 THEN -35 ELSE -40 END"
        )
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    assert rows.iloc[:, 0].tolist() == ["A", "B"]
    assert rows[("value", "contribution.q")].tolist() == [0.15, 0.1]
    assert rows[("value", "total.q")].tolist() == [0.25, 0.25]
    assert rows[("value", "observed_total.q")].tolist() == [10 / 90, 10 / 90]
    assert rows[("value", "contribution_share.q")].tolist() == [0.6, 0.4]


def test_relative_zero_fresh_baseline_is_rejected_before_closure(
    numeric_session: Session, tmp_path: Path
) -> None:
    amount = numeric_session.catalog.fields.get("sales.facts.amount")
    phase = numeric_session.catalog.fields.get("sales.facts.phase")
    channel = numeric_session.catalog.fields.get("sales.facts.channel")
    current = numeric_session.observe({"q": amount.sum()}, where=phase == "current")
    baseline = numeric_session.observe({"q": amount.sum()}, where=phase == "base")
    comparison = current.compare({"base": baseline})
    selected = comparison.select({"q": comparison["base.relative.q"]}).compute()
    with duckdb.connect(str(tmp_path / "numeric.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=0 WHERE phase='base'")
    with pytest.raises(CoreRuleError) as error:
        selected.attribute(by=(channel,)).compute()
    assert "nonzero complete-parent baseline" in error.value.expected


def test_report_preserves_historical_values_with_finer_parent_carrier_and_reordered_reads(
    numeric_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with duckdb.connect(str(tmp_path / "numeric.duckdb")) as connection:
        connection.execute("ALTER TABLE facts ALTER amount TYPE DECIMAL(18,2)")
    amount = numeric_session.catalog.fields.get("sales.facts.amount")
    phase = numeric_session.catalog.fields.get("sales.facts.phase")
    channel = numeric_session.catalog.fields.get("sales.facts.channel")
    selected = numeric_session.observe({"q": amount.sum() * Decimal("0.5")}, by=(phase,)).compute()
    with duckdb.connect(str(tmp_path / "numeric.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN -50 WHEN 2 THEN -50 WHEN 3 THEN -35 ELSE -40 END"
        )
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if "__attribute_kind" not in rows:
            return rows
        scale = 4 if "attribute-parent:" in issued.purpose else 2
        for name in rows:
            if (
                name.endswith("/value")
                or name.endswith(".additive_value")
                or name.startswith("attribute.native.")
            ):
                rows[name] = pd.Series(
                    pd.arrays.ArrowExtensionArray(
                        pa.array(rows[name], type=pa.decimal128(38, scale))
                    )
                )
        return rows.iloc[::-1].reset_index(drop=True)

    monkeypatch.setattr(SourceSession, "execute", read)
    report = selected.attribute(by=(channel,)).compute()
    rows = report.to_pandas()
    assert list(rows.iloc[:, :2].itertuples(index=False, name=None)) == [
        ("base", "A"),
        ("base", "B"),
        ("current", "A"),
        ("current", "B"),
    ]
    assert rows[("value", "contribution.q")].tolist() == [
        Decimal("-25"),
        Decimal("-25"),
        Decimal("-17.5"),
        Decimal("-20"),
    ]
    assert rows[("value", "total.q")].tolist() == [
        Decimal("-50"),
        Decimal("-50"),
        Decimal("-37.5"),
        Decimal("-37.5"),
    ]
    assert rows[("value", "observed_total.q")].tolist() == [
        Decimal("-45"),
        Decimal("-45"),
        Decimal("-40"),
        Decimal("-40"),
    ]
    for name in ("contribution.q", "total.q", "observed_total.q"):
        assert pa.array(rows[("value", name)]).type == pa.decimal128(38, 4)


def test_report_rejects_float_carrier_for_exact_large_historical_value(
    numeric_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with duckdb.connect(str(tmp_path / "numeric.duckdb")) as connection:
        connection.execute(
            "UPDATE facts SET amount=CASE id WHEN 1 THEN 9007199254740993 ELSE 0 END"
        )
    amount = numeric_session.catalog.fields.get("sales.facts.amount")
    channel = numeric_session.catalog.fields.get("sales.facts.channel")
    selected = numeric_session.observe({"q": amount.sum()}).compute()
    assert selected.to_pandas()[("value", "q")].tolist() == [Decimal("9007199254740993")]
    with duckdb.connect(str(tmp_path / "numeric.duckdb")) as connection:
        connection.execute("UPDATE facts SET amount=1")
    execute = SourceSession.execute

    def read(self: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        rows = execute(self, issued)
        if "__attribute_kind" not in rows or "attribute-parent:" in issued.purpose:
            return rows
        for name in rows:
            if (
                name.endswith("/value")
                or name.endswith(".additive_value")
                or name.startswith("attribute.native.")
            ):
                rows[name] = rows[name].astype("float64[pyarrow]")
        return rows

    monkeypatch.setattr(SourceSession, "execute", read)
    with pytest.raises(MaterializationError) as error:
        selected.attribute(by=(channel,)).compute()
    assert error.value.expected == "a lossless common Attribute report carrier"


def test_duration_linear_sum_retains_final_tick_carrier(
    numeric_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "numeric.duckdb")) as connection:
        connection.execute(
            "ALTER TABLE facts ALTER amount TYPE INTERVAL USING amount * INTERVAL '1 microsecond'"
        )
    amount = numeric_session.catalog.fields.get("sales.facts.amount")
    channel = numeric_session.catalog.fields.get("sales.facts.channel")
    selected = numeric_session.observe({"q": amount.sum() * 2}).compute()
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    for name, expected in (
        ("contribution.q", [-170, -170]),
        ("total.q", [-340, -340]),
        ("observed_total.q", [-340, -340]),
    ):
        values = pa.array(rows[("value", name)])
        assert values.type == pa.duration("us")
        assert values.cast(pa.int64()).to_pylist() == expected
