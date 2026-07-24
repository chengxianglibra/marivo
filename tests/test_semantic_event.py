"""Event semantic authoring, compilation, catalog, and fingerprint contracts."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pandas as pd
import pytest

import marivo.semantic as ms
from marivo.preview import PreviewSamplePolicy, preview_from_pandas
from marivo.refs import Ref, SemanticKind
from marivo.semantic._expression_binding import compile_expression_body
from marivo.semantic.catalog import EventEntry, SemanticCatalog, _validate_event_preview
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    SemanticRuntimeError,
)

_DOMAIN = """\
import marivo.semantic as ms
ms.domain(name="commerce", owner="Analytics", default=True)
"""


def _objects(
    *,
    predicate: str = "return ms.bind(event_type, rows) == 'payment_succeeded'",
    participant_path: str = "path=(event_to_buyer,), ",
    participant_cardinality: str = "one",
) -> str:
    return f"""\
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")
buyers = ms.entity(
    name="buyers",
    datasource=warehouse,
    source=md.table("buyers"),
    primary_key=["buyer_id"],
    ai_context=ms.ai_context(business_definition="One row per buyer."),
)
event_log = ms.entity(
    name="event_log",
    datasource=warehouse,
    source=md.table("event_log"),
    primary_key=["event_id"],
    ai_context=ms.ai_context(business_definition="One row per source event."),
)
buyer_id = ms.dimension_column(
    name="buyer_id", entity=buyers, column="buyer_id",
    ai_context=ms.ai_context(business_definition="Buyer identity."),
)
event_id = ms.dimension_column(
    name="event_id", entity=event_log, column="event_id",
    ai_context=ms.ai_context(business_definition="Event identity."),
)
event_buyer_id = ms.dimension_column(
    name="buyer_id", entity=event_log, column="buyer_id",
    ai_context=ms.ai_context(business_definition="Event buyer identity."),
)
event_type = ms.dimension_column(
    name="event_type", entity=event_log, column="event_type",
    ai_context=ms.ai_context(business_definition="Physical event code."),
)
event_time = ms.time_dimension_column(
    name="event_time",
    entity=event_log,
    column="event_time",
    granularity="second",
    parse=ms.timestamp(timezone="UTC"),
    is_default=True,
    ai_context=ms.ai_context(business_definition="Business occurrence time."),
)
event_to_buyer = ms.relationship(
    name="event_to_buyer",
    from_entity=event_log,
    to_entity=buyers,
    keys=[ms.join_on(event_buyer_id, buyer_id)],
)

@ms.event(
    name="payment_succeeded",
    identity=(event_id,),
    occurred_at=event_time,
    participants=(
        ms.participant(
            name="buyer",
            {participant_path}cardinality={participant_cardinality!r},
        ),
    ),
    ai_context=ms.ai_context(
        business_definition="A successfully completed payment.",
    ),
)
def payment_succeeded(rows):
    {predicate}
