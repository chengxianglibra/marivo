"""Prove emitted temporal predicates over symbolic rows and execute boundary witnesses."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, cast

import duckdb
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pytest
from typing_extensions import override

import marivo.analysis as mv
from marivo.analysis.compiler import source_time as source_time_module
from marivo.analysis.compiler.member_version import version_predicate
from marivo.analysis.compiler.source_time import source_time, source_time_predicate
from marivo.analysis.materialization.temporal_sql import lower_temporal
from marivo.analysis.session.core import Session
from marivo.semantic.ir import TimestampParse
from marivo.semantic.reader import SemanticProject
from marivo.semantic.static_check.source_timezone import DeclaredSourceTimezone
from marivo.semantic.static_check.targets import (
    normalize_target_entity,
    normalize_target_version_selection,
)
from tests.analysis.temporal.encoded_time_fixtures import axis
from tests.formal._smt import prove_unsat
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_TICK = timedelta(microseconds=1)
_DAY = timedelta(days=1) // _TICK
_Interpretation = Literal["instant", "before_endpoint"]
_INTERPRETATIONS: tuple[_Interpretation, ...] = ("instant", "before_endpoint")
_Symbols = Mapping[ops.Value, tuple[str, str]]


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _micros(value: date | datetime) -> int:
    if not isinstance(value, datetime):
        return (value - _EPOCH.date()).days * _DAY
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (value - _EPOCH) // _TICK


def _integer(value: int) -> str:
    return str(value) if value >= 0 else f"(- {-value})"


@dataclass(frozen=True)
class _Truth:
    """Independent true and false formulas. Both false represents SQL NULL."""

    yes: str
    no: str


def _scalar(node: ops.Value, symbols: _Symbols) -> tuple[str, str]:
    if node in symbols:
        return symbols[node]
    if isinstance(node, ops.Literal) and isinstance(node.value, (date, datetime)):
        return _integer(_micros(node.value)), "false"
    if (
        isinstance(node, ops.Cast)
        and isinstance(node.arg, ops.Literal)
        and isinstance(node.arg.value, str)
    ):
        if node.to.is_date():
            return _integer(_micros(date.fromisoformat(node.arg.value))), "false"
        if node.to.is_timestamp():
            instant = _instant(node.arg.value)
            assert instant.tzinfo is not None, "SMT timestamp sentinels require an explicit offset"
            return _integer(_micros(instant)), "false"
    raise AssertionError(f"Unsupported temporal scalar: {node!r}")


def _truth(node: ops.Value, symbols: _Symbols) -> _Truth:
    if isinstance(node, ops.Literal) and (node.value is None or isinstance(node.value, bool)):
        return {
            True: _Truth("true", "false"),
            False: _Truth("false", "true"),
            None: _Truth("false", "false"),
        }[node.value]
    if isinstance(node, ops.Cast) and node.to.is_boolean():
        return _truth(node.arg, symbols)
    if isinstance(node, ops.IfElse):
        condition = _truth(node.bool_expr, symbols)
        yes, no = _truth(node.true_expr, symbols), _truth(node.false_null_expr, symbols)
        return _Truth(
            f"(ite {condition.yes} {yes.yes} {no.yes})",
            f"(ite {condition.yes} {yes.no} {no.no})",
        )
    if isinstance(node, (ops.And, ops.Or)):
        left, right = _truth(node.left, symbols), _truth(node.right, symbols)
        if isinstance(node, ops.And):
            return _Truth(f"(and {left.yes} {right.yes})", f"(or {left.no} {right.no})")
        return _Truth(f"(or {left.yes} {right.yes})", f"(and {left.no} {right.no})")
    if isinstance(node, ops.Not):
        child = _truth(node.arg, symbols)
        return _Truth(child.no, child.yes)
    if isinstance(node, ops.IsNull):
        _, missing = _scalar(node.arg, symbols)
        return _Truth(missing, f"(not {missing})")
    operators = {
        ops.Less: "<",
        ops.LessEqual: "<=",
        ops.Greater: ">",
        ops.GreaterEqual: ">=",
        ops.Equals: "=",
    }
    if isinstance(node, (ops.Less, ops.LessEqual, ops.Greater, ops.GreaterEqual, ops.Equals)):
        lhs, lhs_null = _scalar(node.left, symbols)
        rhs, rhs_null = _scalar(node.right, symbols)
        defined = f"(not (or {lhs_null} {rhs_null}))"
        comparison = f"({operators[type(node)]} {lhs} {rhs})"
        return _Truth(f"(and {defined} {comparison})", f"(and {defined} (not {comparison}))")
    raise AssertionError(f"Unsupported temporal predicate: {node!r}")


def _binding(
    table: ir.Table, columns: tuple[str, ...], *, step: int, suffix: str = ""
) -> tuple[_Symbols, str, tuple[str, ...]]:
    symbols: dict[ops.Value, tuple[str, str]] = {}
    declarations: list[str] = []
    witness: list[str] = []
    for column in columns:
        value = f"{column}{suffix}"
        missing = f"{value}_null"
        symbols[table[column].op()] = (value, missing)
        witness.extend((value, missing))
        declarations.extend(
            (
                f"(declare-const {value} Int)",
                f"(declare-const {missing} Bool)",
                f"(assert (= (mod {value} {step}) 0))",
            )
        )
    return symbols, "\n".join(declarations), tuple(witness)


def _prove_selection(
    prelude: str,
    actual: _Truth,
    expected: str,
    *,
    claim: str,
    witness: tuple[str, ...],
    known: str | None = None,
) -> None:
    # For value predicates, prove FALSE as well as TRUE so NULL cannot turn
    # into FALSE. For a version filter the contract is the selected row set.
    difference = f"(distinct selected {expected})"
    if known is not None:
        difference = f"(or {difference} (distinct rejected (and {known} (not {expected}))))"
    prove_unsat(
        f"""(set-logic QF_LIA)
{prelude}
(define-fun selected () Bool {actual.yes})
(define-fun rejected () Bool {actual.no})
(assert {difference})""",
        claim=claim,
        witness=(*witness, "selected", "rejected"),
    )


def _point_matches(lower: int, upper: int) -> str:
    return f"(and (not point_null) (<= {_integer(lower)} point) (< point {_integer(upper)}))"


@dataclass
class _CutoffBranch:
    take: bool
    guard: str | None = None

    def choose(self, guard: str) -> bool:
        assert self.guard is None, "A cutoff trace may test at most one symbolic guard"
        self.guard = guard
        return self.take


@dataclass(frozen=True, eq=False)
class _SymbolicRemainder:
    expression: str
    branch: _CutoffBranch

    def __mod__(self, divisor: int) -> _SymbolicRemainder:
        return replace(self, expression=f"(mod {self.expression} {divisor})")

    def __rsub__(self, left: int) -> str:
        return f"(- {left} {self.expression})"

    def __bool__(self) -> bool:
        return self.branch.choose(f"(distinct {self.expression} 0)")

    @override
    def __eq__(self, other: object) -> bool:
        assert type(other) is int, "The symbolic remainder only compares with integer ticks"
        return self.branch.choose(f"(= {self.expression} {_integer(other)})")

    @override
    def __ne__(self, other: object) -> bool:
        assert type(other) is int, "The symbolic remainder only compares with integer ticks"
        return self.branch.choose(f"(distinct {self.expression} {_integer(other)})")


@dataclass(frozen=True, eq=False)
class _SymbolicPoint:
    """One path through the concrete compiler's datetime cutoff arithmetic."""

    expression: str
    branch: _CutoffBranch
    midnight: bool = False

    @property
    def microsecond(self) -> _SymbolicRemainder:
        return _SymbolicRemainder(f"(mod {self.expression} 1000000)", self.branch)

    def replace(self, *, hour: int, minute: int, second: int, microsecond: int) -> _SymbolicPoint:
        assert (hour, minute, second, microsecond) == (0, 0, 0, 0)
        return replace(
            self,
            expression=f"(- {self.expression} (mod {self.expression} {_DAY}))",
            midnight=True,
        )

    @override
    def __eq__(self, other: object) -> bool:
        assert isinstance(other, _SymbolicPoint)
        if self is other:
            return True
        assert self.branch is other.branch
        return self.branch.choose(f"(= {self.expression} {other.expression})")

    def __add__(self, elapsed: str) -> _SymbolicPoint:
        return replace(self, expression=f"(+ {self.expression} {elapsed})")

    def date(self) -> _SymbolicPoint:
        assert self.midnight
        return self


