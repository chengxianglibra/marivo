"""Public Null pairing and wrong-domain refusal from independent raw facts."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import duckdb
import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import statistical_execution
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.packaging.graph_journeys import NAMES, _create
from tests.shared_fixtures import DslCase, analysis_dsl_rows, export_dsl_parquet_models
from tests.support.json import Json, checked, obj, read


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("offline association touched a source or reran an exact hit")


def run(root: Path, phase: str) -> dict[str, Json]:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state_path = root / "null-pairs.json"
    if phase == "produce":
        session = _create(root, "j4", "table")
        with duckdb.connect(str(root / "warehouse.duckdb")) as connection:
            connection.execute("UPDATE reading SET energy=NULL WHERE device_id='D'")
        models = root / "models/semantic/operations/models.py"
        models.write_text(
            models.read_text()
            + "\nnullable_energy = ms.aggregate(name='nullable_energy', measure=amount, "
            "agg='sum', time=ordered_at, nulls=ms.nulls.ignore(), empty=ms.empty.null())\n"
        )
        catalog = ms.load(workspace_dir=root)
        case = DslCase("j4", NAMES, root, root / "warehouse.duckdb", catalog, session)
        export_dsl_parquet_models(case, root)
        ms.load(workspace_dir=root)
        population = session.members(ms.ref.entity("operations.device"))
        window = mv.time_scope(start="2026-08-01", end="2026-09-01")
        path = ms.ref.relationship("operations.reading_device")
        left_logical = population.observe(
            ms.ref.metric("operations.nullable_energy"), during=window, via=path
        )
        right_logical = population.observe(
            ms.ref.metric("operations.reading_count"), during=window, via=path
        )
        left, right = left_logical.execute(), right_logical.execute()
        assert isinstance(left, mv.MaterializedNumericRelation)
        assert isinstance(right, mv.MaterializedNumericRelation)
        assert left.to_pandas().cell_tag.tolist() == ["defined"] * 3 + ["null"]
        result = left_logical.correlate(right_logical, method="spearman").execute()
        # Raw complete vectors are (1,2,4) and (4,1,3); centered ranks give -1/2.
        rows = result.to_pandas()
        assert rows.coefficient.tolist() == [-0.5]
        assert rows.complete_pair_count.tolist() == [3]
        assert rows.null_pair_count.tolist() == [1]
        state: dict[str, Json] = {
            "session": session.id,
            "left": left.state.artifact_ref.ref,
            "right": right.state.artifact_ref.ref,
            "source": snapshot(result),
            "raw_facts": checked(
                [
                    [*row[:-1], None if row[1] == "D" else row[-1]]
                    for row in analysis_dsl_rows("j4").orders
                ]
            ),
            "oracle": {"coefficient": -0.5, "complete_pairs": 3, "null_pairs": 1},
        }
        (root / "warehouse.duckdb").unlink()
        shutil.rmtree(root / "models")
        shutil.rmtree(root / "source_files")
    else:
        state = read(state_path)
        with (
            patch.object(ms, "load", forbidden),
            patch.object(SourceSession, "__init__", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            identity = state["session"]
            assert isinstance(identity, str)
            session = mv.session.resume(identity, by="id")
            left_ref, right_ref = state["left"], state["right"]
            assert isinstance(left_ref, str) and isinstance(right_ref, str)
            left_loaded, right_loaded = session.artifact(left_ref), session.artifact(right_ref)
            assert isinstance(left_loaded, mv.MaterializedNumericRelation)
            assert isinstance(right_loaded, mv.MaterializedNumericRelation)
            left, right = left_loaded, right_loaded
            before = len(session.runs().items)
            operation = left.correlate(right, method="spearman")
            with patch.object(
                statistical_execution,
                "execute",
                forbidden if phase == "cold" else statistical_execution.execute,
            ):
                result = operation.execute()
            assert len(session.runs().items) - before == (0 if phase == "cold" else 1)
            if phase == "fixed":
                state["fixed"] = snapshot(result)
            else:
                assert snapshot(result) == obj(state["fixed"])
    assert result.to_pandas().coefficient.tolist() == [-0.5]
    # Filtering one operand must refuse mismatched complete identity images.
    short = right.where(right.value.gt(1)).execute()
    published_before = publication_counts(session)
    with pytest.raises(AnalysisError, match="different complete composite key sets"):
        left.correlate(short, method="spearman").execute()
    assert publication_counts(session) == published_before
    assert session._runtime.store.resources(session._runtime.session_ref) == ()
    state_path.write_text(json.dumps(state))
    return {"snapshot": snapshot(result), "oracle": state["oracle"], "wrong_domain": "refused"}
