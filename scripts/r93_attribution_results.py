"""Strict C09 source/fixed attribution witnesses without scenario grants."""

from scripts import r9_qualification_requirements as freeze
from scripts.r93_multiroot_results import BACKENDS, _native_receipt


def validate_attribution_consumer(
    backend: str, metric: str, axis: str, receipt: dict[str, freeze.Json]
) -> None:
    """Check original endpoints, independent allocation and retained authority."""
    if backend not in BACKENDS or metric not in ("sum", "mean") or axis not in ("single", "joint"):
        raise ValueError("Closed native C09 representative required")
    _native_receipt(backend, receipt)
    inputs = freeze.obj(receipt["input"])
    if not inputs.get("columns") or not inputs.get("values"):
        raise ValueError("Independent original source facts required")
    contributions: list[int | float] = [2, 1, -5] if metric == "sum" else [1, 0.5, -2.5]
    expected: dict[str, freeze.Json] = {
        "metric_kind": metric,
        "axis_kind": axis,
        "input_key_types": ["string", "int64", "int64"],
        "current": [2, 4, 0] if metric == "sum" else [1, 2, 0],
        "baseline": [0, 3, 5] if metric == "sum" else [0, 1.5, 2.5],
        "contributions": [*contributions],
        "top_k_other": [-3, 1] if metric == "sum" else [-1.5, 0.5],
        "selected": [1, 2] if metric == "sum" else [0.5, 1],
        "hierarchy": [*sorted(contributions * 2)] if axis == "joint" else None,
        "ordinary_source_snapshot_forbidden": True,
        "fixed_source_reads_forbidden": True,
        "no_additional_native_submission": True,
        "resources": 0,
    }
    if freeze.encode(receipt["oracle"]) != freeze.encode(expected):
        raise ValueError("Independent original endpoint/allocation oracle differs")
    method = (
        "attribution.additive_difference@v1" if metric == "sum" else "attribution.component_mix@v1"
    )
    selected = [
        freeze.obj(item)
        for item in freeze.arr(receipt["physical_keys"])
        if freeze.obj(item).get("method") == method
    ]
    if len(selected) != 2:
        raise ValueError("Exactly one source and one fixed attribution key required")
    for source, item in zip((True, False), selected, strict=True):
        shape: dict[str, freeze.Json] = (
            {
                "kind": "SourceShape",
                "backend": backend,
                "form": "table",
                "table_kind": "native",
                "time": {"kind": "instant", "unit": "us", "timezone": "UTC"},
            }
            if source
            else {"kind": "FixedShape", "time": {"kind": "NoTime"}}
        )
        if (
            item.get("shape") != shape
            or item.get("input_types") != (["int64"] * 2 if metric == "sum" else ["float64"] * 2)
            or item.get("input_domains") != ["singleton", "singleton"]
            or item.get("route") != ("ibis_python" if source else "artifact_python")
        ):
            raise ValueError("Exact original attribution types/domains/routes required")
    fields: list[freeze.Json] = [["key_0", "int64"], ["key_1", "string"], ["key_2", "int64"]]
    if axis == "joint":
        fields = [["key_0", "int64"], ["key_1", "string"], ["key_2", "string"], ["key_3", "int64"]]
    result: dict[str, freeze.Json] = {
        "kind": "MaterializedAttributionResult",
        "schema": [
            *fields,
            ["value", "int64" if metric == "sum" else "double"],
            ["cell_tag", "string"],
            ["cell_reason", "string"],
        ],
        "parts": [
            {
                "role": role,
                "key_fields": []
                if role in ("current_endpoint", "baseline_endpoint", "basis")
                else fields,
            }
            for role in (
                "current_endpoint",
                "baseline_endpoint",
                "basis",
                "allocation",
                "reconciliation",
                "selection_scope",
            )
        ],
        "verified": True,
    }
    if freeze.encode(receipt["retained"]) != freeze.encode([result, result]):
        raise ValueError("Exact source/fixed attribution endpoints and allocation parts required")
