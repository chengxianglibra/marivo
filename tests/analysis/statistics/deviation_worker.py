"""Separate producer, offline fixed-kernel and cold recovery processes."""

from __future__ import annotations

import json
import os
import sys
from fractions import Fraction
from pathlib import Path
from typing import Literal, TypedDict
from unittest.mock import patch

import duckdb
import ibis
import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.rules import DeviationFit
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.statistics.deviation_oracle import decimal_finish, expected


class Manifest(TypedDict):
    session: str
    method: Literal["zscore", "mad"]
    input: str
    result: str
    fixed: str
    selected: str


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline deviation touched current Semantic or a source")


def assert_scores(result: mv.MaterializedDeviationResult) -> None:
    observed = result.observed.to_pandas()
    scores = result.score.to_pandas()
    values = tuple(observed.loc[observed.cell_tag == "defined", "value"])
    parameters = result._node.definition.parameters
    assert isinstance(parameters, DeviationFit)
    center, _, _, oracle = expected(values, parameters.method)
    assert scores.loc[scores.cell_tag == "defined", "value"].tolist() == list(oracle)
    reference = result.reference.to_pandas()
    deviations = result.deviation.to_pandas()
    reference_dataset = result.reference._dataset
    assert reference_dataset is not None
    output_type = reference_dataset.verified().primary.schema.field("value").type
    if isinstance(output_type, pa.Decimal128Type):
        assert reference.value.tolist() == [decimal_finish(center, output_type.scale)] * len(values)
        assert deviations.value.tolist() == [
            decimal_finish(Fraction(x) - center, output_type.scale) for x in values
        ]
    else:
        assert reference.value.tolist() == [float(center)] * len(values)
        assert deviations.value.tolist() == [float(Fraction(x) - center) for x in values]
    assert "original_state" not in result.score.contract().retained_parts
    assert "subject" not in result.reference.contract().retained_parts
    facts = dict(result.contract()._facts)
    assert facts["original_count"] == str(len(observed))
    assert result.evidence_digest().finding_count == 0


def run(root: Path, phase: str, method: Literal["zscore", "mad"]) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    if phase == "produce":
        ms.load(workspace_dir=root)
        session = mv.session.get_or_create("r82-process", report_timezone="UTC")
        source = session.members(ms.ref.entity("sales.order")).read(
            ms.ref.measure("sales.order.amount")
        )
        original = source.execute()
        result = source.deviation(method=method).execute()
        assert_scores(result)
        manifest: Manifest = {
            "session": session.id,
            "method": method,
            "input": original.evidence_digest().artifact_ref.ref,
            "result": result.evidence_digest().artifact_ref.ref,
            "fixed": "",
            "selected": "",
        }
        (root / "deviation-recovery.json").write_text(json.dumps(manifest))
    else:
        manifest = json.loads((root / "deviation-recovery.json").read_text())
        with (
            patch.object(SemanticProject, "load", forbidden),
            patch.object(ms, "load", forbidden),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(SourceSession, "batches", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            session = mv.session.resume(manifest["session"], by="id")
            captured_input = session.artifact(manifest["input"])
            captured_result = session.artifact(manifest["result"])
            assert isinstance(captured_input, mv.MaterializedNumericRelation)
            assert isinstance(captured_result, mv.MaterializedDeviationResult)
            original, result = captured_input, captured_result
            assert_scores(result)
            fitted = original.deviation(method=method).execute()
            assert_scores(fitted)
            selected = fitted.where(fitted.score.value.gt(0)).execute()
            assert (
                selected.observed.to_pandas().value.tolist()
                == fitted.observed.to_pandas()
                .loc[fitted.score.to_pandas().value > 0, "value"]
                .tolist()
            )
            if phase == "fixed":
                manifest["fixed"] = fitted.evidence_digest().artifact_ref.ref
                manifest["selected"] = selected.evidence_digest().artifact_ref.ref
                (root / "deviation-recovery.json").write_text(json.dumps(manifest))
            else:
                assert fitted.evidence_digest().artifact_ref.ref == manifest["fixed"]
                assert selected.evidence_digest().artifact_ref.ref == manifest["selected"]
    print(json.dumps({"accepted": phase, "method": method}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], "zscore" if sys.argv[3] == "zscore" else "mad")
