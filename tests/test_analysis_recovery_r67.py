"""Fresh-process R6 recovery and independently pinned consumer retirement."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.runtime
@pytest.mark.parametrize("source", ["table", "parquet"])
@pytest.mark.parametrize("scenario", ["a02", "a06", "a07", "a08"])
def test_public_r6_journeys_recover_in_three_processes(
    tmp_path: Path, source: str, scenario: str
) -> None:
    project = tmp_path / "project"
    reports = []
    for phase in ("produce", "continue", "recover"):
        output = tmp_path / f"{phase}.json"
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.installed_r6_journeys",
                phase,
                str(project),
                scenario,
                source,
                str(output),
            ],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(ROOT), "MARIVO_TELEMETRY": "off"},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        reports.append(json.loads(output.read_text()))
    assert len({report["pid"] for report in reports}) == 3
    for field in ("session", "snapshots", "oracle", "facts_sha256", "edge_checks", "shared_nodes"):
        assert reports[0][field] == reports[1][field] == reports[2][field]
    assert reports[1]["continuations"] == reports[2]["continuations"]
    assert reports[1]["run_count"] > reports[0]["run_count"]
    assert reports[1]["run_count"] == reports[2]["run_count"]


def test_retired_r6_consumers_have_no_export_registration_or_runtime_dispatch() -> None:
    import marivo
    import marivo.analysis as mv
    from marivo.analysis.observation.contracts import (
        make_family_registry,
        make_ids,
        producer_contract,
    )
    from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
    from marivo.analysis.operators.registry import legacy_source_migration_stage

    for name in (
        "LogicalDeltaDataset",
        "MaterializedDeltaDataset",
        "LogicalAttributionDataset",
        "MaterializedAttributionDataset",
    ):
        assert name not in mv.__all__ and not hasattr(mv, name)
    for cls in (LogicalMetricDataset, MaterializedMetricDataset):
        assert not hasattr(cls, "compare")
    registry = make_family_registry(make_ids(()))
    assert not {"delta", "attribution"} & {f.family_id for f in registry.registrations}
    for operator in (
        "metric.compare",
        "delta.attribute",
        "delta.attribute_expanded",
        "delta.rank",
        "attribution.where",
    ):
        with pytest.raises(Exception):
            producer_contract(operator)
    for relative in (
        "compiler/lowering.py",
        "compiler/placement.py",
        "compiler/normalize.py",
        "materialization/local_execution.py",
        "materialization/local_stage.py",
        "materialization/dataset_publication.py",
    ):
        source = (Path(marivo.__file__).parent / "analysis" / relative).read_text()
        assert "ComparePayload" not in source.replace("FunnelComparePayload", "")
        assert "AttributePayload" not in source.replace("FunnelAttributePayload", "")
    for relative in (
        "materialization/comparison_codec.py",
        "materialization/attribution_codec.py",
        "materialization/comparison_publication.py",
        "materialization/attribution_publication.py",
        "operators/delta.py",
        "operators/attribution.py",
    ):
        assert not (Path(marivo.__file__).parent / "analysis" / relative).exists()
    assert legacy_source_migration_stage("event.compare") is None
    assert legacy_source_migration_stage("discover.driver_axes") == 8
    assert legacy_source_migration_stage("metric.compare") is None
    assert legacy_source_migration_stage("delta.attribute") is None
    assert legacy_source_migration_stage("funnel_delta.attribute") is None


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_every_r6_required_part_revokes_recovery_and_cached_continuation(
    analysis_dsl_case_factory, parquet: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from copy import deepcopy

    import duckdb
    import ibis

    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode
    from marivo.datasource.adapters import SourceSession
    from tests.shared_fixtures import export_dsl_parquet_models

    case = analysis_dsl_case_factory("j2")
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    targets = case.session.members(ms.ref.entity("sales.customer"))
    metric = ms.ref.metric("sales.order_count")
    via = ms.ref.relationship("sales.order_buyer")
    current = targets.observe(
        metric, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=via
    )
    baseline = targets.observe(
        metric, during=mv.time_scope(start="2026-07-01", end="2026-08-01"), via=via
    )
    difference = current.compare(baseline).execute()
    category = targets.read(ms.ref.dimension("sales.customer.region"))
    cohort = targets.cohort(category.value.eq("east"), rule=mv.any_instance()).execute()
    share = current.share_of(current.rollup()).execute()
    ranking = current.rank(order="descending", ties="min").execute()
    axis = ms.ref.dimension("sales.order.channel")
    endpoints = [
        targets.observe(
            metric,
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=via,
            coordinates=(axis,),
        ).rollup()
        for month in (8, 7)
    ]
    allocation = endpoints[0].compare(endpoints[1]).attribute(axes=(axis,)).execute()
    original_ratio = mv.runtime_metric.ratio(ms.ref.metric("sales.revenue"), metric, label="mean")
    mix_endpoints = [
        targets.observe(
            original_ratio,
            during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
            via=via,
            coordinates=(axis,),
        ).rollup()
        for month in (8, 7)
    ]
    component_mix = mix_endpoints[0].compare(mix_endpoints[1]).attribute(axes=(axis,)).execute()
    table = mv.table(value=ranking.values, rank=ranking.ranks).execute()
    continuations = (
        difference.where(difference.value.is_defined()),
        cohort.penetration_in(targets.execute()),
        share.where(share.value.is_defined()),
        ranking.limit(1),
        allocation.where(allocation.contribution.value.is_defined()),
        component_mix.where(component_mix.contribution.value.is_defined()),
    )
    for continuation in continuations:
        continuation.execute()
    runs_before = len(case.session.runs().items)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("damaged fixed R6 state replayed its source")

    for owner, name in (
        (duckdb, "connect"),
        (ibis.duckdb, "connect"),
        (ms, "load"),
        (SourceSession, "__init__"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    results = (difference, cohort, share, ranking, allocation, component_mix, table)
    for index, result in enumerate(results):
        descriptor = result._dataset.artifact.descriptor
        original = encode(descriptor, DESCRIPTOR)
        payload = json.loads(original)
        reference = (
            result.artifact_ref.ref
            if isinstance(result, mv.MaterializedTable)
            else result.state.artifact_ref.ref
        )

        def reject(reference: str, result, index: int) -> None:
            with pytest.raises(AnalysisError):
                case.session.artifact(reference)
            if isinstance(result, mv.MaterializedTable):
                with pytest.raises(AnalysisError):
                    result.to_pandas()
            else:
                with pytest.raises(AnalysisError):
                    result.contract()
                with pytest.raises(AnalysisError):
                    continuations[index].execute()
            assert len(case.session.runs().items) == runs_before

        def metadata(value: str, reference: str) -> None:
            with case.session._runtime.store._write() as connection:
                connection.execute(
                    "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                    (value, reference),
                )

        for position, part in enumerate(descriptor.parts):
            path = (
                case.root
                / part.local.project_relative_path
                / part.local.file_manifest[0].relative_path
            )
            contents = path.read_bytes()
            try:
                path.unlink()
                reject(reference, result, index)
                path.write_bytes(b"damaged required R6 state")
                reject(reference, result, index)
            finally:
                path.write_bytes(contents)
            for damage in ("receipt", "version", "exchange"):
                changed = deepcopy(payload)
                receipt = changed["parts"][position]
                if damage == "receipt":
                    receipt["input_binding"] = "foreign-input-binding"
                elif damage == "version":
                    receipt["method_state_version"] += 1
                else:
                    receipt["local"] = changed["primary_receipt"]["local"]
                try:
                    metadata(json.dumps(changed), reference)
                    reject(reference, result, index)
                finally:
                    metadata(original, reference)
        recovered = case.session.artifact(reference)
        assert recovered.to_pandas().equals(result.to_pandas())
    assert len(case.session.runs().items) == runs_before
