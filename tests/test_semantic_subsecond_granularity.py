"""Tests for minute/second base granularity and the sub-day data_type guard."""

import pytest

from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed


def test_minute_granularity_timestamp_is_valid(semantic_project_factory):
    semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='minute', parse=ms.timestamp(timezone='UTC'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )


def test_second_granularity_on_date_is_rejected(semantic_project_factory):
    """ms.date() with second granularity is rejected at decorator time."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='second', parse=ms.date())\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    # Now caught at decorator time by _validate_time_parse_granularity
    assert "invalid_ref" in kinds or "subday_granularity_without_time" in kinds


def test_minute_granularity_on_date_is_rejected(semantic_project_factory):
    """ms.date() with minute granularity is rejected at decorator time."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='minute', parse=ms.date())\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    # Now caught at decorator time by _validate_time_parse_granularity
    assert "invalid_ref" in kinds or "subday_granularity_without_time" in kinds


def test_hour_granularity_on_date_is_rejected(semantic_project_factory):
    """Hour on date IS rejected because hour is a sub-day granularity."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='hour', parse=ms.date())\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    # Now caught at decorator time by _validate_time_parse_granularity
    assert "invalid_ref" in kinds or "subday_granularity_without_time" in kinds


def test_second_granularity_datetime_is_valid(semantic_project_factory):
    semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
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
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='minute', "
                "parse=ms.strptime('%Y%m%d%H%M', data_type='string'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )


def test_minute_granularity_string_without_time_format_is_rejected(semantic_project_factory):
    """String with a date-only format should be rejected for minute granularity."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.semantic as ms\nms.domain(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_dimension(entity=events, granularity='minute', "
                "parse=ms.strptime('%Y%m%d', data_type='string'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).get("ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "invalid_ref" in kinds