"""


def _project(
    semantic_project_factory,
    *,
    objects: str | None = None,
    workspace_dir: Path | None = None,
):
    if workspace_dir is not None:
        workspace_dir.mkdir(parents=True, exist_ok=True)
    return semantic_project_factory(
        {
            "commerce/_domain.py": textwrap.dedent(_DOMAIN),
            "commerce/objects.py": textwrap.dedent(objects or _objects()),
        },
        workspace_dir=workspace_dir,
    )


def _load_error_kinds(
    semantic_project_factory,
    objects: str,
) -> set[str]:
    project = semantic_project_factory(
        {
            "commerce/_domain.py": textwrap.dedent(_DOMAIN),
            "commerce/objects.py": textwrap.dedent(objects),
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    return {error.kind for error in result.errors}


def test_filtered_event_is_an_exact_non_callable_event_ref_and_catalog_entry(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory)
    registry = project._registry
    assert registry is not None
    event_ir = registry.events["commerce.payment_succeeded"]

    assert event_ir.source_entity == "commerce.event_log"
    assert event_ir.identity == ("commerce.event_log.event_id",)
    assert event_ir.occurred_at == "commerce.event_log.event_time"
    assert event_ir.predicate_kind == "filtered"

    catalog = SemanticCatalog(project)
    event = catalog.events.get("payment_succeeded")
    assert type(event) is EventEntry
    assert type(event.ref) is Ref
    assert event.ref.kind is SemanticKind.EVENT
    assert not callable(event.ref)
    assert catalog.require(ms.ref.event("commerce.payment_succeeded")) is event
    assert catalog.domains.get("commerce").events.get("payment_succeeded") is event
    assert catalog.entities.get("event_log").events.get("payment_succeeded") is event
    assert event.details().participants[0][0] == "buyer"
    assert event.details().participants[0][1] == ms.ref.entity("commerce.buyers")
    assert ms.ref.dimension("commerce.event_log.event_type") in event.details().parents
    assert event.details().definition_fingerprint.startswith("sha256:")
    assert "definition_fingerprint" in event.details().render()
    verification = catalog.verify(event.ref)
    assert verification.status == "passed"
    readiness = catalog.readiness(refs=(event.ref,))
    assert event.ref in readiness.analysis_ready_refs
    assert "commerce.event_log.event_type" in readiness.input_summary.refs
    assert any(
        issue.kind == "snapshot_missing" and "commerce.payment_succeeded" in issue.refs
        for issue in readiness.warnings
    )


def test_unfiltered_event_requires_explicit_all_rows(
    semantic_project_factory,
) -> None:
    project = _project(
        semantic_project_factory,
        objects=_objects(predicate="return ms.all_rows()"),
    )
    registry = project._registry
    assert registry is not None
    assert registry.events["commerce.payment_succeeded"].predicate_kind == "all_rows"


@pytest.mark.parametrize(
    "predicate",
    [
        "pass",
        "return None",
        "return True",
        "return ms.all_rows() == True",
        "return ms.all_rows() & (ms.bind(event_type, rows) == 'payment_succeeded')",
        "return ms.bind(event_type, rows)",
        (
            "return (ms.bind(event_type, rows) == 'payment_succeeded') "
            "and (ms.bind(event_id, rows) == 'e1')"
        ),
        "return len(rows) > 0",
        "return rows.count() > 0",
        "return 'a' == ms.bind(event_type, rows) == 'b'",
    ],
)
def test_event_rejects_missing_or_open_ended_predicates(
    semantic_project_factory,
    predicate: str,
) -> None:
    kinds = _load_error_kinds(
        semantic_project_factory,
        _objects(predicate=predicate),
    )
    assert ErrorKind.INVALID_EVENT_PREDICATE in kinds


def test_event_predicate_must_bind_a_source_owned_dimension(
    semantic_project_factory,
) -> None:
    kinds = _load_error_kinds(
        semantic_project_factory,
        _objects(predicate="return ms.bind(buyer_id, rows) == 'b1'"),
    )
    assert ErrorKind.BINDING_ENTITY_MISMATCH in kinds


def test_event_identity_owner_is_inferred_from_occurred_at(
    semantic_project_factory,
) -> None:
    source = _objects().replace(
        "identity=(event_id,),",
        "identity=(buyer_id,),",
    )
    kinds = _load_error_kinds(semantic_project_factory, source)
    assert ErrorKind.INVALID_EVENT_SOURCE in kinds


@pytest.mark.parametrize(
    ("replacement", "expected_kind"),
    [
        ("path=(), ", ErrorKind.INVALID_EVENT_PARTICIPANT_PATH),
        (
            "path=(ms.ref.relationship('commerce.missing_path'),), ",
            ErrorKind.INVALID_EVENT_PARTICIPANT_PATH,
        ),
    ],
)
def test_event_rejects_invalid_participant_paths(
    semantic_project_factory,
    replacement: str,
    expected_kind: ErrorKind,
) -> None:
    kinds = _load_error_kinds(
        semantic_project_factory,
        _objects(participant_path=replacement),
    )
    assert expected_kind in kinds or ErrorKind.INVALID_EVENT_PARTICIPANT_PATH in kinds


def test_cardinality_one_participant_requires_endpoint_primary_key(
    semantic_project_factory,
) -> None:
    source = _objects().replace('primary_key=["buyer_id"],', "primary_key=[],", 1)
    kinds = _load_error_kinds(semantic_project_factory, source)
    assert ErrorKind.INVALID_EVENT_PARTICIPANT_CARDINALITY in kinds


def test_participant_role_handle_is_catalog_independent(
    semantic_project_factory,
    tmp_path: Path,
) -> None:
    first = _project(
        semantic_project_factory,
        workspace_dir=tmp_path / "first",
    )
    first_details = SemanticCatalog(first).events.get("payment_succeeded").details()
    first_role = ms.participant_role(
        event=ms.ref.event("commerce.payment_succeeded"),
        name="buyer",
    )
    assert not hasattr(first_role, "event_fingerprint")

    second = _project(
        semantic_project_factory,
        workspace_dir=tmp_path / "second",
    )
    second_details = SemanticCatalog(second).events.get("payment_succeeded").details()
    assert second_details.definition_fingerprint == first_details.definition_fingerprint

    changed = _project(
        semantic_project_factory,
        objects=_objects(predicate="return ms.bind(event_type, rows) == 'payment_captured'"),
        workspace_dir=tmp_path / "changed",
    )
    changed_details = SemanticCatalog(changed).events.get("payment_succeeded").details()
    assert changed_details.definition_fingerprint != first_details.definition_fingerprint

    current_role = ms.participant_role(
        event=ms.ref.event("commerce.payment_succeeded"),
        name="buyer",
    )
    assert current_role == first_role
    assert current_role.key == "event:commerce.payment_succeeded#participant:buyer"


def test_event_external_scalar_constant_is_frozen_into_definition_identity(
    semantic_project_factory,
    tmp_path: Path,
) -> None:
    predicate = "return ms.bind(event_type, rows) == PAYMENT_STATUS"
    first_source = _objects(predicate=predicate).replace(
        "@ms.event(",
        "PAYMENT_STATUS = 'payment_succeeded'\n\n@ms.event(",
        1,
    )
    changed_source = first_source.replace(
        "PAYMENT_STATUS = 'payment_succeeded'",
        "PAYMENT_STATUS = 'payment_captured'",
        1,
    )

    first = _project(
        semantic_project_factory,
        objects=first_source,
        workspace_dir=tmp_path / "constant-first",
    )
    changed = _project(
        semantic_project_factory,
        objects=changed_source,
        workspace_dir=tmp_path / "constant-changed",
    )

    first_fingerprint = (
        SemanticCatalog(first).events.get("payment_succeeded").details().definition_fingerprint
    )
    changed_fingerprint = (
        SemanticCatalog(changed).events.get("payment_succeeded").details().definition_fingerprint
    )
    assert first_fingerprint != changed_fingerprint


def test_event_external_scalar_constant_is_frozen_in_compiled_callable() -> None:
    event_type_ref = ms.ref.dimension("commerce.event_log.event_type")
    source_ref = ms.ref.entity("commerce.event_log")
    status = "payment_succeeded"

    def predicate(rows):
        return ms.bind(event_type_ref, rows) == status

    body = compile_expression_body(
        predicate,
        owning_ref=ms.ref.event("commerce.payment_succeeded"),
        ordered_entity_refs=(source_ref,),
    )
    status = "payment_captured"
    closure_values = dict(
        zip(
            body.callable.__code__.co_freevars,
            (cell.cell_contents for cell in body.callable.__closure__ or ()),
            strict=True,
        )
    )

    assert closure_values["status"] == "payment_succeeded"


def test_event_rejects_non_scalar_external_predicate_state(
    semantic_project_factory,
) -> None:
    source = _objects(predicate="return ms.bind(event_type, rows) == PAYMENT_STATUSES").replace(
        "@ms.event(",
        "PAYMENT_STATUSES = ['payment_succeeded']\n\n@ms.event(",
        1,
    )

    kinds = _load_error_kinds(semantic_project_factory, source)

    assert ErrorKind.INVALID_EVENT_PREDICATE in kinds


def test_event_readiness_includes_predicate_dimension_enrichment(
    semantic_project_factory,
) -> None:
    source = _objects().replace(
        '    ai_context=ms.ai_context(business_definition="Physical event code."),\n',
        "",
        1,
    )
    project = _project(semantic_project_factory, objects=source)
    catalog = SemanticCatalog(project)
    event = catalog.events.get("payment_succeeded")

    readiness = catalog.readiness(refs=(event.ref,))

    assert "commerce.event_log.event_type" in readiness.input_summary.refs
    assert event.ref not in readiness.analysis_ready_refs
    assert any(
        issue.kind == "missing_business_definition"
        and "commerce.event_log.event_type" in issue.refs
        for issue in readiness.blockers
    )


@pytest.mark.parametrize(
    ("subjects", "expected_kind"),
    [
        (["b1", "b1"], ErrorKind.INVALID_EVENT_IDENTITY),
        (["b1", "b2"], ErrorKind.INVALID_EVENT_PARTICIPANT_CARDINALITY),
        ([None], ErrorKind.INVALID_EVENT_PARTICIPANT_CARDINALITY),
    ],
)
def test_event_preview_checks_identity_and_participant_cardinality(
    semantic_project_factory,
    subjects: list[str | None],
    expected_kind: ErrorKind,
) -> None:
    project = _project(semantic_project_factory)
    registry = project._registry
    assert registry is not None
    event_ir = registry.events["commerce.payment_succeeded"]
    event_ids = ["e1"] * len(subjects)
    result = preview_from_pandas(
        pd.DataFrame(
            {
                "__event_identity_0": event_ids,
                "__occurred_at": ["2026-07-01T00:00:00Z"] * len(subjects),
                "__subject_buyer_identity_0": subjects,
            }
        ),
        kind="semantic_event",
        ref=event_ir.semantic_id,
        requested_limit=20,
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=20),
    )

    with pytest.raises(SemanticRuntimeError) as exc_info:
        _validate_event_preview(
            result,
            event_ir=event_ir,
            participants=event_ir.participants,
        )

    assert exc_info.value.kind == expected_kind


def test_event_public_help_has_one_authoring_path() -> None:
    event_help = ms.help_text("event")
    assert "Declare a filtered or explicit all-rows" in event_help
    assert "return ms.all_rows()" in ms.help_text("all_rows")
    assert "participant_role" in ms.help_text("participant_role")
    assert "event_fingerprint" not in ms.help_text("participant_role")
    assert not hasattr(ms, "filtered_event")

    example = event_help.split("  Example:\n", maxsplit=1)[1]
    example = example.split("\n  Constraints:", maxsplit=1)[0]
    compile(textwrap.dedent(example), "<event-help-example>", "exec")


def test_event_authoring_errors_expose_agent_recovery_contract() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.participant(name="Buyer", cardinality="one")

    error = exc_info.value
    assert error.kind == ErrorKind.INVALID_EVENT_PARTICIPANT_PATH
    assert error.expected == "a lowercase snake_case role name"
    assert error.received == "'Buyer'"
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "participant"
    assert error.repair.snippet == "ms.participant(name='buyer', cardinality='one')"
    assert "expected:" in str(error)
    assert "received:" in str(error)
    assert "Repair:" in str(error)
    assert error.repair.snippet in str(error)


@pytest.mark.parametrize(
    ("event", "name", "expected_fragment"),
    [
        (
            ms.ref.metric("commerce.revenue"),
            "buyer",
            "Ref[event]",
        ),
        (
            ms.ref.event("commerce.payment_succeeded"),
            "Buyer",
            "lowercase snake_case",
        ),
    ],
)
def test_participant_role_rejects_invalid_inputs_with_structured_repair(
    event: Ref,
    name: str,
    expected_fragment: str,
) -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.participant_role(event=event, name=name)  # type: ignore[arg-type]

    error = exc_info.value
    assert error.kind == ErrorKind.INVALID_EVENT_PARTICIPANT_PATH
    assert error.expected is not None
    assert expected_fragment in error.expected
    assert error.received is not None
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "participant_role"
