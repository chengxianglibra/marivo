"""Column-specific static origins survive composition and source-offline recovery."""

from __future__ import annotations

import json
import operator
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.lineage import ColumnLineage, SemanticObjectIdentity, union
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory, catalog_value
from tests.support.paths import PROJECT_ROOT


def paths(origins: tuple[SemanticObjectIdentity, ...]) -> set[str]:
    return {origin.path for origin in origins}


def test_source_columns_context_and_fixed_lifecycle(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    revenue = case.catalog.get("metric:sales.revenue")
    count = case.catalog.get("metric:sales.order_count")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    frame = session.observe({"revenue": revenue, "orders": count}, by=(region,))
    rev = frame.lineage.values["revenue"]
    assert {"sales.revenue", "sales.order.amount", "sales.order", "warehouse"} <= paths(
        rev.value_origins
    )
    assert "sales.order_count" not in paths(rev.value_origins)
    assert "sales.customer.region" in paths(rev.context_origins)
    assert "sales.order.ordered_at" in paths(rev.context_origins)
    assert "sales.order_buyer" in paths(rev.context_origins)
    members = session.members(case.catalog.get("entity:sales.customer"))
    customer_id = case.catalog.get("dimension:sales.customer.customer_id")
    read = session.read(members, {"region": region, "id": customer_id})
    region_lineage = read.lineage.values["region"]
    assert "sales.customer.customer_id" not in paths(
        region_lineage.value_origins + region_lineage.context_origins
    )
    selected = frame.where(frame["orders"] > 0)
    ranked = frame.top(1, by=frame["orders"])
    for limited in (selected, ranked):
        axis = limited.lineage.axes[limited.axes[0].token]
        assert "sales.order_count" in paths(axis.context_origins)
        assert "sales.order_count" not in paths(axis.value_origins)
    projected = selected.select({"renamed": selected["revenue"]})
    assert projected.lineage.axes == selected.lineage.axes
    lineage = projected.lineage.values["renamed"]
    assert lineage.value_origins == rev.value_origins
    assert "sales.order_count" in paths(lineage.context_origins)
    reduced = projected.reduce({"total": projected["renamed"].sum(), "rows": mv.row_count()})
    assert reduced.lineage.values["rows"].value_origins == ()
    assert "sales.order_count" in paths(reduced.lineage.values["rows"].context_origins)
    fixed = reduced.compute()
    saved = fixed.persist()
    assert fixed.lineage == saved.lineage == reduced.lineage
    reopened = session.persisted_frame(saved.artifact_id)
    assert reopened.lineage == saved.lineage

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("lineage must not consume rows or method files")

    from marivo.analysis.materialization.graph_reading import FrameReader

    monkeypatch.setattr(FrameReader, "data", forbidden)
    monkeypatch.setattr(FrameReader, "method_facts", forbidden)
    assert reopened.lineage == saved.lineage
    assert (
        reopened.select({"copy": reopened["total"]}).lineage.values["copy"]
        == saved.lineage.values["total"]
    )
    with pytest.raises(TypeError):
        operator.setitem(reopened.lineage.values, "bad", lineage)
    with pytest.raises(FrozenInstanceError):
        lineage.value_origins = ()


def test_named_measures_survive_expansion_without_changed_computation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    model = case.root / "models" / "semantic" / "sales" / "models.py"
    if not model.exists():
        model = next((case.root / "models").rglob("models.py"))
    model.write_text(
        model.read_text()
        + """
@ms.measure()
def named_amount():
    return amount_value

@ms.measure()
def nested_amount():
    return named_amount * 2

@ms.metric()
def named_total():
    return nested_amount.sum(time=time_value)
"""
    )
    catalog = ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("named-lineage", timezone="UTC")
    metric = catalog.get("metric:sales.named_total")
    result = session.observe({"named": metric})
    captured_origins = result.lineage.values["named"].value_origins
    assert len([o for o in captured_origins if o.path == "sales.order.nested_amount"]) == 1
    origins = paths(captured_origins)
    assert {
        "sales.named_total",
        "sales.order.named_amount",
        "sales.order.nested_amount",
        "sales.order.amount",
    } <= origins
    from marivo._definition.graph import computation_fingerprint
    from marivo.semantic._deferred_semantics import consume_metric

    direct = catalog.get("measure:sales.order.amount")
    derived = catalog.get("measure:sales.order.named_amount")
    assert computation_fingerprint(consume_metric(direct.sum()).node) == computation_fingerprint(
        consume_metric(derived.sum()).node
    )


def test_named_predicate_origins_keep_their_usage_roles(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    model = next((case.root / "models").rglob("models.py"))
    model.write_text(
        model.read_text()
        + """
@ms.measure()
def named_amount():
    return amount_value * 2

@ms.measure()
def threshold():
    return named_amount + amount_value

@ms.metric()
def positive_count():
    return orders_value.count(time=time_value).where(threshold > 0)
"""
    )
    catalog = ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("predicate-lineage", timezone="UTC")
    amount = catalog.get("measure:sales.order.amount")
    named = catalog.get("measure:sales.order.threshold")
    count = catalog.get("metric:sales.order_count")
    published = catalog.get("metric:sales.positive_count")
    aliases = {"sales.order.named_amount", "sales.order.threshold"}
    common = session.observe({"n": count}, where=named > 0)
    for frame in (
        common,
        session.observe({"n": count.where(named > 0)}),
        session.observe({"n": published}),
        session.observe({"n": amount.sum(where=named > 0)}),
    ):
        origins = frame.lineage.values["n"]
        assert aliases <= paths(origins.context_origins)
        assert aliases.isdisjoint(paths(origins.value_origins))
    both = session.observe({"n": named.sum(where=named > 0)}).lineage.values["n"]
    assert aliases <= paths(both.value_origins)
    assert aliases <= paths(both.context_origins)
    saved = common.persist()
    assert session.persisted_frame(saved.artifact_id).lineage == common.lineage

    from marivo._definition.graph import computation_fingerprint, direct_refs
    from marivo.semantic._deferred_semantics import consume_metric, consume_predicate

    direct = (amount * 2 + amount).sum()
    named_node = consume_metric(named.sum()).node
    assert computation_fingerprint(named_node) == computation_fingerprint(
        consume_metric(direct).node
    )
    assert direct_refs(named_node) == direct_refs(consume_metric(direct).node)
    assert computation_fingerprint(consume_predicate(named > 0).node) == computation_fingerprint(
        consume_predicate(amount * 2 + amount > 0).node
    )


@pytest.mark.parametrize("method", ("mean", "weighted", "ratio"))
def test_attribute_components_keep_separate_value_and_validity_origins(
    analysis_dsl_case_factory: DslCaseFactory,
    method: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    model = next((case.root / "models").rglob("models.py"))
    model.write_text(
        model.read_text()
        + """
weight = ms.measure_column(name='weight', entity=orders, column='amount',
                           additivity=ms.additive_all(), unit='CNY')
@ms.measure()
def named_weight():
    return weight

@ms.metric()
def weight_total():
    return named_weight.sum(time=time_value)
"""
    )
    catalog = ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("component-lineage", timezone="UTC")
    amount = catalog.get("measure:sales.order.amount")
    weight = catalog.get("measure:sales.order.named_weight")
    channel = catalog.get("dimension:sales.order.channel")
    metric = (
        amount.mean()
        if method == "mean"
        else amount.weighted_mean(weight=weight)
        if method == "weighted"
        else catalog.get("metric:sales.revenue") / catalog.get("metric:sales.weight_total")
    )
    report = session.observe({"q": metric}).attribute(by=(channel,))
    numerator = report.lineage.values["component.value.numerator.q"]
    denominator = report.lineage.values["component.value.denominator.q"]
    measures = {"sales.order.amount", "sales.order.weight", "sales.order.named_weight"}
    assert paths(numerator.value_origins) & measures == (
        measures if method == "weighted" else {"sales.order.amount"}
    )
    assert paths(denominator.value_origins) & measures == (
        set() if method == "mean" else {"sales.order.weight", "sales.order.named_weight"}
    )
    if method != "ratio":
        assert "sales.order.amount" in paths(denominator.context_origins)
    else:
        assert "sales.revenue" in paths(numerator.value_origins)
        assert "sales.weight_total" not in paths(numerator.value_origins)
        assert "sales.weight_total" in paths(denominator.value_origins)
        assert "sales.revenue" not in paths(denominator.value_origins)
    if method == "weighted":
        valid = report.lineage.values["component.value.valid_count.q"]
        assert valid.value_origins == ()
        assert measures <= paths(valid.context_origins)
    saved = report.persist()
    assert session.persisted_frame(saved.artifact_id).lineage == report.lineage


def test_compare_attribute_and_key_bridge_keep_output_roles(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    metric = case.catalog.get("metric:sales.revenue")
    region = catalog_value(case.catalog, "sales.customer.region", kind="field")
    channel = catalog_value(case.catalog, "sales.order.channel", kind="field")
    left = session.observe({"revenue": metric})
    right = session.observe({"revenue": metric * 2})
    compared = left.compare(right)
    assert (
        compared.lineage.values["reference.absolute.revenue"].value_origins
        == left.lineage.values["revenue"].value_origins
    )
    assert set(compared.lineage.presence) == set(compared.presence_names)
    report = left.attribute(by=(region,))
    assert (
        report.lineage.values["observed_total.revenue"].value_origins
        == left.lineage.values["revenue"].value_origins
    )
    assert "sales.customer.region" in paths(
        report.lineage.values["contribution.revenue"].context_origins
    )
    assert report.compute().lineage == report.lineage
    groups = session.observe({"revenue": metric}, by=(region,))
    selected = groups.where(groups["revenue"] > 0)
    keys = session.population_from(selected, on=((selected.axes[0], region),))
    bridged = session.observe(
        {"twice": metric * 2}, by=(region, channel), where=mv.in_population(keys)
    )
    assert "sales.revenue" in paths(bridged.lineage.values["twice"].context_origins)
    assert bridged.compute().lineage == bridged.lineage


def test_cold_lineage_has_no_model_or_source_dependency(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    saved = case.session.observe({"revenue": case.catalog.get("metric:sales.revenue")}).persist()
    (case.root / "models").rename(case.root / "hidden_models")
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    script = """
import sys
import marivo.analysis as mv
session = mv.session.resume(sys.argv[1])
frame = session.persisted_frame(sys.argv[2])
assert 'sales.revenue' in {o.path for o in frame.lineage.values['revenue'].value_origins}
assert frame.select({'copy': frame['revenue']}).lineage.values['copy'] == frame.lineage.values['revenue']
print('cold lineage ok')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, saved.session_id, saved.artifact_id],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold lineage ok" in result.stdout


def test_identity_versions_and_literal_counts_are_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = SemanticObjectIdentity("metric", "sales.revenue", "v1")
    second = replace(first, definition_id="v2")
    assert union((first,), (second,)) == (first, second)
    with pytest.raises(AnalysisError):
        ColumnLineage((second, first))
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("literal-lineage")
    frame = session.values(columns={"x": None}, rows=(((), {"x": 1}),))
    assert frame.lineage.values["x"] == ColumnLineage()
    assert frame.reduce({"n": mv.row_count()}).lineage.values["n"] == ColumnLineage()


@pytest.mark.parametrize("damage", ("missing", "null", "wrong_column"))
def test_saved_lineage_cannot_be_missing_or_mismatched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    from marivo.analysis.core.artifact_data import ArtifactManifest
    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.materialization.graph_reading import open_manifest
    from marivo.analysis.materialization.terminal_protocol import decode, encode

    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("lineage-integrity")
    saved = session.values(columns={"x": None}, rows=(((), {"x": 7}),)).persist()
    manifest = open_manifest(session._runtime.local.store, saved.session_id, saved.artifact_id)
    payload = json.loads(encode(manifest))
    fields = payload["value"]["fields"]["metadata"]["fields"]
    if damage == "missing":
        del fields["lineage"]
    elif damage == "null":
        fields["lineage"] = None
    else:
        fields["lineage"]["fields"]["_values"][0][0] = "unrelated"
    with pytest.raises(IntegrityError):
        decode(json.dumps(payload), ArtifactManifest)
