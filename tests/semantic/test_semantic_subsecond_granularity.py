"""Time encodings and representable field-owned sampling cadence."""

import pytest

from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed


def test_minute_granularity_timestamp_is_valid(semantic_project_factory):
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
                "@ms.time_dimension(entity=events, parse=ms.timestamp(timezone='UTC'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )
    assert project.is_ready(), project.errors()


def test_hour_sampling_on_date_parse_is_rejected(semantic_project_factory):
    """A DATE encoding cannot represent source samples within a day."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "from marivo.semantic.ir import DateParse\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
                "@ms.time_dimension(entity=events, parse=DateParse(), sample_interval=(1, 'hour'))\n"
                "def d(events):\n"
                "    return events.d.cast('date')\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        _ = SemanticCatalog(project).get("entity:ops.events").value
    assert [error.kind for error in exc_info.value.errors] == ["invalid_sample_interval"]


def test_second_granularity_datetime_is_valid(semantic_project_factory):
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
                "@ms.time_dimension(entity=events, parse=ms.timestamp(timezone='UTC'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )
    assert project.is_ready(), project.errors()


def test_minute_granularity_string_with_time_format_is_valid(semantic_project_factory):
    """String with a time-bearing format like yyyymmddhhmm should be accepted."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
                "@ms.time_dimension(entity=events, "
                "parse=ms.strptime('%Y%m%d%H%M'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        }
    )
    assert project.is_ready(), project.errors()


def test_minute_sampling_requires_time_bearing_encoding(semantic_project_factory):
    """Date-only strings cannot promise a minute cadence."""
    project = semantic_project_factory(
        {
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
                "@ms.time_dimension(entity=events, "
                "parse=ms.strptime('%Y%m%d'), sample_interval=(1, 'minute'))\n"
                "def ts(events):\n"
                "    return events.ts\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        _ = SemanticCatalog(project).get("entity:ops.events").value
    errors = exc_info.value.errors
    kinds = [e.kind for e in errors]
    assert "invalid_sample_interval" in kinds
