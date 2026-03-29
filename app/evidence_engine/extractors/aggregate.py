from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, ClassVar
from uuid import uuid4

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.extractors.anomaly import _compute_outliers
from app.evidence_engine.factories import make_anomaly_observation
from app.evidence_engine.schemas import Observation

# ── Unit inference constants (G-5a) ────────────────────────────────────────
# Duration columns: values are expected to be in seconds, milliseconds, etc.
_DURATION_COLUMN_PATTERNS = re.compile(
    r"(?:^|_)(elapsed|latency|duration|time|wait|delay|lag|ttfb|rtt|p\d+|p99|p95|p50)(?:_|$)",
    re.IGNORECASE,
)
_BYTES_COLUMN_PATTERNS = re.compile(
    r"(?:^|_)(bytes?|size|bandwidth|throughput|traffic|download|upload|bw)(?:_|$)",
    re.IGNORECASE,
)

# Magnitude bands for duration heuristics (seconds):
# milliseconds: < 1000ms median, values look like 0–999
# seconds: 0–86400 range
# microseconds: very small values
_DURATION_BANDS = [
    ("microseconds", 1e-3, 1.0),  # median < 1
    ("milliseconds", 1.0, 10_000.0),  # median 1–10000 ms-class
    ("seconds", 10_000.0, 1e9),  # median > 10000 → seconds or larger
]
_BYTES_BANDS = [
    ("bytes", 1.0, 1_000.0),
    ("kilobytes", 1_000.0, 1_000_000.0),
    ("megabytes", 1_000_000.0, 1_000_000_000.0),
    ("gigabytes", 1_000_000_000.0, float("inf")),
]


# ── Temporal column name heuristics (G-2) ──────────────────────────────────────
# These column names are recognized as temporal slice keys for observed_window inference.
TEMPORAL_COLUMN_NAMES_DAY: frozenset[str] = frozenset(
    {
        "date",
        "day",
        "dt",
        "log_date",
        "event_date",
        "partition_date",
        "report_date",
        "transaction_date",
        "created_date",
        "updated_date",
        "create_date",
    }
)

