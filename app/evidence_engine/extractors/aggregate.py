from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, ClassVar
from uuid import uuid4

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.schemas import Observation


# ── Temporal column name heuristics (G-2) ──────────────────────────────────────
# These column names are recognized as temporal slice keys for observed_window inference.
TEMPORAL_COLUMN_NAMES_DAY: frozenset[str] = frozenset({
    "date", "day", "dt", "log_date", "event_date", "partition_date",
    "report_date", "transaction_date", "created_date", "updated_date",
    "create_date",
})

TEMPORAL_COLUMN_NAMES_HOUR: frozenset[str] = frozenset({
    "hour", "dt_hour", "log_hour", "event_hour", "report_hour",
    "hour_slot", "create_time",
})

# Regex patterns for temporal value parsing
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YYYYMMDD_PATTERN = re.compile(r"^\d{8}$")
_ISO_DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_DATETIME_HOUR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}(?::\d{2}(?::\d{2})?)?$")


def _parse_temporal_value(value: Any) -> tuple[date | None, str | None]:
    """Parse a temporal value into a (start_date, granularity) tuple.

    Returns (None, None) if parsing fails. Granularity is 'day' or 'hour'.

    Supported formats:
    - ISO date: "2024-01-15" -> day granularity
    - YYYYMMDD: "20240115" -> day granularity
    - ISO datetime: "2024-01-15T10:30:00" -> hour granularity
    - YYYY-MM-DD HH[:MM[:SS]]: "2024-01-15 10:30:00" -> hour granularity
    """
    if value is None:
        return None, None

    s = str(value).strip()

    # ISO date (day granularity)
    if _ISO_DATE_PATTERN.match(s):
        try:
            d = date.fromisoformat(s)
            return d, "day"
        except ValueError:
            return None, None

    # YYYYMMDD (day granularity)
    if _YYYYMMDD_PATTERN.match(s):
        try:
            d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            return d, "day"
        except ValueError:
            return None, None

    # ISO datetime (hour granularity)
    if _ISO_DATETIME_PATTERN.match(s):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])
            return dt.replace(minute=0, second=0, microsecond=0), "hour"
        except ValueError:
            return None, None

    # YYYY-MM-DD HH[:MM[:SS]] (hour granularity)
    if _DATETIME_HOUR_PATTERN.match(s):
        try:
            dt = datetime(int(s[:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]))
            return dt, "hour"
        except (ValueError, IndexError):
            return None, None

    return None, None


def _build_observed_window(start: date | datetime, granularity: str) -> dict[str, str]:
    """Build observed_window dict from start and granularity.

    Half-open interval representation:
    - day: [day, next_day)   — start is a date, output is YYYY-MM-DD
    - hour: [hour, next_hour) — start is a datetime truncated to the hour,
                                output is YYYY-MM-DDTHH:00
    """
    if granularity == "day":
        d = start if isinstance(start, date) and not isinstance(start, datetime) else start.date()
        return {
            "start": d.isoformat(),
            "end": (d + timedelta(days=1)).isoformat(),
            "granularity": "day",
        }
    else:  # hour — start is a datetime
        end_dt = start + timedelta(hours=1)
        return {
            "start": start.strftime("%Y-%m-%dT%H:00"),
            "end": end_dt.strftime("%Y-%m-%dT%H:00"),
            "granularity": "hour",
        }


class AggregateRowExtractor(ExtractorContract):
    """Extract observations from aggregate_query rows.

    Context keys:
        group_by (list[str]): columns used for GROUP BY — become slice dimensions.
        observation_type (str): observation type (default: "metric_change").
        value_column (str | None): primary numeric column for payload.
            When omitted, auto-detects the first numeric column that isn't a group_by column.
        metric (str): metric label (default: "aggregate").
        observed_window_column (str | None): explicit column to use for observed_window.
            When omitted, auto-detects temporal columns from TEMPORAL_COLUMN_NAMES_DAY/HOUR.

    G-2: When a temporal column is detected in group_by, infer per-row observed_window
    from the slice key value. This enables TemporalPrecedenceChecker to recognize
    time-ordered evidence and promote claims from L1 to L2.
    """

    name = "aggregate_rows"
    artifact_type: ClassVar[str] = "aggregate_rows"
    observation_types: ClassVar[list[str]] = ["metric_change"]
    preconditions: ClassVar[list[str]] = []

    def extract(
        self,
        rows: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        context = dict(context or {})
        group_by: list[str] = list(context.get("group_by", []))
        observation_type = str(context.get("observation_type", "metric_change"))
        metric = str(context.get("metric", "aggregate"))
        value_column: str | None = context.get("value_column")
        observed_window_column: str | None = context.get("observed_window_column")

        # Detect temporal column for observed_window inference (G-2)
        temporal_col = self._detect_temporal_column(group_by, observed_window_column)

        observations: list[Observation] = []
        for row in rows:
            row_dict = dict(row)

            # Determine primary value column
            vc = value_column
            if not vc:
                vc = self._detect_value_column(row_dict, group_by)

            # Build slice from group_by columns
            slice_dict = {col: row_dict[col] for col in group_by if col in row_dict}

            # Build payload with all non-group_by values
            payload: dict[str, Any] = {}
            if vc and vc in row_dict:
                payload["current_value"] = row_dict[vc]
            for k, v in row_dict.items():
                if k not in group_by:
                    payload[k] = v

            # Infer observed_window from temporal column (G-2)
            observed_window: dict[str, str] | None = None
            if temporal_col and temporal_col in row_dict:
                parsed = _parse_temporal_value(row_dict[temporal_col])
                if parsed[0] is not None and parsed[1] is not None:
                    observed_window = _build_observed_window(parsed[0], parsed[1])

            obs: Observation = {
                "observation_id": f"obs_{uuid4().hex[:12]}",
                "type": observation_type,
                "subject": {
                    "metric": metric,
                    "slice": slice_dict,
                },
                "payload": payload,
                "significance": {
                    "sample_size": int(payload.get("current_value", 0)) if isinstance(payload.get("current_value"), (int, float)) else 0,
                    "practical_significance": True,
                },
                "quality": {
                    "freshness_ok": True,
                    "sample_size_ok": True,
                },
            }
            if observed_window is not None:
                obs["observed_window"] = observed_window
            observations.append(obs)
        return observations

    @staticmethod
    def _detect_temporal_column(
        group_by: list[str],
        observed_window_column: str | None,
    ) -> str | None:
        """Detect temporal column for observed_window inference.

        Priority:
        1. Explicit observed_window_column if provided and in group_by
        2. First group_by column matching TEMPORAL_COLUMN_NAMES_DAY
        3. First group_by column matching TEMPORAL_COLUMN_NAMES_HOUR
        """
        if observed_window_column and observed_window_column in group_by:
            return observed_window_column

        # Check for day-granularity temporal columns
        for col in group_by:
            if col.lower() in TEMPORAL_COLUMN_NAMES_DAY:
                return col

        # Check for hour-granularity temporal columns
        for col in group_by:
            if col.lower() in TEMPORAL_COLUMN_NAMES_HOUR:
                return col

        return None

    @staticmethod
    def _detect_value_column(row: dict[str, Any], group_by: list[str]) -> str | None:
        for k, v in row.items():
            if k not in group_by and isinstance(v, (int, float)):
                return k
        return None