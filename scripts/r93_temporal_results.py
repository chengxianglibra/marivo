"""Validate bounded C06 receipts without granting candidate qualification."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import r9_qualification_requirements as freeze
from scripts.r93_evidence_transport import read_attachment, verify
from scripts.r93_multiroot_results import BACKENDS, _native_receipt

FOLDS = ("mean", "first", "last", "min", "max")
SPATIAL_FOLDS = tuple("spatial_" + kind for kind in FOLDS)
WINDOW_MODES = ("string-time", "dst", *FOLDS, *SPATIAL_FOLDS)


def validate_grid_consumer(backend: str, mode: str, receipt: dict[str, freeze.Json]) -> None:
    """Require independent temporal oracles and complete source/fixed coordinates."""
    if backend not in BACKENDS:
        raise ValueError("Known native backend required")
    _native_receipt(backend, receipt)
    inputs = freeze.obj(receipt["input"])
    if not inputs.get("columns") or not inputs.get("values"):
        raise ValueError("Independent temporal source facts required")
    cumulative = mode in ("all-history", "month-reset", "trailing-overlap")
    zone = (
        "UTC" if mode in ("civil-date-grid", "certified-calendar") or cumulative else "Asia/Tokyo"
    )
    expected: dict[str, freeze.Json] = {
        "values": [7, 11],
        "report_timezone": zone,
        "grid_timezone": "America/New_York",
        "resources": 0,
    }
    if cumulative:
        anchors: dict[str, tuple[list[freeze.Json], str, bool]] = {
            "all-history": ([10, 28], "None", True),
            "month-reset": ([5, 18], "ms.grain_to_date(grain=mv.grain('month'))", False),
            "trailing-overlap": ([5, 21], "ms.trailing(count=31, unit='day')", True),
        }
        values, anchor, overlap = anchors[mode]
        expected = {
            "values": values,
            "anchor": anchor,
            "overlap": overlap,
            "display_start_does_not_clip_anchor": True,
            "end_exclusive": True,
            "resources": 0,
        }
    elif mode == "native-time":
        expected.update(
            product_rows=10,
            native_time_type="timestamp[us, tz=UTC]"
            if backend == "clickhouse"
            else "timestamp[us]",
            parser="timestamp UTC" if backend == "sqlite" else None,
            reader_timezone=None if backend in ("sqlite", "clickhouse") else "UTC",
            exact_start_included=True,
            exact_end_excluded=True,
            last_microsecond_included=True,
        )
    elif mode == "aware-repeated-hour" and backend in ("duckdb", "postgres"):
        expected.update(
            values=[18],
            product_rows=4,
            native_time_type="timestamp[us, tz=UTC]",
            parser=None,
            distinct_repeated_hour_values=[2, 5],
            exact_start_included=True,
            exact_end_excluded=True,
        )
    elif mode == "civil-date-grid":
        expected.update(
            values=[2, 5],
            product_rows=8,
            source_role="civil_date",
            end_exclusive=True,
            before_start_excluded=True,
        )
    elif mode in ("spring-grid", "fall-grid"):
        expected.update(
            product_rows=8,
            source_timezone="UTC",
            cell_hours=[23 if mode == "spring-grid" else 25, 24],
            end_exclusive=True,
            last_half_hour_included=True,
        )
    elif mode == "certified-calendar":
        expected = {
            "values": [7, 41],
            "period_days": [2, 3],
            "product_rows": 10,
            "calendar_timezone": "Asia/Shanghai",
            "report_timezone": "UTC",
            "snapshot_current": True,
            "conflicting_zone_rejected": True,
            "resources": 0,
        }
        oracle = freeze.obj(receipt["oracle"])
        controls = oracle.get("deadline_controls", [])
        expected_controls: list[freeze.Json] = []
        if backend == "mysql":
            expected_controls = [
                {
                    "purpose": "semantic.certified_preview.deadline",
                    "sql": sql,
                    "state": "succeeded",
                    "statement_id": statement,
                }
                for statement, sql in (
                    (
                        "mysql.authoring.install_select_deadline",
                        "SET SESSION max_execution_time = 30000",
                    ),
                    ("mysql.authoring.read_select_deadline", "SELECT @@session.max_execution_time"),
                )
            ]
        if freeze.encode(controls) != freeze.encode(expected_controls):
            raise ValueError("Exact isolated certification deadline controls required")
        if "deadline_controls" in oracle:
            expected["deadline_controls"] = expected_controls
        if not any(
            freeze.obj(item).get("purpose") == "semantic.certified_preview"
            for item in freeze.arr(receipt["submissions"])
        ):
            raise ValueError("Actual certified calendar source submission required")
    else:
        raise ValueError("Closed C06 grid consumer mode required")
    if freeze.encode(freeze.obj(receipt["oracle"])) != freeze.encode(expected):
        raise ValueError("Independent temporal boundary oracle differs")
    _validate_state(backend, receipt, zone, grid=True)


def _validate_state(
    backend: str,
    receipt: dict[str, freeze.Json],
    zone: str,
    *,
    grid: bool,
    fold: bool = False,
    mean: bool = False,
) -> None:
    methods = {
        "parts_transport@v1",
        "metric.fold@v1" if fold else "metric.sum_zero@v1",
    }
    if grid:
        methods.update(("time.product@v1", "state_rollup.sum_zero@v1"))
    keys = [freeze.obj(item) for item in freeze.arr(receipt["physical_keys"])]
    if len(keys) != len(methods) or {key.get("method") for key in keys} != methods:
        raise ValueError("Complete temporal source and fixed method keys required")
    for key in keys:
        fixed = key["method"] == "state_rollup.sum_zero@v1"
        shape: dict[str, freeze.Json] = {
            "kind": "FixedShape" if fixed else "SourceShape",
            "time": {
                "kind": "instant",
                "unit": "us",
                "timezone": zone if backend == "duckdb" else "UTC",
            },
        }
        if not fixed:
            shape.update(backend=backend, form="table", table_kind="native")
        if (
            key.get("shape") != shape
            or key.get("route") != ("artifact_python" if fixed else "ibis")
            or key.get("input_types") != (["int64"] if fixed else ["string"])
            or key.get("input_domains") != ["entity"]
        ):
            raise ValueError("Exact report-zone temporal method shape required")
    coordinates: list[freeze.Json] = [["key_0", "string"]]
    if not fold:
        coordinates.extend([["key_1", "int64"], ["key_2", "int64"]])
    if grid:
        coordinates.append(["key_3", "string"])
    retained = [freeze.obj(item) for item in freeze.arr(receipt["retained"])]
    if len(retained) != (2 if grid else 1):
        raise ValueError("Exact temporal producer and continuation results required")
    shapes: list[tuple[dict[str, freeze.Json], str, list[freeze.Json], tuple[str, ...]]] = [
        (
            retained[0],
            "MaterializedNumericRelation",
            coordinates,
            ("subject", "original_state", "coverage"),
        ),
    ]
    if grid:
        shapes.append(
            (
                retained[1],
                "MaterializedGroupedNumericRelation",
                coordinates[:1],
                ("original_state", "coverage"),
            )
        )
    for item, kind, fields, roles in shapes:
        schema: list[freeze.Json] = [
            *fields,
            ["value", "double" if mean else "int64"],
            ["cell_tag", "string"],
            ["cell_reason", "string"],
        ]
        parts: list[freeze.Json] = [{"role": role, "key_fields": fields} for role in roles]
        if freeze.encode(item) != freeze.encode(
            {"kind": kind, "verified": True, "schema": schema, "parts": parts}
        ):
            raise ValueError("Complete retained temporal keys and original state required")


def validate_window_consumer(backend: str, mode: str, receipt: dict[str, freeze.Json]) -> None:
    """Check declared string windows and all five semi-additive time folds."""
    if backend not in BACKENDS:
        raise ValueError("Known native backend required")
    _native_receipt(backend, receipt)
    inputs = freeze.obj(receipt["input"])
    fold_kind = mode.removeprefix("spatial_")
    spatial = mode in SPATIAL_FOLDS
    fold = mode in FOLDS or spatial
    if spatial and backend != "duckdb":
        raise ValueError("Shared spatial-before-temporal risk owns the DuckDB representative")
    zone = "America/New_York" if mode == "dst" else "UTC"
    if fold:
        roots = [freeze.obj(item) for item in freeze.arr(inputs.get("roots", []))]
        if len(roots) != 3 or any(
            not item.get("columns") or not item.get("values") for item in roots
        ):
            raise ValueError("Three independent temporal fold source roots required")
        expected: dict[str, freeze.Json] = {
            "fold": fold_kind,
            "a": (
                {"mean": "18", "first": "15", "last": "21", "min": "15", "max": "21"}
                if spatial
                else {"mean": "15", "first": "10", "last": "20", "min": "10", "max": "20"}
            )[fold_kind],
            "b": "Null(empty_contribution)",
            "resources": 0,
        }
        if spatial:
            expected.update(spatial_samples=[15, 21], sample_counts=[2, 2], raw_row_mean=9)
    elif mode in ("string-time", "dst"):
        if not inputs.get("columns") or not inputs.get("values"):
            raise ValueError("Independent temporal window source facts required")
        expected = {
            "values": [2, 0, 0, 0, 5] if mode == "dst" else [2, 0, 0, 0],
            "report_timezone": zone,
            "window_seconds": 90000 if mode == "dst" else 86400,
            "last_hour_included": mode == "dst",
            "declared_format": "%Y-%m-%d %H:%M:%S",
            "start_inclusive": True,
            "end_exclusive": True,
            "before_start_excluded": True,
            "resources": 0,
        }
    else:
        raise ValueError("Closed C06 window consumer mode required")
    if freeze.encode(freeze.obj(receipt["oracle"])) != freeze.encode(expected):
        raise ValueError("Independent temporal window oracle differs")
    _validate_state(backend, receipt, zone, grid=False, fold=fold, mean=fold_kind == "mean")


def consumer_invocation(backend: str, mode: str) -> tuple[str, str]:
    """Resolve one recorded mode to its exact owning Runtime invocation."""
    module = "tests.test_r93_capability_consumers"
    if mode in (*FOLDS, *SPATIAL_FOLDS):
        return (
            "tests.test_r93_multiroot_consumers",
            f"test_c04_c06_independent_roots_and_temporal_fold[{mode}-{backend}]",
        )
    if mode in ("string-time", "dst"):
        case = "utc" if mode == "string-time" else "dst"
        name = f"test_c06_declared_string_time_uses_owned_execution_functions[{case}-{backend}]"
    elif mode in ("all-history", "month-reset", "trailing-overlap"):
        name = f"test_c06_cumulative_keeps_anchor_and_overlap[{mode}-{backend}]"
    elif mode in ("spring-grid", "fall-grid"):
        name = f"test_c06_dst_grid_keeps_instant_edges_and_fixed_coordinate[{mode.removesuffix('-grid')}-{backend}]"
    else:
        names = {
            "native-time": "test_c06_native_timestamp_retains_reader_and_grid_authority",
            "aware-repeated-hour": "test_c06_aware_native_repeated_hour_keeps_distinct_instants",
            "civil-date-grid": "test_c06_civil_date_grid_keeps_date_keys_on_foreign_zone",
            "certified-calendar": "test_c06_certified_unequal_calendar_owns_native_boundaries",
        }
        if mode not in names:
            raise ValueError("Closed temporal invocation mode required")
        name = f"{names[mode]}[{backend}]"
    return module, name


def collect_temporal_proofs(
    directories: tuple[Path, ...], candidate: str
) -> dict[str, freeze.Json]:
    """Bind supporting receipts to identical frozen executions; grant no statuses."""
    if not directories:
        raise ValueError("Frozen temporal executions required")
    records: dict[str, freeze.Json] = {}
    authority: dict[str, freeze.Json] | None = None
    executions: list[tuple[Path, dict[str, freeze.Json]]] = []
    testcases: list[ET.Element] = []
    for directory in directories:
        verify(directory)
        payload = freeze.load(directory / "freeze")
        run = freeze.read(directory / "run.json")
        if (
            type(run.get("exit_code")) is not int
            or run["exit_code"] != 0
            or run.get("candidate_sha256") != candidate
            or freeze.obj(payload["candidate"])["content_sha256"] != candidate
            or (authority is not None and payload != authority)
        ):
            raise ValueError("Successful identical frozen temporal candidate required")
        authority = payload
        attachments = freeze.obj(run["attachments"])
        if "junit.xml" not in attachments:
            raise ValueError("Hashed temporal invocation report required")
        testcases.extend(
            ET.fromstring(read_attachment(directory, "junit.xml")).findall(".//testcase")
        )
        executions.append((directory, attachments))
    for directory, attachments in executions:
        for name, digest in attachments.items():
            if not name.startswith("c06-source-") or not name.endswith(".json"):
                continue
            backend, separator, mode = (
                name.removeprefix("c06-source-").removesuffix(".json").partition("-")
            )
            if not separator or backend not in BACKENDS:
                raise ValueError("Exact temporal consumer invocation must pass")
            module, node = consumer_invocation(backend, mode)
            selected = [
                item
                for item in testcases
                if item.attrib.get("classname") == module and item.attrib.get("name") == node
            ]
            if len(selected) != 1 or list(selected[0]):
                raise ValueError("Exact temporal consumer invocation must pass uniquely")
            if name in records:
                raise ValueError("Duplicate temporal consumer receipt")
            receipt = freeze.obj(freeze.checked(json.loads(read_attachment(directory, name))))
            if mode in WINDOW_MODES:
                validate_window_consumer(backend, mode, receipt)
            else:
                validate_grid_consumer(backend, mode, receipt)
            records[name] = {"sha256": digest, "candidate_sha256": candidate, "observed": receipt}
    if not records:
        raise ValueError("Observed temporal consumers required")
    return records
