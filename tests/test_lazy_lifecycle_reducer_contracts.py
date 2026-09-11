"""Private reducer admission, exact part demand and closed metadata contracts."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.lifecycle import ROLES
from marivo.analysis.domains.lifecycle_reducers import LifecycleReducerPayload, in_state
from marivo.analysis.materialization.contracts import _semantics, _semantics_payload
from marivo.analysis.materialization.retained import required_part_roles
from marivo.analysis.observation.contracts import producer_contract
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_lifecycle_fixtures import END, MODEL, START, history, sources_without_io


def test_all_closed_reducers_and_exact_part_demand() -> None:
    h = history(sources_without_io())
    cases: tuple[tuple[Dataset, set[str]], ...] = (
        (h.distribution(at=(END, START)), {ROLES[1]}),
        (h.transitions(), {ROLES[0]}),
        (h.dwell(), set()),
        (h.violations(), {ROLES[2]}),
        (h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END)), {ROLES[1]}),
    )
    for value, required in cases:
        assert required_part_roles(value, input_dataset=h) == required
        assert isinstance(value._root, LogicalRootHandle)
        assert producer_contract(value._root.operator_id).retained_contract_ids == ()
        if isinstance(value._root.payload, LifecycleReducerPayload):
            semantics = value.row_contract.family_semantics
            assert _semantics(json.loads(json.dumps(_semantics_payload(semantics)))) == semantics
        assert "\n" not in repr(value)


@pytest.mark.parametrize(
    "points",
    [
        (),
        (START, START),
        (START.replace(tzinfo=None),),
        (START - timedelta(microseconds=1),),
        (END + timedelta(microseconds=1),),
        (START, START.astimezone(timezone(timedelta(hours=8)))),
    ],
)
def test_invalid_distribution_instants(points: tuple[datetime, ...]) -> None:
    with pytest.raises(DatasetConstructionError):
        history(sources_without_io()).distribution(at=points)


def test_selection_checks_model_window_and_aware_instant() -> None:
    from marivo.refs import ref

    h = history(sources_without_io())
    for state, point in (
        (ModelStateHandle(MODEL, "absent"), START),
        (ModelStateHandle(ref.state_model("sales.other"), "open"), START),
        (ModelStateHandle(MODEL, "open"), END + timedelta(seconds=1)),
        (ModelStateHandle(MODEL, "open"), START.replace(tzinfo=None)),
    ):
        with pytest.raises(DatasetConstructionError):
            h.select_subjects(in_state(state, at=point))


def test_structural_and_identity_filters_are_rejected() -> None:
    from marivo.analysis.observation.predicates import is_null

    h = history(sources_without_io())
    with pytest.raises(DatasetConstructionError):
        h.where()
    v = h.violations()
    with pytest.raises(DatasetConstructionError):
        v.where(is_null(v.fields.get("entity_identity")))
    with pytest.raises(DatasetConstructionError):
        h.transitions().dwell()


@pytest.mark.parametrize("shape", ["distribution", "transitions", "dwell", "violations"])
def test_malformed_retained_history_is_typed_integrity_error(shape: str) -> None:
    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.materialization.lifecycle_reducer_codec import decode_semantics

    h = history(sources_without_io())
    value = (
        h.distribution(at=(START,))
        if shape == "distribution"
        else h.transitions()
        if shape == "transitions"
        else h.dwell()
        if shape == "dwell"
        else h.violations()
    )
    encoded = _semantics_payload(value.row_contract.family_semantics)
    encoded["history_json"] = "invalid-json-private-canary"
    with pytest.raises(IntegrityError) as caught:
        decode_semantics(encoded)
    assert "private-canary" not in str(caught.value)


def test_private_selector_does_not_replace_current_public_constructor() -> None:
    import inspect

    import marivo.analysis as mv
    from marivo.analysis.domains.lifecycle_reducers import InState
    from marivo.analysis.lifecycle import InState as PublicInState
    from marivo.analysis.lifecycle import in_state as public_in_state

    exported = mv.__getattr__("in_state")
    assert callable(exported)
    assert tuple(inspect.signature(exported).parameters) == ("state", "as_of")
    assert tuple(inspect.signature(public_in_state).parameters) == ("state", "as_of")
    assert tuple(inspect.signature(in_state).parameters) == ("state", "at")
    state = ModelStateHandle(MODEL, "open")
    assert type(public_in_state(state, as_of=START.isoformat())) is PublicInState
    assert type(in_state(state, at=START)) is InState
    assert isinstance(mv.__all__, (list, tuple))
    assert "LogicalLifecycleDataset" not in mv.__all__


def test_fractional_duration_storage_is_owned_only_by_dwell() -> None:
    from dataclasses import replace

    import pandas as pd
    import pyarrow as pa

    from marivo.analysis.datasets import descriptors as d
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.local import validate_frame
    from marivo.analysis.materialization.storage import _matches_type, _realized_schema

    value = history(sources_without_io()).dwell()
    row = value.row_contract
    schema = pa.schema(
        [
            (
                f.name,
                pa.string()
                if f.name == "model_state"
                else pa.float64()
                if f.logical_type_id == "duration"
                else pa.int64(),
            )
            for f in row.schema.columns
        ]
    )
    _realized_schema(row, schema)
    assert _matches_type("duration", pa.int64())
    assert not _matches_type("duration", pa.float64())
    other = replace(row, _token=d._CORE_TOKEN, family_semantics=d._complete_from_schema())
    with pytest.raises(MaterializationError, match="logical type mismatch"):
        _realized_schema(other, schema)
    frame = pd.DataFrame(
        {
            f.name: pd.Series([], dtype=pd.ArrowDtype(schema.field(f.name).type))
            for f in row.schema.columns
        }
    )
    validate_frame(frame, row, value.row_set_contract)
    with pytest.raises(MaterializationError, match="invalid local duration"):
        validate_frame(frame, other, value.row_set_contract)
