from __future__ import annotations

import unittest
from datetime import date
from typing import Any, cast

from app.analysis_core.calendar_data_runtime import (
    CalendarDataReader,
    CalendarDataResolutionError,
)
from app.config import CalendarConfig
from app.routing import QueryRouter
from app.storage.metadata import MetadataStore


class _FakeMetadata:
    def __init__(self) -> None:
        self.sources = {
            "CN Holiday": {"source_id": "src_holiday"},
            "Campaign Calendar": {"source_id": "src_event"},
        }
        self.tables = {
            ("src_holiday", "analytics.cn_public_holiday"): {"object_id": "obj_holiday"},
            ("src_event", "analytics.campaign_calendar"): {"object_id": "obj_event"},
        }

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        params = params or []
        if "FROM sources" in sql:
            return self.sources.get(str(params[0]))
        if "FROM source_objects" in sql:
            return self.tables.get((str(params[0]), str(params[1])))
        raise AssertionError(f"Unexpected query: {sql}")


class _FakeEngine:
    def __init__(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> None:
        self.rows_by_table = rows_by_table

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        _ = params
        for table_name, rows in self.rows_by_table.items():
            if table_name in sql:
                return rows
        raise AssertionError(f"Unexpected table query: {sql}")


class _FakeRoute:
    def __init__(
        self, table_name: str, engine: _FakeEngine, *, qualified_name: str | None = None
    ) -> None:
        self.qualified_names = {table_name: qualified_name or table_name}
        self.engine = engine


class _FakeQueryRouter:
    def __init__(self, engine: _FakeEngine, *, qualified_name: str | None = None) -> None:
        self.engine = engine
        self.qualified_name = qualified_name

    def resolve_tables(self, table_names: list[str]) -> _FakeRoute:
        return _FakeRoute(table_names[0], self.engine, qualified_name=self.qualified_name)


def _metadata_store() -> MetadataStore:
    return cast("MetadataStore", _FakeMetadata())


def _query_router(engine: _FakeEngine, *, qualified_name: str | None = None) -> QueryRouter:
    return cast("QueryRouter", _FakeQueryRouter(engine, qualified_name=qualified_name))


def _calendar_row(
    day: str,
    *,
    holiday_group_id: str | None = None,
    year_relative_holiday_key: str | None = None,
    event_group_id: str | None = None,
    year_relative_event_key: str | None = None,
) -> dict[str, Any]:
    day_value = date.fromisoformat(day)
    return {
        "calendar_date": day,
        "region_code": "CN",
        "calendar_version": "version",
        "weekday": day_value.weekday() + 1,
        "is_weekend": day_value.weekday() >= 5,
        "is_workday": day_value.weekday() < 5,
        "holiday_group_id": holiday_group_id,
        "year_relative_holiday_key": year_relative_holiday_key,
        "event_group_id": event_group_id,
        "year_relative_event_key": year_relative_event_key,
    }


class CalendarDataReaderTests(unittest.TestCase):
    def test_read_for_alignment_assembles_holiday_and_event_rows(self) -> None:
        config = CalendarConfig.model_validate(
            {
                "default_region_code": "CN",
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                        "event_source": {
                            "source_name": "Campaign Calendar",
                            "table_fqn": "analytics.campaign_calendar",
                            "calendar_version": "campaign_calendar_2026_q2_v3",
                        },
                    }
                ],
            }
        )
        engine = _FakeEngine(
            {
                "analytics.cn_public_holiday": [
                    _calendar_row(
                        "2025-04-01",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-3",
                    ),
                    _calendar_row(
                        "2025-04-02",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-2",
                    ),
                    _calendar_row(
                        "2026-04-01",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-3",
                    ),
                    _calendar_row(
                        "2026-04-02",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-2",
                    ),
                ],
                "analytics.campaign_calendar": [
                    _calendar_row(
                        "2025-04-01",
                        event_group_id="member_day",
                        year_relative_event_key="member_day_d-1",
                    ),
                    _calendar_row(
                        "2025-04-02",
                        event_group_id="member_day",
                        year_relative_event_key="member_day_d+0",
                    ),
                    _calendar_row(
                        "2026-04-01",
                        event_group_id="member_day",
                        year_relative_event_key="member_day_d-1",
                    ),
                    _calendar_row(
                        "2026-04-02",
                        event_group_id="member_day",
                        year_relative_event_key="member_day_d+0",
                    ),
                ],
            }
        )
        reader = CalendarDataReader(
            metadata=_metadata_store(),
            query_router=_query_router(engine),
            config=config,
        )

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 3)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 3)),
        )

        self.assertEqual(result.resolved_calendar_source, "calendar_data_cn_assembled")
        self.assertEqual(result.resolved_calendar_version, "calendar_data_cn_2026q2_v1")
        self.assertEqual(result.annotation_rows[0].holiday_group_id, "qingming")
        self.assertEqual(result.annotation_rows[0].event_group_id, "member_day")
        self.assertEqual(
            result.source_lineage["holiday_source"]["calendar_version"],
            "cn_public_holiday_2026_v1",
        )

    def test_read_for_alignment_rejects_overlapping_snapshots(self) -> None:
        config = CalendarConfig.model_validate(
            {
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                    },
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v2",
                        "region_code": "CN",
                        "effective_start": "2025-06-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v2",
                        },
                    },
                ]
            }
        )
        reader = CalendarDataReader(
            metadata=_metadata_store(),
            query_router=_query_router(_FakeEngine({"analytics.cn_public_holiday": []})),
            config=config,
        )

        with self.assertRaises(CalendarDataResolutionError):
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 3)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 3)),
            )

    def test_read_for_alignment_omits_event_source_lineage_when_snapshot_has_no_event_source(
        self,
    ) -> None:
        config = CalendarConfig.model_validate(
            {
                "default_region_code": "CN",
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                    }
                ],
            }
        )
        engine = _FakeEngine(
            {
                "analytics.cn_public_holiday": [
                    _calendar_row(
                        "2025-04-01",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-3",
                    ),
                    _calendar_row(
                        "2025-04-02",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-2",
                    ),
                    _calendar_row(
                        "2026-04-01",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-3",
                    ),
                    _calendar_row(
                        "2026-04-02",
                        holiday_group_id="qingming",
                        year_relative_holiday_key="qingming_d-2",
                    ),
                ],
            }
        )
        reader = CalendarDataReader(
            metadata=_metadata_store(),
            query_router=_query_router(engine),
            config=config,
        )

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 3)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 3)),
        )

        self.assertIn("holiday_source", result.source_lineage)
        self.assertNotIn("event_source", result.source_lineage)

    def test_read_for_alignment_rejects_missing_required_fields(self) -> None:
        config = CalendarConfig.model_validate(
            {
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                    }
                ]
            }
        )
        engine = _FakeEngine(
            {
                "analytics.cn_public_holiday": [
                    {
                        "calendar_date": "2025-04-01",
                        "region_code": "CN",
                        "calendar_version": "cn_public_holiday_2026_v1",
                        "weekday": 2,
                        "is_weekend": False,
                    }
                ]
            }
        )
        reader = CalendarDataReader(
            metadata=_metadata_store(),
            query_router=_query_router(engine),
            config=config,
        )

        with self.assertRaises(CalendarDataResolutionError):
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

    def test_read_for_alignment_rejects_unsafe_qualified_table_name(self) -> None:
        config = CalendarConfig.model_validate(
            {
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                    }
                ]
            }
        )
        reader = CalendarDataReader(
            metadata=_metadata_store(),
            query_router=_query_router(
                _FakeEngine({"analytics.cn_public_holiday": []}),
                qualified_name="analytics.cn_public_holiday; DROP TABLE source_objects",
            ),
            config=config,
        )

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

        self.assertEqual(
            str(ctx.exception),
            "calendar source resolved to an unsafe table identifier",
        )

    def test_read_for_alignment_reports_missing_sources_for_uncovered_day(self) -> None:
        config = CalendarConfig.model_validate(
            {
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                        "event_source": {
                            "source_name": "Campaign Calendar",
                            "table_fqn": "analytics.campaign_calendar",
                            "calendar_version": "campaign_calendar_2026_q2_v3",
                        },
                    }
                ]
            }
        )
        engine = _FakeEngine(
            {
                "analytics.cn_public_holiday": [
                    _calendar_row("2025-04-01"),
                ],
                "analytics.campaign_calendar": [
                    _calendar_row("2025-04-01", event_group_id="member_day"),
                ],
            }
        )
        reader = CalendarDataReader(
            metadata=_metadata_store(),
            query_router=_query_router(engine),
            config=config,
        )

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

        self.assertEqual(
            ctx.exception.details,
            {
                "calendar_date": "2026-04-01",
                "missing_sources": ["holiday_source", "event_source"],
            },
        )

    def test_reader_rejects_mutable_snapshot_version_aliases(self) -> None:
        config = CalendarConfig.model_validate(
            {
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "latest",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                    }
                ]
            }
        )

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            CalendarDataReader(
                metadata=_metadata_store(),
                query_router=_query_router(_FakeEngine({"analytics.cn_public_holiday": []})),
                config=config,
            )

        self.assertEqual(
            str(ctx.exception),
            "calendar snapshot requires an immutable resolved_calendar_version",
        )

    def test_reader_rejects_unregistered_source(self) -> None:
        config = CalendarConfig.model_validate(
            {
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "Missing Source",
                            "table_fqn": "analytics.cn_public_holiday",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                    }
                ]
            }
        )

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            CalendarDataReader(
                metadata=_metadata_store(),
                query_router=_query_router(_FakeEngine({"analytics.cn_public_holiday": []})),
                config=config,
            )

        self.assertEqual(str(ctx.exception), "calendar source 'Missing Source' is not registered")

    def test_reader_rejects_unsynced_table(self) -> None:
        config = CalendarConfig.model_validate(
            {
                "snapshots": [
                    {
                        "resolved_calendar_source": "calendar_data_cn_assembled",
                        "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
                        "region_code": "CN",
                        "effective_start": "2025-01-01",
                        "effective_end": "2026-12-31",
                        "holiday_source": {
                            "source_name": "CN Holiday",
                            "table_fqn": "analytics.missing_table",
                            "calendar_version": "cn_public_holiday_2026_v1",
                        },
                    }
                ]
            }
        )

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            CalendarDataReader(
                metadata=_metadata_store(),
                query_router=_query_router(_FakeEngine({"analytics.cn_public_holiday": []})),
                config=config,
            )

        self.assertEqual(
            str(ctx.exception),
            "calendar table 'analytics.missing_table' is not synced for source 'CN Holiday'",
        )


if __name__ == "__main__":
    unittest.main()
