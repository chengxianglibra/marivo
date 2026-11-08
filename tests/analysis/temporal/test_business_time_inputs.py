"""Business calendars are ordinary fields and explicit question inputs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms

if TYPE_CHECKING:
    from tests.conftest import _SemanticProjectFactory


def test_business_periods_and_disjoint_days_need_no_calendar_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    database = tmp_path / "business.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE sales(id INTEGER, day DATE, amount INTEGER, fiscal_month VARCHAR, is_workday BOOLEAN)"
        )
        connection.execute(
            "INSERT INTO sales VALUES (1,'2026-01-01',10,'FY26-M1',true),"
            "(2,'2026-01-02',20,'FY26-M1',NULL),(3,'2026-01-03',30,'FY26-M2',false)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            f"md.duckdb(name='warehouse',path={str(database)!r},wall_clock_timezone='UTC')\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Finance',default=True)\n",
            "sales/models.py": """
import marivo.datasource as md
import marivo.semantic as ms
sales = ms.entity(name='sales',datasource=ms.ref.datasource('warehouse'),source=md.table('sales'),primary_key=['id'])
day = ms.time_dimension_column(name='day',entity=sales,column='day',parse=ms.date(timezone='UTC'))
fiscal_month = ms.dimension_column(name='fiscal_month',entity=sales,column='fiscal_month')
is_workday = ms.dimension_column(name='is_workday',entity=sales,column='is_workday')
amount = ms.measure_column(name='amount',entity=sales,column='amount',additivity=ms.additive_all())
@ms.metric()
def revenue():
    return amount.sum(time=day)
""",
        }
    )
    monkeypatch.chdir(tmp_path)
    # Existing irrelevant state cannot change source readiness or execution.
    old = tmp_path / ".marivo/temporal/period-calendars/unused/current.json"
    old.parent.mkdir(parents=True)
    old.write_text("invalid retired certificate")
    original_read = Path.read_text

    def read_without_calendar(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        if "/.marivo/temporal/" in self.as_posix():
            pytest.fail("ordinary analysis consulted retired calendar state")
        return original_read(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", read_without_calendar)
    session = mv.session.get_or_create("business-time", timezone="UTC")
    revenue = session.catalog.get("metric:sales.revenue")
    fiscal = session.catalog.get("dimension:sales.sales.fiscal_month")
    workday = session.catalog.get("dimension:sales.sales.is_workday")
    day = session.catalog.get("time_dimension:sales.sales.day")
    assert isinstance(revenue, ms.Metric)
    assert isinstance(fiscal, ms.Dimension) and isinstance(workday, ms.Dimension)
    assert isinstance(day, ms.TimeDimension)
    assert session.catalog.readiness([revenue]).status != "blocked"
    scope = session.time_scope(start="2026-01-01", end="2026-01-04")
    grouped = session.observe({"v": revenue}, by=(fiscal,), where=scope).compute().to_pandas()
    assert grouped.sort_values("fiscal_month").v.tolist() == [30, 30]
    working = session.observe({"v": revenue}, where=(scope, workday == True)).compute()  # noqa: E712
    assert working.to_pandas().v.tolist() == [10]
    disjoint = (day < "2026-01-02") | (day >= "2026-01-03")
    selected = session.observe({"v": revenue}, where=(scope, disjoint)).compute()
    assert selected.to_pandas().v.tolist() == [40]
    assert session.observe({"v": revenue}, where=scope).compute().to_pandas().v.tolist() == [60]
