"""Recover C08 retained reference, rank and full-opportunity continuations."""

import json

import pandas as pd

import marivo.analysis as mv
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR
from marivo.analysis.materialization.graph_protocol import encode as descriptor_encode
from marivo.analysis.public_dsl import _MaterializedRead
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.support.json import Json, checked, digest, obj


def saved(
    result: _MaterializedRead | mv.MaterializedTable, *, frame: pd.DataFrame | None = None
) -> dict[str, Json]:
    if isinstance(result, _MaterializedRead):
        return snapshot(result, frame=frame)
    assert result._dataset is not None
    descriptor = result._dataset.artifact.descriptor
    verified = result._dataset.verified()
    return obj(
        checked(
            {
                "artifact": result._dataset.artifact.artifact_ref,
                "descriptor_sha256": digest(descriptor_encode(descriptor, DESCRIPTOR).encode()),
                "schema": descriptor.realized_schema,
                "rows": json.loads(
                    (result.to_pandas() if frame is None else frame).to_json(
                        orient="table", index=False
                    )
                ),
                "parts": [part.role for part in verified.parts],
            }
        )
    )


def reference_parts(
    value: mv.MaterializedNumericRelation | mv.MaterializedSelectedNumericRelation,
) -> dict[str, Json]:
    assert value._dataset is not None
    parts = {
        part.role: digest(
            json.dumps(
                {"schema": str(part.table.schema), "rows": part.table.to_pylist()},
                sort_keys=True,
                default=str,
            ).encode()
        )
        for part in value._dataset.verified().parts
        if part.role in ("fixed_reference", "reference_proof", "stratum_values")
    }
    assert set(parts) == {"fixed_reference", "reference_proof", "stratum_values"}
    return dict(parts)
