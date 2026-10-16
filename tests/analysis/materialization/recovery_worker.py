"""Fresh-process public continuations with source and Semantic access forbidden."""

from __future__ import annotations

import json
from typing import NoReturn

import pandas as pd

import marivo.analysis as mv
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode
from tests.support.json import Json, checked, digest


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("retained execution accessed a source or current Semantic")


def snapshot(
    result: mv.MaterializedNumericRelation | mv.MaterializedRatioRelation,
    *,
    frame: pd.DataFrame | None = None,
) -> Json:
    assert result._dataset is not None
    verified = result._dataset.verified()
    evidence = result.evidence_digest()
    descriptor = result._dataset.artifact.descriptor
    return checked(
        {
            "artifact": result.state.artifact_ref.ref,
            "run": result._dataset.artifact.producing_run_ref,
            "descriptor_sha256": digest(encode(descriptor, DESCRIPTOR).encode()),
            "execution_key_digest": descriptor.execution_key_digest,
            "continuation_snapshot_digest": descriptor.continuation_snapshot_digest,
            "schema": descriptor.realized_schema,
            "frame": json.loads(
                (result.to_pandas() if frame is None else frame).to_json(orient="split")
            ),
            "parts": [part.role for part in verified.parts],
            "required_parts": list(result.contract().required_parts),
            "evidence": {
                "evidence_digest": evidence.evidence_digest,
                "finding_set_digest": evidence.finding_set_digest,
                "finding_count": evidence.finding_count,
            },
            "contract_facts": dict(result.contract()._facts),
        }
    )
