"""Public quantile selection preserves method identity through Session and cold reads."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import marivo
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.observation.distribution_contracts import distribution_part_authorities
from marivo.semantic._quantile import QuantileMethod
from marivo.semantic.errors import SemanticLoadError


@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
def test_public_quantile_selection_is_pure_frozen_and_discoverable(
    monkeypatch, capsys, method: QuantileMethod
):
    import marivo.telemetry as telemetry

    reference = ms.ref.metric("sales.p95_amount")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("quantile selection attempted project I/O")

    with monkeypatch.context() as guarded:
        guarded.setattr(telemetry, "resolve_project_root", forbidden)
        selected = ms.quantile_metric(reference, method=method)
    assert type(selected) is ms.QuantileMetricInput
    assert selected.metric is reference
    assert selected.method == method
    with pytest.raises(FrozenInstanceError):
        selected.method = method
    assert len(repr(selected).splitlines()) == 1
    assert len(repr(selected)) < 256
    selected.show()
    assert method in capsys.readouterr().out
    for target in (
        "semantic.quantile_metric",
        ms.quantile_metric,
        ms.QuantileMetricInput,
        selected,
        ms.QuantileMetricInput.render,
        ms.QuantileMetricInput.show,
    ):
        marivo.help(target)
        text = capsys.readouterr().out
        assert "quantile_metric" in text


@pytest.mark.parametrize("method", ["automatic", "duckdb_tdigest", None])
def test_public_quantile_rejects_unregistered_method_with_resolvable_repair(method, capsys):
    with pytest.raises(SemanticLoadError) as caught:
        ms.quantile_metric(ms.ref.metric("sales.p95_amount"), method=method)
    error = caught.value
    assert error.kind == "invalid_quantile_method"
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "quantile_metric"
    marivo.help("semantic.quantile_metric")
    assert "duckdb_tdigest@v1" in capsys.readouterr().out


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["linear_interpolation@v1", "duckdb_tdigest@v1"])
def test_public_quantile_executes_and_cold_projection_keeps_method(
    authoring_evidence_project: Path, monkeypatch, method: QuantileMethod
):
    monkeypatch.chdir(authoring_evidence_project)
    model = authoring_evidence_project / "models" / "semantic" / "sales" / "models.py"
    model.write_text(
        model.read_text()
        + "\nmedian_amount = ms.aggregate(name='median_amount', measure=amount, agg='median')\n"
    )
    session = mv.session.get_or_create("quantile")
    reference = ms.ref.metric("sales.median_amount")
    logical = session.observe(ms.quantile_metric(reference, method=method)).aggregate()
    authority = distribution_part_authorities(logical.row_contract)[0][1]
    assert authority.distribution is not None
    assert authority.distribution.quantile.method == method
    assert authority.distribution.quantile.q == 0.5
    assert session.runs().items == ()
    output = logical.execute()
    assert output.to_pandas()["median_amount"].tolist() == [187.875]
    (authoring_evidence_project / "warehouse.duckdb").rename(
        authoring_evidence_project / "warehouse.offline"
    )
    cold = mv.session.resume(session.id, by="id")
    loaded = cold.artifact(output.state.artifact_ref)
    assert not cold._runtime.statistics.statements
    from marivo.analysis.materialization import admission

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cold projection attempted origin access")

    monkeypatch.setattr(admission, "_build_backend_from_effective", forbidden)
    monkeypatch.setattr(admission, "_effective_kwargs", forbidden)
    projected = loaded.metric(loaded.fields.get("median_amount")).execute()
    assert projected.to_pandas()["median_amount"].tolist() == [187.875]
    assert (
        distribution_part_authorities(projected.row_contract)[0][1].distribution
        == authority.distribution
    )
    assert cold._runtime.statistics.source_fences == 0