def _symbolic_elapsed(*, days: bool = False, microseconds: str | None = None) -> str:
    return microseconds if microseconds is not None else str(_DAY * int(days))


def _emitted_cutoff(
    raw: ir.Value,
    expression: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    inclusive: bool,
) -> _Truth:
    # The only doubles supply symbolic civil-time arithmetic and capture the
    # value bound into the real Ibis comparator. Force each truth value and
    # attach the predicate actually tested by Python to the returned path.
    placeholder = ibis.table({"bound": raw.type()}, name="symbolic_cutoff")["bound"]
    symbols: dict[ops.Value, tuple[str, str]] = {raw.op(): ("point", "point_null")}

    def bind_literal(value: _SymbolicPoint, type: dt.DataType) -> ir.Value:
        assert type == raw.type()
        symbols[placeholder.op()] = (value.expression, "false")
        return placeholder

    traces: list[tuple[str | None, _Truth]] = []
    for take_branch in (True, False):
        branch = _CutoffBranch(take_branch)
        with monkeypatch.context() as patch:
            patch.setattr(source_time_module, "timedelta", _symbolic_elapsed)
            patch.setattr(ibis, "literal", bind_literal)
            compiled = source_time_module._scalar_cutoff(
                raw,
                cast("datetime", _SymbolicPoint(expression, branch)),
                inclusive=inclusive,
                engine="duckdb",
            )
        assert compiled is not None
        traces.append((branch.guard, _truth(compiled.op(), symbols)))
    (guard, when_true), (other_guard, when_false) = traces
    assert guard == other_guard, "Both cutoff paths must test the same symbolic predicate"
    if guard is None:
        assert when_true == when_false
        return when_true
    return _Truth(
        f"(ite {guard} {when_true.yes} {when_false.yes})",
        f"(ite {guard} {when_true.no} {when_false.no})",
    )


