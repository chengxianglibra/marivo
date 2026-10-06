"""Shared scoped semantic preview projects and bounded authoring scopes."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path

import ibis

import marivo.datasource as md
from marivo.datasource.source import AuthoringScope
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.reader import SemanticProject


def default_scope(*, max_rows: int = 100) -> AuthoringScope:
    return md.unpruned(max_rows=max_rows, timeout_seconds=30)


def certified_project(
    *,
    tmp_path: Path,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> SemanticCatalog:
    database_path = tmp_path / "calendar.duckdb"
    backend = ibis.duckdb.connect(str(database_path))
    backend.raw_sql("CREATE TABLE calendar (calendar_date DATE, week TEXT, is_working BOOLEAN)")
    backend.raw_sql(
        "INSERT INTO calendar VALUES "
        "(DATE '2026-01-01', 'W1', false), "
        "(DATE '2026-01-02', 'W1', true), "
        "(DATE '2026-01-03', 'W2', true), "
        "(DATE '2026-01-04', 'W2', false)"
    )
    backend.raw_sql(
        "CREATE TABLE campaigns (campaign_id TEXT, starts DATE, ends DATE, category TEXT)"
    )
    backend.raw_sql(
        "INSERT INTO campaigns VALUES "
        "('spring', DATE '2026-03-01', DATE '2026-03-04', 'promotion'), "
        "('incident', DATE '2026-03-03', DATE '2026-03-05', 'incident')"
    )
    backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Data', default=True)\n"
            ),
            "sales/time.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                calendar = ms.entity(name="calendar", datasource=ms.ref.datasource("warehouse"), source=md.table("calendar"))
                calendar_date = ms.time_dimension_column(name="calendar_date", entity=calendar, column="calendar_date", granularity="day")
                week = ms.dimension_column(name="week", entity=calendar, column="week")
                is_working = ms.dimension_column(name="is_working", entity=calendar, column="is_working")
                fiscal = ms.period_calendar(name="fiscal", date=calendar_date, boundary_timezone="UTC", coverage=(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 5)), levels={"week": week})
                schedule = ms.work_schedule(name="schedule", date=calendar_date, is_working=is_working, boundary_timezone="UTC", coverage=(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 5)))
                campaigns = ms.entity(name="campaigns", datasource=ms.ref.datasource("warehouse"), source=md.table("campaigns"))
                campaign_id = ms.dimension_column(name="campaign_id", entity=campaigns, column="campaign_id")
                category = ms.dimension_column(name="category", entity=campaigns, column="category")
                starts = ms.time_dimension_column(name="starts", entity=campaigns, column="starts", granularity="day")
                ends = ms.time_dimension_column(name="ends", entity=campaigns, column="ends", granularity="day")
                named_campaigns = ms.temporal_set(name="named_campaigns", occurrence_id=campaign_id, start=starts, end=ends, category=category, boundary_timezone="UTC", coverage=(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2027, 1, 1)))
                """
            ),
        }
    )
    return SemanticCatalog(project)
