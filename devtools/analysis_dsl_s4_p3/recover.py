"""Recover one public Artifact in a fresh process with its source offline."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from common import emit, rows

import marivo.analysis as mv
import marivo.semantic as ms


def main(root: Path, journey: str, session_id: str, reference: str) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root.resolve())
    session = mv.session.resume(session_id, by="id")
    failed_source: str | None = None
    failed_source_detail: str | None = None
    if journey == "j1":
        try:
            session.members(ms.ref.entity("sales.customer")).observe(
                ms.ref.metric("sales.revenue"),
                during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
                via=ms.ref.relationship("sales.order_buyer"),
                by=(ms.ref.entity("sales.customer"),),
            ).execute()
        except Exception as exc:
            failed_source = type(exc).__name__
            failed_source_detail = str(exc)
        else:
            raise AssertionError("The removed source unexpectedly remained available")

    fixed = session.artifact(reference)
    if journey == "j1":
        assert isinstance(fixed, mv.MaterializedNumericRelation)
        continuation_rows = rows(fixed.rollup().execute().to_pandas())
    elif journey == "j2":
        assert isinstance(fixed, mv.MaterializedDifferenceRelation)
        selected = fixed.where(fixed.value.lt(0)).execute()
        continuation_rows = rows(selected.members().execute().to_pandas())
    elif journey == "j3":
        assert isinstance(fixed, mv.MaterializedRatioRelation)
        continuation_rows = rows(fixed.rollup().execute().to_pandas())
    elif journey == "j4":
        assert isinstance(fixed, mv.MaterializedAssociationResult)
        coefficient = fixed.coefficient
        continuation_rows = rows(coefficient.where(coefficient.value.lt(0)).execute().to_pandas())
    else:
        raise ValueError(journey)
    emit(
        {
            "journey": journey,
            "session_id": session.id,
            "artifact_ref": fixed.state.artifact_ref.ref,
            "kind": type(fixed).__name__,
            "recovered": rows(fixed.to_pandas()),
            "continuation": continuation_rows,
            "failed_source": failed_source,
            "failed_source_detail": failed_source_detail,
        }
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4])
