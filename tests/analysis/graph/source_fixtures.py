"""Shared builders for source fixtures tests."""

from collections.abc import Callable
from typing import Literal, NoReturn

import pytest

from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case


def author_source_project(
    backend: str,
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    identity_type: Literal["int64", "string"] = "string",
    time_parse: Literal["native", "native_resolved", "string", "date"] = "native",
    cumulative_anchor: str | None = None,
    certified_calendar: bool = False,
    read_timezone: str = "UTC",
) -> bool:
    assert isinstance(case.source, TableSourceIR)
    remote = backend not in ("duckdb", "sqlite")
    if remote:

        def forbid_staging_write(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Remote C05 must not create or drop backend tables")

        monkeypatch.setattr(type(case.session._backend), "create_table", forbid_staging_write)
        monkeypatch.setattr(type(case.session._backend), "drop_table", forbid_staging_write)
    arguments = {
        **case.session.datasource.fields,
        **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
    }
    if "user" in arguments:
        monkeypatch.setenv("MARIVO_R93_READER", str(arguments.pop("user")))
        arguments["user_env"] = "MARIVO_R93_READER"
    argument_text = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    primary_key = (
        ["tenant", "id", "revision"] if identity_type == "string" else ["id", "revision", "tenant"]
    )
    parse = (
        "None"
        if time_parse in {"date", "native_resolved"}
        else f"ms.timestamp(timezone={read_timezone!r})"
        if time_parse == "native"
        else "ms.strptime('%Y-%m-%d %H:%M:%S', timezone='UTC')"
    )
    granularity = "day" if time_parse == "date" else "second"
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.{backend}(name='warehouse', {argument_text})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='R9', default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key={primary_key!r})\n"
            + "bucket = ms.dimension_column(name='bucket', entity=facts, column='tenant')\n"
            + "revision = ms.dimension_column(name='revision', entity=facts, column='revision')\n"
            + "amount = ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
            + f"happened = ms.time_dimension_column(name='happened', entity=facts, column='happened', granularity={granularity!r}, parse={parse}, is_default=True)\n"
            + "total = ms.aggregate(name='total', measure=amount, agg='sum', empty=ms.empty.zero())\n"
            + "average = ms.aggregate(name='average', measure=amount, agg='mean')\n"
            + (
                "import marivo.analysis as mv\n"
                "running = ms.cumulative(name='running', base=total, over=happened, "
                f"anchor={cumulative_anchor})\n"
                if cumulative_anchor is not None
                else ""
            )
            + (
                "from datetime import date\n"
                "calendar_day = ms.time_dimension_column(name='calendar_day', entity=facts, column='calendar_day', granularity='day')\n"
                "period = ms.dimension_column(name='period', entity=facts, column='period')\n"
                "fiscal = ms.period_calendar(name='fiscal', date=calendar_day, boundary_timezone='Asia/Shanghai', coverage=(date(2026, 8, 1), date(2026, 8, 6)), levels={'period': period})\n"
                if certified_calendar
                else ""
            ),
        }
    )
    return remote
