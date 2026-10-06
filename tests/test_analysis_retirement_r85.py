"""Independent reverse guards for physically retired statistical execution."""

import importlib
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo._help.model import MarivoHelpTargetError
from marivo._help.render import help as help_api
from marivo.analysis._public import __all__ as analysis_exports
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, decode
from tests.shared_fixtures import DslCaseFactory

ROOT = Path(__file__).resolve().parents[1]
RETIRED_MODULES = (
    "operators.discovery",
    "operators.candidate_contracts",
    "operators.candidate_dataset",
    "operators.candidate_values",
    "operators.driver_axes",
    "operators.driver_contracts",
    "operators.driver_expansion",
    "operators.driver_values",
    "operators.association",
    "operators.association_contracts",
    "operators.association_values",
    "operators.correlate",
    "operators.forecast",
    "operators.forecast_contracts",
    "operators.forecast_dataset",
    "operators.forecast_values",
    "compiler.correlation",
    "compiler.driver_candidate",
    "compiler.driver_numeric",
    "compiler.entity_candidate",
    "materialization.candidate_codec",
    "materialization.candidate_publication",
    "materialization.association_codec",
    "materialization.association_publication",
    "materialization.forecast_codec",
    "materialization.forecast_publication",
    "evidence._finding_registry",
)
RETIRED_EXPORTS = (
    "LogicalCandidateDataset",
    "MaterializedCandidateDataset",
    "LogicalAssociationDataset",
    "MaterializedAssociationDataset",
    "LogicalForecastDataset",
    "MaterializedForecastDataset",
)
RETIRED_TARGETS = (
    "discovery",
    "methods.discovery",
    "discovery.point_anomalies",
    "discovery.interesting_windows",
    "discovery.period_shifts",
    "discovery.entity_outliers",
    "discovery.driver_axes",
    "metric_dataset.correlate",
    "metric_dataset.forecast",
    *RETIRED_EXPORTS,
)


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_exclusive_module_cannot_import_or_execute(module: str) -> None:
    assert not (ROOT / "marivo/analysis" / (module.replace(".", "/") + ".py")).exists()
    with pytest.raises(ModuleNotFoundError) as raised:
        importlib.import_module("marivo.analysis." + module)
    assert raised.value.name == "marivo.analysis." + module


def test_public_and_hidden_old_entries_are_absent() -> None:
    for name in RETIRED_EXPORTS:
        assert name not in analysis_exports and not hasattr(mv, name)
    for name in ("execute_candidate", "execute_association", "execute_forecast"):
        assert not hasattr(DatasetRuntime, name)
    assert not (ROOT / "marivo/analysis/materialization/duckdb_execution.py").exists()
    assert not (ROOT / "marivo/analysis/operators/registry.py").exists()
    for path in (ROOT / "marivo/analysis").rglob("*.py"):
        text = path.read_text()
        assert not any(
            token in text
            for token in (
                "CREATE MACRO",
                "install_numeric(",
                "DriverCandidateDefinition",
                "CandidateSemantics",
                "CorrelatePayload",
                "ForecastPayload",
            )
        )


@pytest.mark.parametrize("target", RETIRED_TARGETS)
def test_old_help_routes_reject(target: str) -> None:
    with pytest.raises(MarivoHelpTargetError):
        help_api("analysis." + target)


@pytest.mark.parametrize("kind", ("candidate", "association", "forecast"))
def test_old_family_and_payload_do_not_decode(kind: str) -> None:
    with pytest.raises(AnalysisError):
        decode(canonical_json({"kind": kind}), DESCRIPTOR)
    for evidence in (None, {"kind": kind}):
        with pytest.raises(AnalysisError):
            decode(canonical_json({kind + "_evidence": evidence}), DESCRIPTOR)


@pytest.mark.parametrize(
    "producer",
    (
        "discover.point_anomalies",
        "discover.interesting_windows",
        "discover.period_shifts",
        "discover.entity_outliers",
        "discover.driver_axes",
        "discover.driver_axes_expanded",
        "candidate.where",
        "candidate.rank",
        "candidate.limit",
        "metric.correlate",
        "association.where",
        "association.rank",
        "association.limit",
        "metric.forecast",
        "forecast.where",
        "forecast.rank",
        "forecast.limit",
    ),
)
def test_old_producer_contract_cannot_authorize_publication(producer: str) -> None:
    with pytest.raises(AnalysisError):
        decode(canonical_json({"producer": producer}), DESCRIPTOR)


@pytest.mark.runtime
def test_public_and_private_artifact_reads_reject_old_envelopes_without_replay(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic as ms
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    current = (
        case.session.members(ms.ref.entity("sales.order"))
        .read(ms.ref.measure("sales.order.amount"))
        .execute()
    )
    store, ref = case.session._runtime.store, current.state.artifact_ref
    with store._read() as connection:
        saved = connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?", (ref.ref,)
        ).fetchone()[0]
    before = case.session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("old envelope recovery tried to replay the source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    try:
        for kind in ("candidate", "association", "forecast"):
            payload = {**__import__("json").loads(saved), kind + "_evidence": {"kind": kind}}
            with store._write() as connection:
                connection.execute(
                    "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                    (canonical_json(payload), ref.ref),
                )
            for read in (case.session.artifact,):
                with pytest.raises(AnalysisError):
                    read(ref)
                assert case.session.runs().items == before
    finally:
        with store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (saved, ref.ref),
            )
    restored = case.session.artifact(ref)
    assert isinstance(restored, mv.MaterializedNumericRelation)
    assert restored.to_pandas().equals(current.to_pandas())


def test_forecast_model_policies_keep_their_current_owner() -> None:
    assert not (ROOT / "marivo/analysis/compiler/distribution.py").exists()
    from marivo.analysis.forecast_models import drift, naive, periods, seasonal_naive

    assert (
        mv.naive is naive
        and mv.drift is drift
        and mv.periods is periods
        and mv.seasonal_naive is seasonal_naive
    )