TEMPORAL_COLUMN_NAMES_HOUR: frozenset[str] = frozenset(
    {
        "hour",
        "dt_hour",
        "log_hour",
        "event_hour",
        "report_hour",
        "hour_slot",
        "create_time",
    }
)

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
        observation_type (str): observation type (default: "metric_observation").
        value_column (str | None): primary numeric column for payload.
            When omitted, auto-detects the first numeric column that isn't a group_by column.
        metric (str): metric label (default: "aggregate").
    When a temporal column is detected in ``group_by``, infer per-row
    ``observed_window`` from the slice key value. This lets aggregate rows carry
    finer-grained temporal evidence than the request-level time_scope window.
    """

    name = "aggregate_rows"
    artifact_type: ClassVar[str] = "aggregate_rows"
    observation_types: ClassVar[list[str]] = ["metric_observation", "anomaly_detection"]
    preconditions: ClassVar[list[str]] = []

    def extract(
        self,
        rows: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[Observation]:
        context = dict(context or {})
        group_by: list[str] = list(context.get("group_by", []))
        observation_type = str(context.get("observation_type", "metric_observation"))
        metric = str(context.get("metric", "aggregate"))
        temporal_group_by_columns_data = context.get("temporal_group_by_columns")
        temporal_group_by_columns = [str(col) for col in (temporal_group_by_columns_data or [])]
        value_column: str | None = context.get("value_column")
        # G-5a: optional column metadata from caller (synced source_objects properties)
        column_metadata: dict[str, dict[str, str]] = dict(context.get("column_metadata") or {})

        # Detect temporal column for observed_window inference (G-2)
        temporal_col = self._detect_temporal_column(group_by)

        # G-5a: pre-compute value column and collect values for unit inference
        row_list = [dict(r) for r in rows]
        vc_resolved = value_column
        if not vc_resolved and row_list:
            vc_resolved = self._detect_value_column(row_list[0], group_by)

        unit_hints: dict[str, dict[str, Any]] = {}
        if vc_resolved and row_list:
            col_values = [
                r[vc_resolved]
                for r in row_list
                if vc_resolved in r and isinstance(r[vc_resolved], (int, float))
            ]
            col_meta = column_metadata.get(vc_resolved, {})
            hint = self.infer_column_unit(
                vc_resolved,
                col_values,
                metadata_unit=col_meta.get("unit"),
                entity_unit=None,
            )
            if hint is not None:
                unit_hints[vc_resolved] = hint

        observations: list[Observation] = []
        for row_dict in row_list:
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

            # G-5a: embed unit hint in payload under reserved key
            effective_vc = vc or vc_resolved
            if effective_vc and effective_vc in unit_hints:
                payload["column_unit_hint"] = unit_hints[effective_vc]

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
                    "sample_size": int(payload.get("current_value", 0))
                    if isinstance(payload.get("current_value"), (int, float))
                    else 0,
                    "practical_significance": True,
                },
                "quality": {
                    "freshness_ok": True,
                    "sample_size_ok": True,
                },
            }
            if temporal_group_by_columns:
                obs["subject"]["temporal_group_by_columns"] = temporal_group_by_columns
            if observed_window is not None:
                obs["observed_window"] = observed_window
            observations.append(obs)

        anomaly_value_column = context.get("anomaly_value_column")
        anomaly_column = str(anomaly_value_column) if anomaly_value_column else vc_resolved
        observations.extend(
            self._detect_anomalies(
                row_list,
                group_by=group_by,
                metric=metric,
                value_column=anomaly_column,
                temporal_col=temporal_col,
                anomaly_group_by=context.get("anomaly_group_by"),
                z_threshold=float(context.get("anomaly_z_threshold", 2.5)),
            )
        )
        return observations

    def _detect_anomalies(
        self,
        row_list: list[dict[str, Any]],
        *,
        group_by: list[str],
        metric: str,
        value_column: str | None,
        temporal_col: str | None,
        anomaly_group_by: Any,
        z_threshold: float,
    ) -> list[Observation]:
        if len(row_list) < 5 or not value_column:
            return []

        stratum_columns = self._resolve_anomaly_group_by(
            group_by,
            temporal_col=temporal_col,
            anomaly_group_by=anomaly_group_by,
        )

        grouped_rows: dict[tuple[Any, ...], list[tuple[dict[str, Any], float]]] = defaultdict(list)
        for row_dict in row_list:
            value = row_dict.get(value_column)
            if not isinstance(value, (int, float)):
                continue
            stratum_key = tuple(row_dict.get(col) for col in stratum_columns)
            grouped_rows[stratum_key].append((row_dict, float(value)))

        observations: list[Observation] = []
        for stratum_key, members in grouped_rows.items():
            if len(members) < 5:
                continue

            values = [value for _, value in members]
            use_iqr = len(members) < 20
            outlier_indices = _compute_outliers(
                values,
                z_threshold=z_threshold,
                use_iqr=use_iqr,
            )
            if not outlier_indices:
                continue

            mean = statistics.mean(values)
            try:
                std = statistics.stdev(values)
            except statistics.StatisticsError:
                std = 0.0

            stratum = {col: value for col, value in zip(stratum_columns, stratum_key, strict=False)}
            iqr_bounds = self._compute_iqr_bounds(values) if use_iqr else None
            for idx in outlier_indices:
                row_dict, value = members[idx]
                z_score = (value - mean) / std if std != 0.0 else 0.0
                method = "z_score"
                if iqr_bounds is not None:
                    lower, upper = iqr_bounds
                    if value < lower or value > upper:
                        method = "iqr"

                payload = {
                    "value": value,
                    "mean": mean,
                    "std": std,
                    "z_score": z_score,
                    "outlier_factor": self._compute_outlier_factor(value, mean),
                    "method": method,
                    "stratum": stratum,
                    "sample_size": len(values),
                }
                quality = {"freshness_ok": True, "sample_size_ok": True}
                anomaly = make_anomaly_observation(
                    metric,
                    {col: row_dict[col] for col in group_by if col in row_dict},
                    payload,
                    quality,
                )
                if temporal_col and temporal_col in row_dict:
                    parsed = _parse_temporal_value(row_dict[temporal_col])
                    if parsed[0] is not None and parsed[1] is not None:
                        anomaly["observed_window"] = _build_observed_window(parsed[0], parsed[1])
                observations.append(anomaly)

        return observations

    @staticmethod
    def _detect_temporal_column(group_by: list[str]) -> str | None:
        """Detect temporal column for observed_window inference.

        Priority:
        1. First group_by column matching TEMPORAL_COLUMN_NAMES_DAY
        2. First group_by column matching TEMPORAL_COLUMN_NAMES_HOUR
        """
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

    @staticmethod
    def _resolve_anomaly_group_by(
        group_by: list[str],
        *,
        temporal_col: str | None,
        anomaly_group_by: Any,
    ) -> list[str]:
        if anomaly_group_by:
            return [str(col) for col in anomaly_group_by]

        non_temporal = [col for col in group_by if col != temporal_col]
        if len(non_temporal) <= 1:
            return []
        return non_temporal[:-1]

    @staticmethod
    def _compute_iqr_bounds(values: list[float]) -> tuple[float, float] | None:
        try:
            quartiles = statistics.quantiles(values, n=4, method="inclusive")
        except statistics.StatisticsError:
            return None
        if len(quartiles) < 3:
            return None
        q1 = quartiles[0]
        q3 = quartiles[2]
        iqr = q3 - q1
        if iqr == 0.0:
            return None
        return (q1 - 1.5 * iqr, q3 + 1.5 * iqr)

    @staticmethod
    def _compute_outlier_factor(value: float, mean: float) -> float | None:
        if mean == 0.0:
            return None
        return value / mean

    # ── Unit inference (G-5a) ─────────────────────────────────────────────────

    @staticmethod
    def infer_column_unit(
        column_name: str,
        values: list[float],
        *,
        metadata_unit: str | None = None,
        entity_unit: str | None = None,
    ) -> dict[str, Any] | None:
        """Infer a unit hint for a numeric column.

        Precedence:
        1. Authoritative metadata (synced source_objects.properties_json.unit)
        2. Semantic entity properties.unit
        3. Column-name heuristics (duration / bytes families)
        4. Distribution-based magnitude scoring

        Returns a structured hint dict or None if no hint can be produced.

        Hint shape:
            {
                "source": "metadata" | "entity" | "heuristic" | "distribution",
                "family": "duration" | "bytes",
                "unit": str,          # best candidate
                "confidence": float,  # 0.0–1.0
                "candidates": [{"unit": str, "score": float}, ...],
                "signals": [str, ...],
            }

        A hint is only returned when confidence is above a minimum threshold (0.4).
        When metadata conflicts with heuristics, confidence is capped low (<0.4)
        and no hint is returned.
        """
        col_lower = column_name.lower()
        signals: list[str] = []

        # ── Step 1: authoritative sources ─────────────────────────────────
        if metadata_unit:
            return {
                "column": column_name,
                "source": "metadata",
                "family": _classify_unit_family(metadata_unit),
                "unit": metadata_unit,
                "confidence": 1.0,
                "candidates": [{"unit": metadata_unit, "score": 1.0}],
                "signals": ["metadata_unit_present"],
            }
        if entity_unit:
            return {
                "column": column_name,
                "source": "entity",
                "family": _classify_unit_family(entity_unit),
                "unit": entity_unit,
                "confidence": 0.95,
                "candidates": [{"unit": entity_unit, "score": 0.95}],
                "signals": ["entity_unit_present"],
            }

        # ── Step 2: name-based heuristics ─────────────────────────────────
        is_duration_name = bool(_DURATION_COLUMN_PATTERNS.search(col_lower))
        is_bytes_name = bool(_BYTES_COLUMN_PATTERNS.search(col_lower))

        # Conflicting column name signals → low confidence
        if is_duration_name and is_bytes_name:
            signals.append("name_ambiguous")
            return None

        # ── Step 3: distribution analysis ─────────────────────────────────
        clean_values = [
            v for v in values if v is not None and not (isinstance(v, float) and (v != v))
        ]
        dist_family: str | None = None
        dist_unit: str | None = None
        dist_confidence: float = 0.0

        if clean_values:
            try:
                median_val = statistics.median(clean_values)
                max_val = max(clean_values)
                signals.append(f"median={median_val:.3g}")
                signals.append(f"max={max_val:.3g}")

                if is_duration_name:
                    dist_family = "duration"
                    dist_unit, dist_confidence = _score_duration_bands(median_val, signals)
                elif is_bytes_name:
                    dist_family = "bytes"
                    dist_unit, dist_confidence = _score_bytes_bands(median_val, signals)
                else:
                    # No name signal — distribution alone can hint duration or bytes
                    dur_unit, dur_conf = _score_duration_bands(median_val, [])
                    byt_unit, byt_conf = _score_bytes_bands(median_val, [])
                    # Only produce a hint if one family clearly wins
                    if dur_conf > byt_conf and dur_conf >= 0.5:
                        dist_family = "duration"
                        dist_unit, dist_confidence = (
                            dur_unit,
                            dur_conf * 0.5,
                        )  # halved (no name signal)
                        signals.append("duration_distribution_hint")
                    elif byt_conf > dur_conf and byt_conf >= 0.5:
                        dist_family = "bytes"
                        dist_unit, dist_confidence = byt_unit, byt_conf * 0.5
                        signals.append("bytes_distribution_hint")
            except (ValueError, statistics.StatisticsError):
                signals.append("distribution_error")

        # ── Step 4: combine signals ────────────────────────────────────────
        if is_duration_name:
            signals.append("duration_name_match")
            family = "duration"
            base_confidence = 0.7 if dist_unit is not None else 0.5
            final_unit = dist_unit or "seconds"
            final_confidence = min(0.95, base_confidence + (dist_confidence * 0.25))
        elif is_bytes_name:
            signals.append("bytes_name_match")
            family = "bytes"
            base_confidence = 0.7 if dist_unit is not None else 0.5
            final_unit = dist_unit or "bytes"
            final_confidence = min(0.95, base_confidence + (dist_confidence * 0.25))
        elif dist_unit is not None and dist_confidence >= 0.5:
            family = dist_family or "unknown"
            final_unit = dist_unit
            final_confidence = dist_confidence
        else:
            return None  # below minimum confidence threshold

        if final_confidence < 0.4:
            return None

        candidates: list[dict[str, Any]] = [
            {"unit": final_unit, "score": round(final_confidence, 3)}
        ]
        # Add runner-up if distribution suggested something different from name default
        if dist_unit and dist_unit != final_unit:
            candidates.append({"unit": dist_unit, "score": round(dist_confidence, 3)})

        return {
            "column": column_name,
            "source": "heuristic" if (is_duration_name or is_bytes_name) else "distribution",
            "family": family,
            "unit": final_unit,
            "confidence": round(final_confidence, 3),
            "candidates": candidates,
            "signals": signals,
        }


def _classify_unit_family(unit: str) -> str:
    """Classify a unit string into a family (duration | bytes | unknown)."""
    u = unit.lower()
    if any(k in u for k in ("second", "ms", "millisec", "microsec", "minute", "hour", "ns")):
        return "duration"
    if any(k in u for k in ("byte", "kb", "mb", "gb", "tb", "kib", "mib", "gib")):
        return "bytes"
    return "unknown"


def _score_duration_bands(median_val: float, signals: list[str]) -> tuple[str, float]:
    """Return (unit, confidence) based on median value for duration family."""
    if median_val < 1.0:
        signals.append("duration_microseconds_band")
        return "microseconds", 0.55
    elif median_val < 10_000.0:
        signals.append("duration_milliseconds_band")
        return "milliseconds", 0.75
    elif median_val < 1_000_000.0:
        signals.append("duration_seconds_band")
        return "seconds", 0.65
    else:
        signals.append("duration_large_band")
        return "seconds", 0.45


def _score_bytes_bands(median_val: float, signals: list[str]) -> tuple[str, float]:
    """Return (unit, confidence) based on median value for bytes family."""
    if median_val < 1_000.0:
        signals.append("bytes_band")
        return "bytes", 0.65
    elif median_val < 1_000_000.0:
        signals.append("kilobytes_band")
        return "kilobytes", 0.70
    elif median_val < 1_000_000_000.0:
        signals.append("megabytes_band")
        return "megabytes", 0.75
    else:
        signals.append("gigabytes_band")
        return "gigabytes", 0.65
