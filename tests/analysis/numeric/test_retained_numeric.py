"""Public retained-input statistics with independent carrier-aware oracles."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.cell_arrow import number
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import SourceData, source_case

Carrier = int | float | Decimal
Profile = Literal["int64-near-extremes", "finite-float64", "decimal-exact"]
Method = Literal["pearson", "spearman", "kendall", "zscore", "mad"]


@pytest.fixture
def profile() -> Profile:
    return "int64-near-extremes"


@pytest.fixture
def scenario() -> str:
    return "numeric-profile"


@dataclass(frozen=True)
class RetainedPair:
    a: mv.MaterializedNumericRelation
    b: mv.MaterializedNumericRelation
    x: tuple[Carrier | None, ...]
    y: tuple[Carrier, ...]
    history: mv.MaterializedNumericRelation
    history_b: mv.MaterializedNumericRelation
    entity_history: mv.MaterializedNumericRelation


def originals(
    relation: mv.MaterializedNumericRelation | mv.MaterializedSelectedNumericRelation,
) -> tuple[Carrier, ...]:
    values: list[Carrier] = []
    for value in relation.to_pandas().value:
        assert isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
        values.append(value)
    return tuple(values)


def keyed_originals(
    relation: mv.MaterializedNumericRelation | mv.MaterializedSelectedNumericRelation,
) -> dict[tuple[object, ...], Carrier]:
    assert relation._dataset is not None
    captured = relation._dataset.verified()
    keys = captured.contract.key_fields
    rows = captured.primary.to_dict(orient="records")
    values = {tuple(row[key] for key in keys): number(row["value"]) for row in rows}
    assert len(values) == len(rows)
    return values


def ranks(values: tuple[Fraction, ...]) -> tuple[Fraction, ...]:
    return tuple(
        Fraction(sum(other < value for other in values))
        + Fraction(sum(other == value for other in values) + 1, 2)
        for value in values
    )


def coefficient(xs: tuple[Carrier, ...], ys: tuple[Carrier, ...], method: Method) -> float:
    x, y = tuple(map(Fraction, xs)), tuple(map(Fraction, ys))
    if method == "spearman":
        x, y = ranks(x), ranks(y)
    if method == "kendall":
        numerator = untied_x = untied_y = 0
        for i in range(len(x)):
            for j in range(i):
                dx, dy = (x[i] > x[j]) - (x[i] < x[j]), (y[i] > y[j]) - (y[i] < y[j])
                numerator += dx * dy
                untied_x += dx != 0
                untied_y += dy != 0
        denominator = Fraction(untied_x * untied_y)
        numerator_q = Fraction(numerator)
    else:
        mean_x, mean_y = sum(x, Fraction()) / len(x), sum(y, Fraction()) / len(y)
        numerator_q = sum(
            ((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True)), Fraction()
        )
        denominator = sum(((a - mean_x) * (a - mean_x) for a in x), Fraction()) * sum(
            ((b - mean_y) * (b - mean_y) for b in y), Fraction()
        )
    with localcontext(Context(prec=240, rounding=ROUND_HALF_EVEN)):
        return float(
            (Decimal(numerator_q.numerator) / Decimal(numerator_q.denominator))
            / (Decimal(denominator.numerator) / Decimal(denominator.denominator)).sqrt()
        )


@pytest.fixture
def retained_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    profile: Profile,
    scenario: str,
) -> Iterator[RetainedPair]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    typ = (
        "BIGINT"
        if profile == "int64-near-extremes"
        else "DOUBLE"
        if profile == "finite-float64"
        else "DECIMAL(38,6)"
    )
    base = (
        -(2**63) + 1
        if profile == "int64-near-extremes"
        else 10**32 - 1000
        if profile == "decimal-exact"
        else 0
    )
    xs = [
        str(base + value) if profile != "decimal-exact" else f"{base + value}.000001"
        for value in (0, 1, 1, 4)
    ]
    ys = ["1", "2", "1", "4"]
    if profile == "int64-near-extremes":
        xs = [str(-(2**63) + 1), str(2**63 - 2), str(2**63 - 2), str(-(2**63) + 5)]
    if profile == "finite-float64":
        xs = ["1e-250", "2e-250", "2e-250", "5e-250"]
        ys = ["1e200", "2e200", "1e200", "4e200"]
    if scenario == "unavailable-breaks":
        xs = ["1", "NULL", "1", "1"]
    if scenario == "empty-null-unavailable":
        xs, ys = ["1", "NULL", "3", "5"], ["1", "2", "0", "4"]
    dst = scenario in {"future-grid-interval", "full-grid-dst-adjacency-duration"}
    zone = "America/New_York" if dst else "UTC"
    start = datetime(2026, 10, 28) if dst else datetime(2026, 8, 1)
    days = 6 if scenario == "full-grid-dst-adjacency-duration" else 4
    instants = [
        (start + timedelta(days=i))
        .replace(tzinfo=ZoneInfo(zone))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
        for i in range(4)
    ]
    data = SourceData(
        f"id BIGINT, tenant VARCHAR(10), revision BIGINT, happened TIMESTAMP, x {typ}, y {typ}",
        ",".join(
            f"(9007199254740993,'{tenant}',{i},'{instants[i].isoformat(sep=' ')}',{x},{y})"
            for i, (tenant, x, y) in enumerate(zip(("a", "a", "b", "b"), xs, ys, strict=True))
        ),
        "",
        [],
    )
    with source_case("duckdb", "table", tmp_path, monkeypatch, data=data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={case.session.datasource.fields['path']!r}, read_only=True)\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='R9', default=True)\n",
                "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}), primary_key=['tenant','id','revision'])\n"
                + "x = ms.measure_column(name='x', entity=facts, column='x', additivity=ms.additive_all())\n"
                + "y = ms.measure_column(name='y', entity=facts, column='y', additivity=ms.additive_all())\n"
                + "event_time = ms.time_dimension_column(name='event_time', entity=facts, column='happened', granularity='second', parse=ms.timestamp(timezone='UTC'), is_default=True)\n"
                + f"total_x = ms.aggregate(name='total_x', measure=x, agg='sum', empty=ms.empty.{('null' if scenario in {'unavailable-breaks', 'empty-null-unavailable'} else 'zero')}())\n"
                + "total_y = ms.aggregate(name='total_y', measure=y, agg='sum', empty=ms.empty.zero())\n",
            }
        )
        session = mv.session.get_or_create("retained-risk", report_timezone=zone)
        members = session.members(ms.ref.entity("sales.facts"))
        grid = mv.time_grid(
            during=mv.time_scope(
                start=start.date().isoformat(),
                end=(start + timedelta(days=days)).date().isoformat(),
            ),
            grain=mv.grain("day"),
        )
        history = members.observe(ms.ref.metric("sales.total_x"), during=grid, by=(mv.member(),))
        converter = (
            int
            if profile == "int64-near-extremes"
            else float
            if profile == "finite-float64"
            else Decimal
        )
        fixed_history = history.group_by(grid).rollup().execute()
        assert isinstance(fixed_history, mv.MaterializedGroupedNumericRelation)
        fixed_history_b = (
            members.observe(
                ms.ref.metric("sales.total_y"),
                during=grid,
                by=(mv.member(),),
            )
            .group_by(grid)
            .rollup()
            .execute()
            if scenario
            in {"complete-pair-lag-order", "unavailable-breaks", "empty-null-unavailable"}
            else fixed_history
        )
        assert isinstance(fixed_history_b, mv.MaterializedGroupedNumericRelation)
        entity_history = (
            history.execute() if scenario == "complete-identity-domain" else fixed_history
        )
        assert isinstance(entity_history, mv.MaterializedNumericRelation)
        yield RetainedPair(
            members.read(ms.ref.measure("sales.facts.x")).execute(),
            members.read(ms.ref.measure("sales.facts.y")).execute(),
            tuple(None if value == "NULL" else converter(value) for value in xs),
            tuple(converter(value) for value in ys),
            fixed_history,
            fixed_history_b,
            entity_history,
        )


def forbidden(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("Retained-input execution touched live source or Semantic")
