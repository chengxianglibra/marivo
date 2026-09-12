"""Correlation failures preserve atomic authority and source-private identity."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import LocalPolicy
from marivo.analysis.materialization.targets import LocalTarget
from marivo.refs import ref
from tests.lazy_local_fixtures import pandas_methods, setup_local
from tests.lazy_materialization_crash_worker import snapshot

pytestmark = pytest.mark.runtime


def unchanged_bundle(before: dict[str, object], after: dict[str, object]) -> None:
    a, b = before["tables"], after["tables"]
    assert isinstance(a, dict) and isinstance(b, dict)
    for name in ("dataset_artifacts", "dataset_evidence", "findings", "action_resource_journal"):
        assert a[name] == b[name]


@pytest.mark.parametrize(
    "policy",
    [
        replace(LocalPolicy(), max_input_rows=1),
        replace(LocalPolicy(), max_method_rows=1),
        replace(LocalPolicy(), max_input_bytes=1),
        replace(LocalPolicy(), max_intermediate_bytes=1),
        replace(LocalPolicy(), max_output_bytes=1),
        replace(LocalPolicy(), deadline_seconds=0.001),
    ],
)
def test_complete_local_guards_publish_nothing(tmp_path: Path, policy: LocalPolicy) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    runtime.local_policy = policy
    logical = sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]
    ).correlate(method="kendall")
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        logical.execute()
    unchanged_bundle(before, snapshot(runtime))
    assert runtime.last_run_ref is not None
    terminal = runtime.store.run(runtime.last_run_ref)
    assert (
        terminal is not None
        and terminal.lifecycle == "failed"
        and terminal.output_artifact_ref is None
    )


@pytest.mark.parametrize(
    "point",
    [
        "backend_compile",
        "transfer",
        "after_rename",
        "quality",
        "evidence",
        "insert_findings",
        "before_commit",
    ],
)
def test_fault_never_publishes_partial_association(tmp_path: Path, point: str) -> None:
    def fault(event: str) -> None:
        if event == point:
            raise RuntimeError("association-private-canary")

    runtime, sources, _ = setup_local(tmp_path, event=fault)
    logical = sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]
    ).correlate()
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        logical.execute()
    assert "association-private-canary" not in str(error.value)
    unchanged_bundle(before, snapshot(runtime))
    assert runtime.statistics.worker_pid is None


def test_raw_entity_checkpoint_rejected_before_admission(tmp_path: Path) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    metric = sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]
    ).execute()
    before = snapshot(runtime)
    for method in ("pearson", "spearman", "kendall"):
        with (
            pandas_methods("metric.correlate"),
            pytest.raises(DatasetCompilationError, match="source-required"),
        ):
            metric.correlate(method=method).execute()
        assert before == snapshot(runtime)


def test_selected_engine_receipt_mutation_rolls_back(tmp_path: Path) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    runtime.target = LocalTarget()
    metric = sources.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]
    ).execute()
    record = runtime.store.artifact(metric.state.artifact_ref.ref)
    assert record is not None
    from marivo.analysis.materialization.contracts import LocalReceipt

    receipt = record.descriptor.storage_receipt
    assert isinstance(receipt, LocalReceipt)

    def mutate(event: str) -> None:
        if event == "after_rename":
            path = tmp_path / receipt.project_relative_path / "data.parquet"
            path.chmod(0o600)
            with path.open("ab") as stream:
                stream.write(b"private-correlation-receipt-mutation")

    runtime.target = LocalTarget()
    runtime._hook = mutate
    before = snapshot(runtime)
    with pytest.raises(MaterializationError, match="backing size changed"):
        metric.correlate(method="kendall").execute()
    unchanged_bundle(before, snapshot(runtime))


@pytest.mark.parametrize("point", ["transfer", "insert_findings"])
def test_cancellation_does_not_publish_authority(tmp_path: Path, point: str) -> None:
    def cancel(event: str) -> None:
        if event == point:
            raise KeyboardInterrupt()

    runtime, sources, _ = setup_local(tmp_path, event=cancel)
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        sources.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]).correlate(
            method="kendall"
        ).execute()
    unchanged_bundle(before, snapshot(runtime))
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"


@pytest.mark.parametrize("fault", ["missing", "schema", "counts"])
def test_corrupted_pair_boundary_fails_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from collections.abc import Iterator

    import ibis.expr.types as ir
    import pyarrow as pa
    from ibis.backends import BaseBackend

    from marivo.analysis.materialization.admission import DatasetRuntime

    original = DatasetRuntime._batches

    def corrupt(
        self: DatasetRuntime, backend: BaseBackend, expression: ir.Table, batch_rows: int
    ) -> Iterator[pa.RecordBatch]:
        for batch in original(self, backend, expression, batch_rows):
            assert "value_a" in batch.schema.names
            if fault == "missing":
                yield batch.slice(1)
            elif fault == "schema":
                yield batch.drop_columns(["value_a"])
            else:
                index = batch.schema.get_field_index("complete_pair_count")
                yield batch.set_column(
                    index,
                    batch.schema.field(index),
                    pa.array([0] * batch.num_rows, type=pa.int64()),
                )

    monkeypatch.setattr(DatasetRuntime, "_batches", corrupt)
    runtime, sources, _ = setup_local(tmp_path)
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        sources.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]).correlate(
            method="kendall"
        ).execute()
    if fault == "missing":
        assert "pair" in str(error.value) and "coalition" not in str(error.value)
    unchanged_bundle(before, snapshot(runtime))


def test_retained_evidence_and_finding_contradictions_are_rejected(tmp_path: Path) -> None:
    from marivo.analysis.evidence._dataset_codec import finding_identity
    from marivo.analysis.evidence._dataset_reads import _validate
    from marivo.analysis.evidence._dataset_types import AssociationFindingValueV1
    from marivo.analysis.materialization.association_publication import finding_registration
    from marivo.analysis.materialization.contracts import (
        canonical_json,
        decode_descriptor,
        descriptor_payload,
    )
    from marivo.analysis.materialization.errors import IntegrityError

    runtime, sources, _ = setup_local(tmp_path)
    result = (
        sources.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
        .correlate()
        .execute()
    )
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    finding = result.findings().items[0]
    assert isinstance(finding.value, AssociationFindingValueV1)
    changed = replace(finding, value=replace(finding.value, method="spearman"))
    changed = replace(changed, finding_id=finding_identity(changed))
    with pytest.raises(IntegrityError, match="method contradicts"):
        _validate(changed, record, finding_registration(record.descriptor))
    payload = descriptor_payload(record.descriptor)
    summary = payload["association_evidence"]
    assert isinstance(summary, dict)
    summary["original_candidate_count"] = 2
    with pytest.raises(IntegrityError, match="Evidence counts"):
        decode_descriptor(canonical_json(payload))


@pytest.mark.parametrize("damage", ["pair", "approximation", "rule"])
def test_cold_association_disclosure_rejects_forged_bindings(tmp_path: Path, damage: str) -> None:
    import json

    from marivo.analysis.materialization.contracts import (
        canonical_json,
        decode_descriptor,
        descriptor_payload,
    )
    from marivo.analysis.materialization.errors import IntegrityError

    runtime, sources, _ = setup_local(tmp_path)
    result = (
        sources.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
        .correlate()
        .execute()
    )
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    payload = json.loads(canonical_json(descriptor_payload(record.descriptor)))
    summary = payload["association_evidence"]
    if damage == "rule":
        summary["selection_rule_id"] = "unknown@v1"
    else:
        binding = summary["pair_approximation_bindings"][0]
        binding["metric_key_a" if damage == "pair" else "approximation_a"] = (
            "metric:sales.unknown" if damage == "pair" else "sampled_population"
        )
    with pytest.raises(IntegrityError, match="Association"):
        decode_descriptor(canonical_json(payload))
