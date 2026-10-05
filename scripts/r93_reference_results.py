"""Strict native C08 representative receipts, without scenario grants."""

from scripts import r9_qualification_requirements as freeze
from scripts.r93_multiroot_results import BACKENDS, _native_receipt


def validate_reference_consumer(
    backend: str, operation: str, receipt: dict[str, freeze.Json]
) -> None:
    """Verify complete identities, source/fixed keys and owning retained parts."""
    if backend not in BACKENDS or operation not in ("share", "rank", "cohort"):
        raise ValueError("Closed native C08 consumer required")
    _native_receipt(backend, receipt)
    inputs = freeze.obj(receipt["input"])
    if not inputs.get("columns") or not inputs.get("values"):
        raise ValueError("Independent source facts required")
    expected: dict[str, freeze.Json] = {
        "operation": operation,
        "complete_key_types": ["string", "int64", "int64"],
        "values": [1 / 3, 0, 2 / 3]
        if operation == "share"
        else [2, 3, 1]
        if operation == "rank"
        else None,
        "member_keys": [["a", 9007199254740992, 1], ["b", 9007199254740993, 1]]
        if operation == "cohort"
        else None,
        "fixed_source_reads_forbidden": True,
        "no_additional_native_submission": True,
        "resources": 0,
    }
    if freeze.encode(receipt["oracle"]) != freeze.encode(expected):
        raise ValueError("Independent C08 complete-key oracle differs")
    method = {
        "share": "reference.share@v1",
        "rank": "display.rank@v1",
        "cohort": "domain.cohort@v1",
    }[operation]
    selected = [
        freeze.obj(item)
        for item in freeze.arr(receipt["physical_keys"])
        if freeze.obj(item).get("method") == method
    ]
    if len(selected) != 2:
        raise ValueError("Exactly one source and one fixed C08 method key required")
    domains = {
        "share": ["entity", "singleton", "entity"],
        "rank": ["entity"],
        "cohort": ["entity", "entity"],
    }[operation]
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
        types = (
            ["string", "int64"] if operation == "cohort" and source else ["int64"] * len(domains)
        )
        if (
            item.get("shape") != shape
            or item.get("input_types") != types
            or item.get("input_domains") != domains
            or item.get("route")
            != (
                ("ibis" if operation == "cohort" else "ibis_python")
                if source
                else "artifact_python"
            )
        ):
            raise ValueError("Exact C08 method types, domains and routes required")
    fields: list[freeze.Json] = [["key_0", "string"], ["key_1", "int64"], ["key_2", "int64"]]
    roles = {
        "share": ("fixed_reference", "reference_proof", "stratum_values"),
        "rank": (
            "subject",
            "original_state",
            "coverage",
            "values",
            "ranks",
            "ranking_domain",
            "partitions",
            "ordering",
        ),
        "cohort": ("subject", "cohort_decision"),
    }[operation]
    expected_result: dict[str, freeze.Json] = {
        "kind": "MaterializedAnalysisDomain"
        if operation == "cohort"
        else "MaterializedNumericRelation",
        "schema": fields
        if operation == "cohort"
        else [
            *fields,
            ["value", "int64" if operation == "rank" else "double"],
            ["cell_tag", "string"],
            ["cell_reason", "string"],
        ],
        "parts": [
            {"role": role, "key_fields": [] if role == "fixed_reference" else fields}
            for role in roles
        ],
        "verified": True,
    }
    if freeze.encode(receipt["retained"]) != freeze.encode([expected_result, expected_result]):
        raise ValueError("Exact complete-key source/fixed C08 parts required")
