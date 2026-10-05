"""Strict bounded C07 consumer receipts; development captures grant no scenarios."""

import xml.etree.ElementTree as ET

from scripts import r9_qualification_requirements as freeze
from scripts.r93_multiroot_results import BACKENDS, _native_receipt

CONTROL_NODES = (
    "test_union_keep_distinguishes_missing_coordinate_and_rejects_filtered_empty[False]",
    *(
        f"test_cohort_group_metric_empty_preserves_metric_null_policy[{metric}-False]"
        for metric in ("revenue", "order_count", "count_ratio")
    ),
    *(
        f"test_period_change_retains_original_buckets_and_rejects_renumbering[{kind}-False]"
        for kind in ("utc", "date", "aware_local")
    ),
    "test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship[False]",
    *(
        f"test_union_keeps_present_nondefined_cell_separate_from_absence[{kind}]"
        for kind in ("null", "undefined")
    ),
    "test_period_change_after_bucket_rollup[False]",
    "test_composite_keys_double_empty_and_wrong_key_images[False]",
    "test_comparison_rejects_duplicate_target_keys",
    "test_float_fold_comparison_without_error_envelope_rejects_statically",
    "test_ordinary_ratio_and_cohort_singleton[False]",
)


def validate_comparison_controls(reports: tuple[bytes, ...]) -> None:
    """Require independently owned shared risks once, without backend multiplication."""
    module = "tests.test_analysis_comparison_runtime_r62"
    cases = [item for report in reports for item in ET.fromstring(report).findall(".//testcase")]
    for name in CONTROL_NODES:
        selected = [
            item
            for item in cases
            if item.attrib.get("classname") == module and item.attrib.get("name") == name
        ]
        if len(selected) != 1 or list(selected[0]):
            raise ValueError(f"Required shared C07 control not passed: {name}")


def validate_comparison_consumer(
    backend: str, operation: str, receipt: dict[str, freeze.Json]
) -> None:
    """Check complete typed identities, missing-side states and both execution routes."""
    if backend not in BACKENDS or operation not in ("nested", "union", "ratio"):
        raise ValueError("Closed native C07 consumer required")
    _native_receipt(backend, receipt)
    inputs = freeze.obj(receipt["input"])
    if not inputs.get("columns") or not inputs.get("values"):
        raise ValueError("Independent native comparison facts required")
    expected: dict[str, freeze.Json] = {
        "operation": operation,
        "complete_key_types": ["string", "int64", "int64"],
        "values": [2, 0, 4]
        if operation == "nested"
        else [1, None, 1]
        if operation == "ratio"
        else [None, None],
        "cell_reason": "missing_side"
        if operation == "union"
        else "zero_denominator"
        if operation == "ratio"
        else None,
        "fixed_source_reads_forbidden": True,
        "no_additional_native_submission": True,
        "resources": 0,
    }
    if freeze.encode(receipt["oracle"]) != freeze.encode(expected):
        raise ValueError("Independent complete-key comparison oracle differs")
    method = "cell.ratio@v1" if operation == "ratio" else "cell.difference@v1"
    selected = [
        freeze.obj(item)
        for item in freeze.arr(receipt["physical_keys"])
        if freeze.obj(item).get("method") == method
    ]
    sources = [item for item in selected if freeze.obj(item["shape"]).get("kind") == "SourceShape"]
    fixed = [item for item in selected if freeze.obj(item["shape"]).get("kind") == "FixedShape"]
    if (
        len(selected) != (4 if operation == "nested" else 2)
        or len(sources) != (3 if operation == "nested" else 1)
        or len(fixed) != 1
    ):
        raise ValueError("Every recursive source node and fixed comparison key required")
    for item in selected:
        source = item in sources
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
        route = ("ibis_python" if operation == "ratio" else "ibis") if source else "artifact_python"
        if (
            item.get("shape") != shape
            or item.get("input_types") != ["int64", "int64"]
            or item.get("input_domains") != ["entity", "entity"]
            or item.get("route") != route
        ):
            raise ValueError("Exact recursive endpoint types, domains and routes required")
    fields: list[freeze.Json] = [["key_0", "string"], ["key_1", "int64"], ["key_2", "int64"]]
    expected_result: dict[str, freeze.Json] = {
        "kind": "MaterializedNumericRelation"
        if operation == "ratio"
        else "MaterializedDifferenceRelation",
        "verified": True,
        "schema": [
            *fields,
            ["value", "double" if operation == "ratio" else "int64"],
            ["cell_tag", "string"],
            ["cell_reason", "string"],
        ],
        "parts": [
            {"role": role, "key_fields": fields}
            for role in ("subject", "current_endpoint", "baseline_endpoint", "correspondence")
        ],
    }
    if freeze.encode(receipt["retained"]) != freeze.encode([expected_result, expected_result]):
        raise ValueError("Both complete retained comparison endpoints and correspondence required")