@pytest.mark.parametrize(
    ("dtype", "step"),
    (("date", _DAY), ("timestamp(0)", 1_000_000), ("timestamp(3)", 1_000), ("timestamp(6)", 1)),
)
def test_symbolic_cutoffs_preserve_signed_preimages_and_adjacent_partitions(
    dtype: str, step: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = ibis.table({"point": dtype}, name="signed_ticks")["point"]
    first, split, final = (
        _emitted_cutoff(raw, f"(+ {name} offset)", monkeypatch, inclusive=True)
        for name in ("start", "endpoint", "end")
    )
    strict = _emitted_cutoff(raw, "(+ endpoint offset)", monkeypatch, inclusive=False)
    prove_unsat(
        f"""(set-logic QF_LIA)
(declare-const start Int)
(declare-const endpoint Int)
(declare-const end Int)
(declare-const offset Int)
(declare-const point Int)
(declare-const point_null Bool)
(assert (and (<= start endpoint) (<= endpoint end) (= (mod point {step}) 0)))
(define-fun instant () Int (- point offset))
(define-fun expected () Bool (and (not point_null) (<= start instant) (< instant end)))
(define-fun first_range () Bool (and {first.yes} {split.no}))
(define-fun second_range () Bool (and {split.yes} {final.no}))
(define-fun span () Bool (and {first.yes} {final.no}))
(assert (or
  (distinct first_range (and (not point_null) (<= start instant) (< instant endpoint)))
  (distinct second_range (and (not point_null) (<= endpoint instant) (< instant end)))
  (distinct span expected)
  (and first_range second_range)
  (distinct span (or first_range second_range))
  (distinct {strict.yes} (and (not point_null) (> instant endpoint)))
  (distinct {strict.no} (and (not point_null) (<= instant endpoint)))
  (distinct (or {first.no} {final.yes}) (and (not point_null) (not expected)))))""",
        claim=f"{dtype} signed ceil preimage, strict cutoff, NULL and adjacent partition",
        witness=("start", "endpoint", "end", "offset", "point", "point_null", "span", "expected"),
    )


def _execute(table: ir.Table, predicate: ir.BooleanValue) -> dict[str, bool | None]:
    rows = lower_temporal(table.select("id", selected=predicate), "duckdb").execute()
    return {
        key: None if pd.isna(value) else bool(value)
        for key, value in rows.itertuples(index=False, name=None)
    }


@pytest.mark.parametrize(
    "lower,upper,included",
    (
        (-1_000, 1_000, {"minus", "zero"}),  # Exact ticks across the epoch.
        (-999, 1_001, {"zero", "one"}),  # Both cutoffs round up.
        (1, 999, set()),  # A nonempty interval containing no source tick.
    ),
)
def test_half_open_compiler_preserves_exact_membership_and_null(
    lower: int, upper: int, included: set[str]
) -> None:
    backend = ibis.duckdb.connect()
    try:
        backend.raw_sql(
            """CREATE TABLE ticks(id VARCHAR, point TIMESTAMP_MS);
            INSERT INTO ticks VALUES
            ('minus', '1969-12-31 23:59:59.999'), ('zero', '1970-01-01'),
            ('one', '1970-01-01 00:00:00.001'), ('two', '1970-01-01 00:00:00.002'),
            ('null', NULL)"""
        )
        table = backend.table("ticks")
        field = replace(axis("%Y%m%d%H"), parse=TimestampParse(timezone="UTC"))
        _, authority = source_time(
            table["point"],
            field,
            boundary_timezone="UTC",
            source_timezone=DeclaredSourceTimezone("UTC", "field"),
            engine="duckdb",
        )
        predicate = source_time_predicate(
            table["point"],
            field,
            authority,
            start=_EPOCH + lower * _TICK,
            end=_EPOCH + upper * _TICK,
        )
        assert predicate is not None
        symbols, prelude, witness = _binding(table, ("point",), step=1000)
        claim = f"Half-open [{lower}, {upper}) on millisecond ticks"
        _prove_selection(
            prelude,
            _truth(predicate.op(), symbols),
            _point_matches(lower, upper),
            claim=claim,
            witness=witness,
            known="(not point_null)",
        )
        following = source_time_predicate(
            table["point"],
            field,
            authority,
            start=_EPOCH + upper * _TICK,
            end=_EPOCH + (upper + 1000) * _TICK,
        )
        assert following is not None
        first = _truth(predicate.op(), symbols).yes
        second = _truth(following.op(), symbols).yes
        prove_unsat(
            f"""(set-logic QF_LIA)
{prelude}
(define-fun first () Bool {first})
(define-fun second () Bool {second})
(assert (or (and first second)
            (distinct (or first second) {_point_matches(lower, upper + 1000)})))""",
            claim=f"{claim} and its adjacent range partition their span",
            witness=(*witness, "first", "second"),
        )
        assert _execute(table, predicate) == {
            key: None if key == "null" else key in included
            for key in ("minus", "zero", "one", "two", "null")
        }, claim
    finally:
        backend.disconnect()


@pytest.mark.parametrize("lower,upper,expected", ((-1000, 1000, 3), (-999, 1001, 6), (1, 999, 0)))
def test_public_observation_uses_half_open_physical_tick_boundaries(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
    lower: int,
    upper: int,
    expected: int,
) -> None:
    # Distinct powers identify the exact selected rows, including accidental
    # inclusion of the NULL clock. No compiler predicate constructs the oracle.
    database = tmp_path / "ticks.duckdb"
    with duckdb.connect(str(database)) as conn:
        conn.execute(
            """CREATE TABLE ticks(id BIGINT, point TIMESTAMP_MS, amount BIGINT);
            INSERT INTO ticks VALUES
                (1, '1969-12-31 23:59:59.999', 1), (2, '1970-01-01', 2),
                (3, '1970-01-01 00:00:00.001', 4), (4, '1970-01-01 00:00:00.002', 8),
                (5, NULL, 16)"""
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')"
            ),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test')",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
ticks = ms.entity(name="ticks", datasource=ms.ref.datasource("warehouse"),
    source=md.table("ticks"), primary_key=["id"])
point = ms.time_dimension_column(name="point", entity=ticks, column="point", parse=ms.timestamp(timezone="UTC"))
amount = ms.measure_column(name="amount", entity=ticks, column="amount", unit="USD", additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("formal-ticks", timezone="UTC")
    amount = catalog_value(session.catalog, "sales.ticks.amount", kind="measure")
    point = catalog_value(session.catalog, "sales.ticks.point", kind="time_dimension")
    scope = session.time_scope(start=_EPOCH + lower * _TICK, end=_EPOCH + upper * _TICK)
    before = len(source_trace.native_sql)
    result = session.observe({"q": amount.sum(time=point)}, where=scope).compute().to_pandas()
    assert len(source_trace.native_sql) > before, "The public observation must execute DuckDB"
    assert result["q"].tolist() == [expected], ("DuckDB observe", lower, upper, expected, result)


_DECLARATIONS = """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource("warehouse")

daily = ms.entity(name="daily", datasource=warehouse, source=md.table("daily"), primary_key=["id"],
    versioning=ms.snapshot(partition_field=ms.ref.time_dimension("sales.daily.point"), timezone="Asia/Shanghai"))
day = ms.time_dimension_column(name="point", entity=daily, column="point", parse=ms.date(timezone="Asia/Shanghai"))
clock = ms.entity(name="clock", datasource=warehouse, source=md.table("clock"), primary_key=["id"],
    versioning=ms.snapshot(partition_field=ms.ref.time_dimension("sales.clock.point"), timezone="America/New_York"))
point = ms.time_dimension_column(name="point", entity=clock, column="point", parse=ms.timestamp())
"""


def _history_model(
    name: str, interval: str, open_end: tuple[str | None, ...], *, timestamp: bool = False
) -> str:
    table = "timestamp_history" if timestamp else "history"
    parse = "ms.timestamp()" if timestamp else 'ms.date(timezone="UTC")'
    return f"""
{name} = ms.entity(name={name!r}, datasource=warehouse, source=md.table({table!r}), primary_key=["id"],
    versioning=ms.validity(valid_from=ms.ref.time_dimension("sales.{name}.begins"),
        valid_to=ms.ref.time_dimension("sales.{name}.ends"), interval={interval!r}, open_end={open_end!r}))
{name}_begins = ms.time_dimension_column(name="begins", entity={name}, column="begins", parse={parse})
{name}_ends = ms.time_dimension_column(name="ends", entity={name}, column="ends", parse={parse})
"""


@dataclass(frozen=True)
class _Versions:
    session: Session
    database: Path

    def predicate(
        self, table: ir.Table, name: str, boundary: datetime, interpretation: _Interpretation
    ) -> ir.BooleanValue:
        state = self.session.catalog._state
        contract = normalize_target_entity(state.registry, f"sales.{name}")
        selected = normalize_target_version_selection(
            contract, boundary=boundary, interpretation=interpretation
        )
        return version_predicate(table, contract.version, selected, state=state)

    def members(self, name: str, boundary: datetime) -> set[str]:
        entity = catalog_value(self.session.catalog, f"sales.{name}", kind="entity")
        selected = self.session.members(entity, at=self.session.instant(boundary))
        return set(self.session.frame(selected).compute().to_pandas().iloc[:, 0])


@pytest.fixture
def versions(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _Versions:
    database = tmp_path / "formal-versions.duckdb"
    with duckdb.connect(str(database)) as conn:
        conn.execute(
            """CREATE TABLE daily(id VARCHAR, point DATE);
            INSERT INTO daily VALUES ('earlier','2026-02-01'), ('current','2026-02-02'),
                ('later','2026-02-04'), ('null',NULL);
            CREATE TABLE clock(id VARCHAR, point TIMESTAMPTZ);
            INSERT INTO clock VALUES
                ('spring-previous','2026-03-08 04:59:59.999999+00'),
                ('spring-first','2026-03-08 05:00:00+00'),
                ('spring-last','2026-03-09 03:59:59.999999+00'),
                ('spring-next','2026-03-09 04:00:00+00'),
                ('fall-first','2026-11-01 04:00:00+00'),
                ('fall-fold-1','2026-11-01 05:30:00+00'),
                ('fall-fold-2','2026-11-01 06:30:00+00'),
                ('fall-last','2026-11-02 04:59:59.999999+00'),
                ('fall-next','2026-11-02 05:00:00+00'), ('null',NULL);
            CREATE TABLE history(id VARCHAR, begins DATE, ends DATE);
            INSERT INTO history VALUES
                ('old','2026-02-01','2026-02-02'), ('new','2026-02-02','2026-02-03'),
                ('null-end','2026-02-01',NULL), ('sentinel','2026-02-01','9999-12-31'),
                ('future','2026-02-04',NULL), ('null-start',NULL,'9999-12-31');
            CREATE TABLE timestamp_history AS
                SELECT id, timezone('UTC', begins::TIMESTAMP) AS begins,
                    timezone('UTC', ends::TIMESTAMP) AS ends FROM history"""
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')"
            ),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test')",
            "sales/model.py": (
                _DECLARATIONS
                + _history_model("half_open", "closed_open", (None, "9999-12-31"))
                + _history_model("closed", "closed_closed", ("9999-12-31",))
                + _history_model(
                    "timestamp_open", "closed_open", (None, "9999-12-31T00:00:00Z"), timestamp=True
                )
                + _history_model(
                    "timestamp_closed", "closed_closed", ("9999-12-31T00:00:00Z",), timestamp=True
                )
            ),
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("formal-versions", timezone="Asia/Tokyo")
    return _Versions(session, database)


@pytest.mark.parametrize(
    "name,boundary,interpretation,day,until,ids",
    (
        ("daily", "2026-02-01T15:59:59.999999Z", "instant", "2026-02-01", None, {"earlier"}),
        ("daily", "2026-02-01T16:00:00Z", "instant", "2026-02-02", None, {"current"}),
        ("daily", "2026-02-01T16:00:00Z", "before_endpoint", "2026-02-01", None, {"earlier"}),
        (
            "daily",
            "2026-02-01T16:00:00.000001Z",
            "before_endpoint",
            "2026-02-02",
            None,
            {"current"},
        ),
        ("daily", "2026-02-02T16:00:00Z", "instant", "2026-02-03", None, set()),
        ("daily", "2026-02-03T16:00:00Z", "before_endpoint", "2026-02-03", None, set()),
        (
            "clock",
            "2026-03-08T05:00:00Z",
            "instant",
            "2026-03-08T05:00:00Z",
            "2026-03-09T04:00:00Z",
            {"spring-first", "spring-last"},
        ),
        (
            "clock",
            "2026-03-09T04:00:00Z",
            "before_endpoint",
            "2026-03-08T05:00:00Z",
            "2026-03-09T04:00:00Z",
            {"spring-first", "spring-last"},
        ),
        (
            "clock",
            "2026-11-02T05:00:00Z",
            "before_endpoint",
            "2026-11-01T04:00:00Z",
            "2026-11-02T05:00:00Z",
            {"fall-first", "fall-fold-1", "fall-fold-2", "fall-last"},
        ),
        (
            "clock",
            "2026-11-02T05:00:00Z",
            "instant",
            "2026-11-02T05:00:00Z",
            "2026-11-03T05:00:00Z",
            {"fall-next"},
        ),
    ),
)
def test_snapshot_normalization_and_exact_partition(
    versions: _Versions,
    name: str,
    boundary: str,
    interpretation: _Interpretation,
    day: str,
    until: str | None,
    ids: set[str],
) -> None:
    point = _instant(boundary)
    backend = ibis.duckdb.connect(str(versions.database))
    try:
        table = backend.table(name)
        predicate = versions.predicate(table, name, point, interpretation)
        symbols, prelude, witness = _binding(table, ("point",), step=_DAY if until is None else 1)
        if until is None:
            label = _integer(_micros(date.fromisoformat(day)))
            expected = f"(and (not point_null) (= point {label}))"
        else:
            expected = _point_matches(_micros(_instant(day)), _micros(_instant(until)))
        claim = f"Snapshot {name} {interpretation} at {boundary}, expected {day}"
        _prove_selection(
            prelude, _truth(predicate.op(), symbols), expected, claim=claim, witness=witness
        )
        assert {key for key, selected in _execute(table, predicate).items() if selected} == ids, (
            claim
        )
    finally:
        backend.disconnect()
    if interpretation == "instant":
        assert versions.members(name, point) == ids, claim


@pytest.mark.parametrize("interpretation", _INTERPRETATIONS)
@pytest.mark.parametrize(
    "name,boundary,at_ids,before_ids",
    (
        ("half_open", "2026-02-02T00:00:00Z", {"new", "null-end"}, {"old", "null-end"}),
        ("half_open", "2026-02-02T12:00:00Z", {"new", "null-end"}, {"new", "null-end"}),
        ("closed", "2026-02-02T00:00:00Z", {"old", "new"}, {"old"}),
        ("closed", "2026-02-02T12:00:00Z", {"new"}, {"new"}),
        ("timestamp_open", "2026-02-01T23:59:59.999999Z", {"old", "null-end"}, {"old", "null-end"}),
        ("timestamp_open", "2026-02-02T00:00:00Z", {"new", "null-end"}, {"old", "null-end"}),
        ("timestamp_open", "2026-02-02T00:00:00.000001Z", {"new", "null-end"}, {"new", "null-end"}),
    ),
)
def test_validity_boundary_and_nonoverlap(
    versions: _Versions,
    name: str,
    boundary: str,
    interpretation: _Interpretation,
    at_ids: set[str],
    before_ids: set[str],
) -> None:
    point = _instant(boundary)
    anchor = _integer(_micros(point))
    sentinel = _integer(_micros(date(9999, 12, 31)))
    before = interpretation == "before_endpoint"
    half_open = name != "closed"
    ascending_start = "<" if before else "<="
    ascending_end = "<" if half_open and not before else "<="
    is_open = "ends_null" if half_open else "false"
    expected = f"""(and (not begins_null) ({ascending_start} begins {anchor})
        (or {is_open} (and (not ends_null) (or (= ends {sentinel}) ({ascending_end} {anchor} ends)))))"""
    claim = f"Validity {name} {interpretation} at {boundary}"
    ids = {"sentinel", *(before_ids if before else at_ids)}
    timestamp = name == "timestamp_open"
    step = 1 if timestamp else _DAY
    backend = ibis.duckdb.connect(str(versions.database))
    try:
        table = backend.table("timestamp_history" if timestamp else "history")
        predicate = versions.predicate(table, name, point, interpretation)
        symbols, prelude, witness = _binding(table, ("begins", "ends"), step=step)
        actual = _truth(predicate.op(), symbols)
        _prove_selection(prelude, actual, expected, claim=claim, witness=witness)

        other, declarations, other_witness = _binding(
            table, ("begins", "ends"), step=step, suffix="2"
        )
        nonoverlap = "<=" if half_open else "<"
        prove_unsat(
            f"""(set-logic QF_LIA)
{prelude}
{declarations}
(assert (and (not ends_null) (distinct ends {sentinel}) ({nonoverlap} ends begins2)))
(assert (and {actual.yes} {_truth(predicate.op(), other).yes}))""",
            claim=f"{claim} can select at most one declared version",
            witness=(*witness, *other_witness),
        )

        assert {key for key, selected in _execute(table, predicate).items() if selected} == ids, (
            claim
        )
    finally:
        backend.disconnect()
    if interpretation == "instant":
        assert versions.members(name, point) == ids, claim


def _lift_temporal_literals(
    predicate: ir.BooleanValue, symbols: _Symbols, literals: Mapping[int, str]
) -> _Truth:
    lifted = dict(symbols)
    observed: set[int] = set()
    replacements = {_integer(value): (value, name) for value, name in literals.items()}
    # Walk leaf constants from the real expression. Provider date/time casts
    # and literal inputs comprise two different Ibis AST spellings.
    for node in predicate.op().find((ops.Literal, ops.Cast)):
        try:
            ground, _ = _scalar(node, {})
        except AssertionError:
            continue
        if ground in replacements:
            value, name = replacements[ground]
            observed.add(value)
            lifted[node] = (name, "false")
    assert observed == set(literals), (predicate, literals, observed)
    return _truth(predicate.op(), lifted)


@pytest.mark.parametrize("interpretation", _INTERPRETATIONS)
@pytest.mark.parametrize("name", ["timestamp_open", "timestamp_closed"])
def test_symbolic_validity_anchor_and_declared_nonoverlap(
    versions: _Versions, interpretation: _Interpretation, name: str
) -> None:
    table = ibis.table(
        {"begins": "timestamp('UTC', 6)", "ends": "timestamp('UTC', 6)"},
        name="timestamp_history",
    )
    point = _instant("2026-02-02T00:00:00Z")
    predicate = versions.predicate(table, name, point, interpretation)
    symbols, prelude, witness = _binding(table, ("begins", "ends"), step=1)
    actual = _lift_temporal_literals(predicate, symbols, {_micros(point): "anchor"})
    half_open = name == "timestamp_open"
    before = interpretation == "before_endpoint"
    start_op = "<" if before else "<="
    end_op = "<" if half_open and not before else "<="
    sentinel = _integer(_micros(date(9999, 12, 31)))
    open_end = "ends_null" if half_open else "false"
    expected = f"""(and (not begins_null) ({start_op} begins anchor)
      (or {open_end} (and (not ends_null) (or (= ends {sentinel}) ({end_op} anchor ends)))))"""
    _prove_selection(
        f"{prelude}\n(declare-const anchor Int)",
        actual,
        expected,
        claim=f"{name}/{interpretation} production predicate over a symbolic anchor",
        witness=(*witness, "anchor"),
    )
    other_symbols, declarations, other_witness = _binding(
        table, ("begins", "ends"), step=1, suffix="2"
    )
    other = _lift_temporal_literals(predicate, other_symbols, {_micros(point): "anchor"})
    nonoverlap = "<=" if half_open else "<"
    prove_unsat(
        f"""(set-logic QF_LIA)
{prelude}
{declarations}
(declare-const anchor Int)
(assert (and (not ends_null) (distinct ends {sentinel}) ({nonoverlap} ends begins2)))
(assert (and {actual.yes} {other.yes}))""",
        claim=f"{name}/{interpretation} cannot select two declared nonoverlapping versions",
        witness=(*witness, *other_witness, "anchor"),
    )


@pytest.mark.parametrize("interpretation", _INTERPRETATIONS)
def test_symbolic_snapshot_day_membership_and_unique_period(
    versions: _Versions, interpretation: _Interpretation
) -> None:
    point = _instant("2026-02-02T05:00:00Z")
    table = ibis.table({"point": "timestamp('UTC', 6)"}, name="clock")
    predicate = versions.predicate(table, "clock", point, interpretation)
    before = interpretation == "before_endpoint"
    left = point - timedelta(days=1) if before else point
    right = left + timedelta(days=1)
    symbols, prelude, witness = _binding(table, ("point",), step=1)
    actual = _lift_temporal_literals(
        predicate, symbols, {_micros(left): "left", _micros(right): "right"}
    )
    _prove_selection(
        f"{prelude}\n(declare-const left Int)\n(declare-const right Int)\n(assert (< left right))",
        actual,
        "(and (not point_null) (<= left point) (< point right))",
        claim=f"Snapshot {interpretation} keeps symbolic half-open source membership",
        witness=(*witness, "left", "right"),
    )
    left_op, right_op = ("<", "<=") if before else ("<=", "<")
    prove_unsat(
        f"""(set-logic QF_LIA)
(declare-const left Int)
(declare-const right Int)
(declare-const later_left Int)
(declare-const later_right Int)
(declare-const anchor Int)
(assert (and (< left right) (<= right later_left) (< later_left later_right)))
(assert (and ({left_op} left anchor) ({right_op} anchor right)
             ({left_op} later_left anchor) ({right_op} anchor later_right)))""",
        claim=f"Snapshot {interpretation} selects at most one nonoverlapping business period",
        witness=("left", "right", "later_left", "later_right", "anchor"),
    )
