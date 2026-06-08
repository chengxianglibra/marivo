"""Tests for minute/second base granularity and the sub-day data_type guard."""

import pytest

from marivo.semantic.errors import SemanticLoadFailed


def test_minute_granularity_timestamp_is_valid(semantic_project_factory):
    semantic_project_factory(
        {
            "ops/_model.py": "import marivo.semantic as ms\nms.model(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.dataset(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_field(dataset=events, data_type='timestamp', granularity='minute')\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )


def test_second_granularity_on_date_is_rejected(semantic_project_factory):
    project = semantic_project_factory(
        {
            "ops/_model.py": "import marivo.semantic as ms\nms.model(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.dataset(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_field(dataset=events, data_type='date', granularity='second')\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_dataset("ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "subday_granularity_without_time" in kinds


def test_minute_granularity_on_date_is_rejected(semantic_project_factory):
    project = semantic_project_factory(
        {
            "ops/_model.py": "import marivo.semantic as ms\nms.model(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.dataset(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_field(dataset=events, data_type='date', granularity='minute')\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_dataset("ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "subday_granularity_without_time" in kinds


def test_hour_granularity_on_date_is_rejected(semantic_project_factory):
    """Hour on date IS rejected because hour is a sub-day granularity."""
    project = semantic_project_factory(
        {
            "ops/_model.py": "import marivo.semantic as ms\nms.model(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.dataset(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_field(dataset=events, data_type='date', granularity='hour')\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_dataset("ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "subday_granularity_without_time" in kinds


def test_second_granularity_datetime_is_valid(semantic_project_factory):
    semantic_project_factory(
        {
            "ops/_model.py": "import marivo.semantic as ms\nms.model(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.dataset(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_field(dataset=events, data_type='datetime', granularity='second')\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )


def test_minute_granularity_string_with_time_format_is_valid(semantic_project_factory):
    """String with a time-bearing format like yyyymmddhhmm should be accepted."""
    semantic_project_factory(
        {
            "ops/_model.py": "import marivo.semantic as ms\nms.model(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.dataset(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_field(dataset=events, data_type='string', granularity='minute', "
                "date_format='%Y%m%d%H%M')\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )


def test_minute_granularity_string_without_time_format_is_rejected(semantic_project_factory):
    """String with a date-only format should be rejected for minute granularity."""
    project = semantic_project_factory(
        {
            "ops/_model.py": "import marivo.semantic as ms\nms.model(name='ops')\n",
            "ops/datasets.py": (
                "import marivo.semantic as ms\n"
                "events = ms.dataset(name='events', datasource='warehouse', source=ms.table('events'))\n"
                "@ms.time_field(dataset=events, data_type='string', granularity='minute', "
                "date_format='%Y%m%d')\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_dataset("ops.events")
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "subday_granularity_without_time" in kinds
