"""Offline allocation retains original target, basis and reconciliation scope."""

import json

import marivo.analysis as mv
from tests.support.json import Json, checked, digest


def proof(value: mv.MaterializedAttributionResult) -> dict[str, Json]:
    assert value._dataset is not None
    parts = value._dataset.verified().parts
    kept: dict[str, Json] = {
        part.role: digest(
            json.dumps(
                {"schema": str(part.table.schema), "rows": part.table.to_pylist()},
                sort_keys=True,
                default=str,
            ).encode()
        )
        for part in parts
        if part.role != "selection_scope"
    }
    assert set(kept) == {
        "current_endpoint",
        "baseline_endpoint",
        "current_endpoint_coordinates",
        "baseline_endpoint_coordinates",
        "basis",
        "allocation",
        "reconciliation",
    }
    reconciliation = next(part.table.to_pylist() for part in parts if part.role == "reconciliation")
    return {"parts": kept, "reconciliation": checked(reconciliation)}
