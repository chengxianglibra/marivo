"""Shared immutable result snapshots for fresh-process recovery checks."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import NoReturn

import pandas as pd

from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode
from marivo.analysis.public_dsl import _MaterializedRead
from tests.support.json import Json, checked, digest, obj


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Fixed recovery accessed a live source or Semantic")


def snapshot(result: _MaterializedRead, *, frame: pd.DataFrame | None = None) -> dict[str, Json]:
    assert result._dataset is not None
    descriptor = result._dataset.artifact.descriptor
    verified = result._dataset.verified()
    evidence = result.evidence_digest()
    return obj(
        checked(
            {
                "artifact": result._dataset.artifact.artifact_ref,
                "descriptor_sha256": digest(encode(descriptor, DESCRIPTOR).encode()),
                "schema": descriptor.realized_schema,
                "execution_key_digest": descriptor.execution_key_digest,
                "continuation_snapshot_digest": descriptor.continuation_snapshot_digest,
                "rows": json.loads(
                    (result.to_pandas() if frame is None else frame).to_json(
                        orient="table", index=False
                    )
                ),
                "parts": [part.role for part in verified.parts],
                "contract": json.loads(json.dumps(asdict(result.contract()), default=str)),
                "evidence": json.loads(json.dumps(asdict(evidence), default=str)),
                "findings": json.loads(json.dumps(asdict(result.findings(limit=100)), default=str)),
            }
        )
    )
