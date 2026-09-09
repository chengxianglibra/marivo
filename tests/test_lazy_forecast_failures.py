"""Forecast guards and faults never publish incomplete model authority."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.local import LocalPolicy
from marivo.analysis.operators.forecast_contracts import periods
from tests.lazy_forecast_fixtures import history, setup_forecast
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
        replace(LocalPolicy(), max_output_rows=1),
        replace(LocalPolicy(), max_output_bytes=1),
        replace(LocalPolicy(), max_intermediate_bytes=1),
        replace(LocalPolicy(), deadline_seconds=0.001),
    ],
)
def test_guards_publish_nothing(tmp_path: Path, policy: LocalPolicy) -> None:
    runtime, source, _ = setup_forecast(tmp_path)
    runtime.local_policy = policy
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        history(source).forecast(horizon=periods(4)).execute()
    unchanged_bundle(before, snapshot(runtime))
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None


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
def test_fault_rolls_back_whole_horizon(tmp_path: Path, point: str) -> None:
    runtime, source, _ = setup_forecast(tmp_path)

    def fault(event: str) -> None:
        if event == point:
            raise RuntimeError("forecast-private-canary")

    runtime._hook = fault
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        history(source).forecast(horizon=periods(4)).execute()
    assert "forecast-private-canary" not in str(error.value)
    unchanged_bundle(before, snapshot(runtime))
    if point == "backend_compile":
        assert runtime.statistics.worker_pid is None


@pytest.mark.parametrize("point", ["transfer", "insert_findings"])
def test_cancellation_rolls_back(tmp_path: Path, point: str) -> None:
    runtime, source, _ = setup_forecast(tmp_path)

    def cancel(event: str) -> None:
        if event == point:
            raise KeyboardInterrupt()

    runtime._hook = cancel
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        history(source).forecast(horizon=periods(4)).execute()
    unchanged_bundle(before, snapshot(runtime))


@pytest.mark.parametrize("values", [(1.0,), (1.0, float("nan"), 3.0), (1e308, -1e308, 1e308)])
def test_invalid_history_never_publishes(tmp_path: Path, values: tuple[float, ...]) -> None:
    runtime, source, _ = setup_forecast(tmp_path, values)
    before = snapshot(runtime)
    with pytest.raises(MaterializationError):
        history(source).forecast(horizon=periods(4)).execute()
    unchanged_bundle(before, snapshot(runtime))


def test_cold_forecast_rejects_corrupt_training_and_finding_claims(tmp_path: Path) -> None:
    from copy import deepcopy

    from marivo.analysis.evidence._dataset_codec import finding_identity
    from marivo.analysis.evidence._dataset_reads import _validate
    from marivo.analysis.evidence._dataset_types import ForecastPointFindingValueV1
    from marivo.analysis.materialization.contracts import (
        canonical_json,
        decode_descriptor,
        descriptor_payload,
    )
    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.materialization.forecast_publication import (
        finding_registration,
        forecast_key,
    )

    runtime, source, _ = setup_forecast(tmp_path)
    result = history(source).forecast(horizon=periods(4)).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    finding = result.findings().items[0]
    assert isinstance(finding.value, ForecastPointFindingValueV1)
    for value in (
        replace(finding.value, model="drift@v1"),
        replace(finding.value, training_row_count=40),
        replace(finding.value, horizon_ordinal=4),
        replace(finding.value, interval_level=0.9),
        replace(
            finding.value,
            interval_lower=finding.value.forecast_value,
            interval_upper=finding.value.forecast_value,
        ),
    ):
        changed = replace(finding, value=value)
        changed = replace(changed, canonical_item_key=forecast_key(changed))
        changed = replace(changed, finding_id=finding_identity(changed))
        with pytest.raises(IntegrityError):
            _validate(changed, record, finding_registration(record.descriptor))
    original = descriptor_payload(record.descriptor)
    from marivo.analysis.operators.errors import ForecastError

    for interval_level in (0.0, 1.0, -0.1, 1.1):
        payload = deepcopy(original)
        row = payload["row_contract"]
        assert isinstance(row, dict)
        semantics = row["family_semantics"]
        assert isinstance(semantics, dict)
        semantics["interval_level"] = interval_level
        # The family validator runs before fingerprint and publication-proof checks.
        with pytest.raises(ForecastError, match="invalid retained invocation"):
            decode_descriptor(canonical_json(payload))
    for key, damaged_value in (
        ("residual_df", 99),
        ("training_row_count", 2**62),
        ("future_coordinates", ("2026-03-01T00:00:00",)),
        ("zero_residual_series_count", 1),
        ("variance_range", (0.0, 0.0)),
    ):
        payload = deepcopy(original)
        evidence = payload["forecast_evidence"]
        assert isinstance(evidence, dict)
        training = evidence["training"]
        assert isinstance(training, dict)
        training[key] = damaged_value
        with pytest.raises(IntegrityError):
            decode_descriptor(canonical_json(payload))


def test_native_object_denial_uses_real_store_and_no_model_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager
    from typing import TYPE_CHECKING

    from botocore.stub import Stubber

    from marivo.analysis.materialization import object_storage
    from marivo.analysis.materialization.targets import ObjectTarget, S3Access

    if TYPE_CHECKING:
        from mypy_boto3_s3 import S3Client
    runtime, source, _ = setup_forecast(tmp_path)
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "private-key", "private-secret")
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    base_client = object_storage.client

    @contextmanager
    def denied(binding: S3Access) -> Iterator[S3Client]:
        with base_client(binding) as client, Stubber(client) as stub:
            stub.add_client_error(
                "get_bucket_versioning",
                service_error_code="AccessDenied",
                service_message="private-canary",
                http_status_code=403,
                expected_params={"Bucket": "bucket"},
            )
            yield client
            stub.assert_no_pending_responses()

    monkeypatch.setattr(object_storage, "client", denied)
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        history(source).forecast(horizon=periods(4)).execute()
    assert error.value.stage == "storage_selection" and "private-canary" not in str(error.value)
    unchanged_bundle(before, snapshot(runtime))
    assert runtime.statistics.worker_pid is None and runtime.statistics.primary_queries == 0
