"""Strict bounded C10 native and fixed-selection execution receipts."""

from scripts import r9_qualification_requirements as freeze
from scripts.r93_multiroot_results import BACKENDS, _native_receipt


def admitted(backend: str, kind: str) -> bool:
    """Return the closed native accuracy boundary exercised by C10 consumers."""
    return backend in BACKENDS and (
        (kind in ("distinct", "identity") and backend != "clickhouse")
        or kind == "approx_distinct"
        or (kind == "quantile" and backend in ("duckdb", "postgres"))
        or (kind == "approx_quantile" and backend in ("duckdb", "postgres", "trino", "clickhouse"))
    )


def validate_distribution_consumer(
    backend: str, kind: str, receipt: dict[str, freeze.Json]
) -> None:
    """Check source precision, bounded oracle and genuinely executed fixed selection."""
    if not admitted(backend, kind):
        raise ValueError("Admitted C10 native accuracy profile required")
    _native_receipt(backend, receipt)
    inputs = freeze.obj(receipt["input"])
    if not inputs.get("columns") or not inputs.get("values"):
        raise ValueError("Independent original identity/value inputs required")
    count = kind in ("distinct", "identity", "approx_distinct")
    expected: dict[str, freeze.Json] = {
        "kind": kind,
        "input_identity_types": ["int64", "string"],
        "values": [4, 1, 0]
        if kind == "identity"
        else [2, 1, 0]
        if count
        else [4, 6, None]
        if kind == "quantile"
        else ["bounded_2_through_6", 6, None],
        "empty_cell": "defined" if count else "null",
        "selected_members": ["a", "b"],
        "fixed_source_reads_forbidden": True,
        "no_additional_native_submission": True,
        "nonadditive_rollup_and_attribution_refused": True,
        "resources": 0,
    }
    if freeze.encode(receipt["oracle"]) != freeze.encode(expected):
        raise ValueError("Independent precision/empty/fixed-selection oracle differs")
    method = {
        "distinct": "metric.distinct@v1",
        "identity": "metric.distinct@v1",
        "quantile": "metric.quantile@v1",
        "approx_distinct": "metric.approx_distinct@v1",
        "approx_quantile": "metric.approx_quantile@v1",
    }[kind]
    source_shape: dict[str, freeze.Json] = {
        "kind": "SourceShape",
        "backend": backend,
        "form": "table",
        "table_kind": "native",
        "time": {"kind": "instant", "unit": "us", "timezone": "UTC"},
    }
    keys: list[freeze.Json] = [
        {
            "method": "parts_transport@v1",
            "input_types": ["string"],
            "input_domains": ["entity"],
            "shape": source_shape,
            "route": "ibis",
        },
        {
            "method": method,
            "input_types": ["string"],
            "input_domains": ["entity"],
            "shape": source_shape,
            "route": "ibis",
        },
        {
            "method": "parts_transport@v1",
            "input_types": ["int64" if count else "float64"],
            "input_domains": ["entity"],
            "shape": {"kind": "FixedShape", "time": {"kind": "NoTime"}},
            "route": "artifact_python",
        },
    ]
    if freeze.encode(receipt["physical_keys"]) != freeze.encode(keys):
        raise ValueError("Exact native distribution and fixed continuation keys required")
    retained: list[freeze.Json] = [
        {
            "kind": result_kind,
            "schema": [
                ["key_0", "string"],
                ["value", "int64" if count else "double"],
                ["cell_tag", "string"],
                ["cell_reason", "string"],
            ],
            "parts": [
                {"role": role, "key_fields": [["key_0", "string"]]}
                for role in ("subject", "coverage")
            ],
            "verified": True,
        }
        for result_kind in ("MaterializedNumericRelation", "MaterializedSelectedNumericRelation")
    ]
    if freeze.encode(receipt["retained"]) != freeze.encode(retained):
        raise ValueError("Exact source/fixed-selection schema and Subject/coverage required")
    if not count and not any(
        "0.25" in str(item) for item in freeze.arr(receipt["actual_native_submissions"])
    ):
        raise ValueError("Actual quantile probability submission required")
