"""Tests for minute/second base granularity and the sub-day parse guard."""

import pytest

from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed


def test_minute_granularity_timestamp_is_valid(semantic_project_factory):
    semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='minute', parse=ms.timestamp(timezone='UTC'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )


def test_second_granularity_on_date_parse_is_rejected(semantic_project_factory):
    """DateParse with second granularity is rejected at decorator time."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "from marivo.semantic.ir import DateParse\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='second', parse=DateParse())\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("entity.ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "invalid_ref" in kinds or "subday_granularity_without_time" in kinds


def test_minute_granularity_on_date_parse_is_rejected(semantic_project_factory):
    """DateParse with minute granularity is rejected at decorator time."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "from marivo.semantic.ir import DateParse\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='minute', parse=DateParse())\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("entity.ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "invalid_ref" in kinds or "subday_granularity_without_time" in kinds


def test_hour_granularity_on_date_parse_is_rejected(semantic_project_factory):
    """Hour on DateParse IS rejected because hour is a sub-day granularity."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "from marivo.semantic.ir import DateParse\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='hour', parse=DateParse())\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("entity.ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "invalid_ref" in kinds or "subday_granularity_without_time" in kinds


def test_second_granularity_datetime_is_valid(semantic_project_factory):
    semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='second', parse=ms.datetime(timezone='UTC'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )


def test_minute_granularity_string_with_time_format_is_valid(semantic_project_factory):
    """String with a time-bearing format like yyyymmddhhmm should be accepted."""
    semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='minute', "
                "parse=ms.strptime('%Y%m%d%H%M'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )


def test_minute_granularity_string_without_time_format_is_rejected(semantic_project_factory):
    """String with a date-only format should be rejected for minute granularity."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=md.ref('datasource.warehouse'), source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='minute', "
                "parse=ms.strptime('%Y%m%d'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("entity.ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "invalid_ref" in kinds
