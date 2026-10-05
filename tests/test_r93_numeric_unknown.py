"""Public Unknown Duration does not confer scalar statistical qualification."""

import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import NoReturn

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import StatisticalRelationError
from marivo.analysis.methods.physical import DurationType
from marivo.datasource.adapters import SourceSession
from tests.lifecycle_r75_fixtures import START, build_lifecycle_public


@pytest.mark.runtime
def test_public_unknown_duration_deviation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session, population, window, claims, _ = build_lifecycle_public(tmp_path, backend_name="sqlite")
    history = session.lifecycle.replay(
        ms.ref.state_model("commerce.model"),
        population=population,
        window=window,
        seed=mv.from_inception(),
        completeness=(replace(claims[0], complete_through=START - timedelta(seconds=1)),),
    ).execute()
    observed = history.intervals().observed_duration.execute()
    assert isinstance(observed, mv.MaterializedNumericRelation)
    assert observed._node.root.value_type == DurationType("us")
    frame = observed.to_pandas()
    assert "unknown" in set(frame.cell_tag)
    assert frame.loc[frame.cell_tag == "unknown", "value"].isna().all()
    assert observed._dataset is not None
    observed._dataset.verified()
    before = set(tmp_path.rglob("*.parquet"))

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Retained Unknown Duration control must not read the source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    errors: list[dict[str, str]] = []
    for method in ("zscore", "mad"):
        with pytest.raises(StatisticalRelationError) as refused:
            observed.deviation(method=method).execute()
        assert refused.value.code == "r8.numeric_unqualified"
        assert refused.value.expected == "int64, float64 or Decimal(p,s)"
        assert refused.value.received == "interval('us')"
        assert refused.value.location == "analysis.deviation.r8.numeric_unqualified"
        assert refused.value.expected is not None
        assert refused.value.received is not None
        assert refused.value.location is not None
        assert set(tmp_path.rglob("*.parquet")) == before
        assert session._runtime.store.resources(session._runtime.session_ref) == ()
        errors.append(
            {
                "method": method,
                "kind": type(refused.value).__name__,
                "expected": refused.value.expected,
                "received": refused.value.received,
                "location": refused.value.location,
            }
        )
    if directory := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(directory, "unknown-duration-deviation-refusal.json").write_text(
            json.dumps(
                {
                    "producer": "history.intervals().observed_duration",
                    "public_result": "MaterializedNumericRelation",
                    "physical_type": "duration(us)",
                    "cell_tags": frame.cell_tag.tolist(),
                    "errors": errors,
                    "fixed_source_reads_forbidden": True,
                    "no_partial_publication": True,
                    "resources": 0,
                    "scalar_numeric_unknown_runtime_verified": False,
                    "boundary": "Public Unknown Duration producer and deviation admission only; no scalar statistical Unknown qualification",
                },
                sort_keys=True,
            )
        )
