"""Closed Event comparison admission, field contracts and aggregate barriers."""

from __future__ import annotations

from dataclasses import replace

import pytest

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.domains.contracts import EventJourneySemantics, encode_journey_semantics
from marivo.analysis.domains.event_attribution import FunnelAttributionSemantics
from marivo.analysis.domains.event_comparison import FunnelDeltaSemantics, compatible
from marivo.analysis.funnel import funnel_loss_rate
from marivo.analysis.materialization.contracts import _semantics, _semantics_payload
from marivo.analysis.observation.predicates import eq
from marivo.refs import ref
from tests.lazy_event_fixtures import make_event_sources
from tests.lazy_event_runtime_fixtures import journey


@pytest.mark.parametrize(
    "field,value",
    [
        ("cohort_end", "2026-02-04T00:00:00+00:00"),
        ("completion_through", "2026-02-04T00:00:00+00:00"),
        ("cohort_start", "2026-02-01T00:00:00+01:00"),
        ("population_definition", "ds_" + "0" * 64),
        ("subject_entity_ref", "sales.different"),
        ("sampling_authority", "changed"),
    ],
)
def test_incompatible_event_authority_is_rejected(field: str, value: str) -> None:
    f = journey(make_event_sources()).funnel()
    original = f.row_contract.family_semantics
    from marivo.analysis.domains.contracts import EventFunnelSemantics

    assert isinstance(original, EventFunnelSemantics)
    base = original.journey
    changed = replace(
        base,
        _token=d._CORE_TOKEN,
        cohort_end=value if field == "cohort_end" else base.cohort_end,
        cohort_start=value if field == "cohort_start" else base.cohort_start,
        completion_through=value if field == "completion_through" else base.completion_through,
        population_definition=value
        if field == "population_definition"
        else base.population_definition,
        subject_entity_ref=value if field == "subject_entity_ref" else base.subject_entity_ref,
        sampling_authority=value if field == "sampling_authority" else base.sampling_authority,
    )
    other = replace(original, _token=d._CORE_TOKEN, journey_json=encode_journey_semantics(changed))
    with pytest.raises(DatasetConstructionError):
        compatible(original, other)


def test_equal_shifted_cohorts_are_compatible() -> None:
    from marivo.analysis.domains.contracts import EventFunnelSemantics

    f = journey(make_event_sources()).funnel()
    original = f.row_contract.family_semantics
    assert isinstance(original, EventFunnelSemantics)
    other = replace(
        original.journey,
        _token=d._CORE_TOKEN,
        cohort_start="2026-03-01T00:00:00+00:00",
        cohort_end="2026-03-02T00:00:00+00:00",
        completion_through="2026-03-03T00:00:00+00:00",
    )
    compatible(
        original,
        replace(original, _token=d._CORE_TOKEN, journey_json=encode_journey_semantics(other)),
    )


def test_shared_families_expose_only_event_continuations() -> None:
    j = journey(make_event_sources())
    meaning = j.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    delta = j.funnel().compare(j.funnel())
    assert isinstance(delta.row_contract.family_semantics, FunnelDeltaSemantics)
    # The kernel admits the shared consumer; public Help specializes its call
    # to the Funnel leaf rather than exposing the generic Metric contract.
    assert any(c.id == "delta.attribute" for c in delta._registry.consumers_for(delta))
    assert "delta.rank" not in delta.contract().render()
    with pytest.raises(DatasetConstructionError):
        delta.rank(delta.fields.get("loss_rate_delta"))
    result = delta.attribute(
        target=funnel_loss_rate(step=meaning.pattern.steps[-1]),
        axes=[ref.dimension("sales.customers.region")],
    )
    assert isinstance(result.row_contract.family_semantics, FunnelAttributionSemantics)
    fields = {field.name: field for field in result.schema.columns}
    for name in ("method", "causal_claim"):
        assert fields[name].role_id == "method_identity"
        assert fields[name].field_id.value == f"generated.attribute.{name}@v1"
    assert "attribution.where" not in result.contract().render()
    with pytest.raises(DatasetConstructionError):
        result.where(eq(result.fields.get("status"), "ok"))
    for value in (delta, result):
        payload = _semantics_payload(value.row_contract.family_semantics)
        assert _semantics(payload) == value.row_contract.family_semantics
        with pytest.raises(Exception):
            _semantics({**payload, "unknown": "not admitted"})


def test_foreign_session_and_non_funnel_input_fail_before_execution() -> None:
    j = journey(make_event_sources())
    foreign = journey(make_event_sources(session_id="foreign"))
    with pytest.raises(DatasetConstructionError):
        j.funnel().compare(foreign.funnel())
    with pytest.raises(DatasetConstructionError):
        j.compare(j)


@pytest.mark.parametrize(
    "option",
    [
        "initial",
        "missing",
        "empty_axes",
        "bool_top_k",
        "bad_top_k",
        "grouped",
        "single_hierarchy",
        "large_top_k",
    ],
)
def test_attribution_admission_is_event_owned(option: str) -> None:
    j = journey(make_event_sources())
    meaning = j.row_contract.family_semantics
    assert isinstance(meaning, EventJourneySemantics)
    axes = (ref.dimension("sales.customers.region"),)
    f = j.funnel(axes=axes if option == "grouped" else [])
    delta = f.compare(f)
    target = funnel_loss_rate(step=meaning.pattern.steps[0 if option == "initial" else -1])
    with pytest.raises(DatasetConstructionError):
        if option == "missing":
            delta.attribute(axes=axes)
        elif option == "bool_top_k":
            from marivo.analysis.domains.event_attribution import attribute

            # Call through runtime type admission without weakening the API annotation.
            attribute(delta, target=target, axes=axes, mode="joint", top_k=False)
        else:
            delta.attribute(
                target=target,
                axes=[] if option == "empty_axes" else axes,
                top_k=0 if option == "bad_top_k" else 1001 if option == "large_top_k" else None,
                mode="hierarchy" if option == "single_hierarchy" else "joint",
            )


def test_generated_delta_schema_cannot_be_rebound() -> None:
    j = journey(make_event_sources())
    delta = j.funnel().compare(j.funnel())
    fields = list(delta.schema.columns)
    fields[-2] = replace(fields[-2], _token=d._CORE_TOKEN, nullable=False)
    row = replace(delta.row_contract, _token=d._CORE_TOKEN, schema=d._make_schema(tuple(fields)))
    with pytest.raises(DatasetConstructionError):
        delta._registration.validate(row, delta.row_set_contract)


def test_checkpoint_scope_rule_versions_comparison_identity() -> None:
    from marivo.analysis.observation.contracts import producer_contract

    versions = producer_contract("event.compare").versions
    assert ("event_funnel_checkpoint_scope", "v1") in versions
