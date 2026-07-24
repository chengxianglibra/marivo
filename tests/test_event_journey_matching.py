"""Pure Event Journey matching semantics."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.errors import (
    AmbiguousEventOrderError,
    EventIdentityError,
    InvalidCompletenessDeclarationError,
    PatternStepMismatchError,
)
from marivo.analysis.intents.events import (
    _coverage,
    _identity_sort_key,
    _match_rows,
    _Occurrence,
    _ResolvedStep,
)


def _bootstrap_event_project(tmp_path: Any) -> None:
    datasource_dir = tmp_path / "models" / "datasources"
    semantic_dir = tmp_path / "models" / "semantic" / "commerce"
    datasource_dir.mkdir(parents=True)
    semantic_dir.mkdir(parents=True)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "event-journey"\n')
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\n"
        "ms.domain(name='commerce', owner='Analytics', default=True)\n"
    )
    (semantic_dir / "events.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "users = ms.entity(\n"
        "    name='users', datasource=warehouse, source=md.table('users'),\n"
        "    primary_key=['user_id'],\n"
        "    ai_context=ms.ai_context(business_definition='One row per user.'),\n"
        ")\n"
        "event_log = ms.entity(\n"
        "    name='event_log', datasource=warehouse, source=md.table('event_log'),\n"
        "    primary_key=['event_id'],\n"
        "    ai_context=ms.ai_context(business_definition='One row per event occurrence.'),\n"
        ")\n"
        "user_id = ms.dimension_column(name='user_id', entity=users, column='user_id')\n"
        "event_id = ms.dimension_column(name='event_id', entity=event_log, column='event_id')\n"
        "event_user_id = ms.dimension_column(\n"
        "    name='user_id', entity=event_log, column='user_id'\n"
        ")\n"
        "event_type = ms.dimension_column(\n"
        "    name='event_type', entity=event_log, column='event_type'\n"
        ")\n"
        "event_time = ms.time_dimension_column(\n"
        "    name='event_time', entity=event_log, column='event_time',\n"
        "    granularity='second', is_default=True,\n"
        ")\n"
        "event_to_user = ms.relationship(\n"
        "    name='event_to_user', from_entity=event_log, to_entity=users,\n"
        "    keys=[ms.join_on(event_user_id, user_id)],\n"
        ")\n\n"
        "@ms.event(\n"
        "    name='cart_created', identity=(event_id,), occurred_at=event_time,\n"
        "    participants=(ms.participant(\n"
        "        name='user', path=(event_to_user,), cardinality='one'\n"
        "    ),),\n"
        "    ai_context=ms.ai_context(business_definition='A cart was created.'),\n"
        ")\n"
        "def cart_created(rows):\n"
        "    return ms.bind(event_type, rows) == 'cart_created'\n\n"
        "@ms.event(\n"
        "    name='payment_succeeded', identity=(event_id,), occurred_at=event_time,\n"
        "    participants=(ms.participant(\n"
        "        name='buyer', path=(event_to_user,), cardinality='one'\n"
        "    ),),\n"
        "    ai_context=ms.ai_context(business_definition='A payment succeeded.'),\n"
        ")\n"
        "def payment_succeeded(rows):\n"
        "    return ms.bind(event_type, rows) == 'payment_succeeded'\n"
    )


def _event_session(
    tmp_path: Any,
    monkeypatch: Any,
    *,
    event_rows_sql: str,
    event_time_type: str = "TIMESTAMP",
    model_replacements: tuple[tuple[str, str], ...] = (),
) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    _bootstrap_event_project(tmp_path)
    model_path = tmp_path / "models" / "semantic" / "commerce" / "events.py"
    model_text = model_path.read_text()
    for old, new in model_replacements:
        assert old in model_text
        model_text = model_text.replace(old, new, 1)
    model_path.write_text(model_text)

    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql("CREATE TABLE users (user_id VARCHAR)")
    backend.raw_sql("INSERT INTO users VALUES ('u1'), ('u2')")
    backend.raw_sql(
        "CREATE TABLE event_log ("
        "event_id VARCHAR, user_id VARCHAR, event_type VARCHAR, "
        f"event_time {event_time_type})"
    )
    backend.raw_sql(f"INSERT INTO event_log VALUES {event_rows_sql}")
    return session_attach.get_or_create(
        name="event_match_probe",
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )


def _resolved(*steps: mv.PatternStep) -> tuple[_ResolvedStep, ...]:
    endpoint = ms.ref.entity("commerce.users")
    return tuple(
        _ResolvedStep(
            step=item,
            details=cast("Any", None),
            endpoint=endpoint,
            subject_identity=("commerce.users.user_id",),
            datasource_name="warehouse",
            event_fingerprint=f"sha256:{item.key}",
        )
        for item in steps
    )


def _occurrence(
    event: Any,
    role: str,
    event_id: str,
    subject_id: str,
    when: str,
) -> _Occurrence:
    return _Occurrence(
        event_ref=event,
        participant_name=role,
        event_identity=(event_id,),
        subject_identity=(subject_id,),
        occurred_at=pd.Timestamp(when),
    )


def test_first_per_subject_is_dense_stable_and_counts_unused_events() -> None:
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    cart_step = mv.step(
        participant=ms.participant_role(event=cart, name="user"),
        key="cart",
    )
    payment_step = mv.step(
        participant=ms.participant_role(event=payment, name="buyer"),
        key="payment",
    )
    pattern = mv.sequence(cart_step, payment_step)
    matching = mv.first_per_subject()
    occurrences = {
        (cart, "user"): (
            _occurrence(cart, "user", "cart_1", "u1", "2026-07-01T01:00:00Z"),
            _occurrence(cart, "user", "cart_2", "u1", "2026-07-01T04:00:00Z"),
            _occurrence(cart, "user", "cart_3", "u2", "2026-07-01T02:00:00Z"),
        ),
        (payment, "buyer"): (
            _occurrence(payment, "buyer", "payment_1", "u1", "2026-07-01T03:00:00Z"),
        ),
    }

    first, unused = _match_rows(
        pattern=pattern,
        matching=matching,
        resolved=_resolved(cart_step, payment_step),
        occurrence_sets=occurrences,
        cohort_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        cohort_end=pd.Timestamp("2026-07-02T00:00:00Z"),
        coverage_complete=False,
    )
    second, second_unused = _match_rows(
        pattern=pattern,
        matching=matching,
        resolved=_resolved(cart_step, payment_step),
        occurrence_sets=occurrences,
        cohort_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        cohort_end=pd.Timestamp("2026-07-02T00:00:00Z"),
        coverage_complete=False,
    )

    assert len(first) == 4
    assert first.groupby("journey_id").size().tolist() == [2, 2]
    assert first["journey_id"].tolist() == second["journey_id"].tolist()
    assert first.loc[
        first["subject_identity"] == ("u1",), "completion_status"
    ].unique().tolist() == ["complete"]
    assert first.loc[
        first["subject_identity"] == ("u2",), "completion_status"
    ].unique().tolist() == ["coverage_censored"]
    missing = first.loc[
        (first["subject_identity"] == ("u2",)) & (first["step_key"] == "payment")
    ].iloc[0]
    assert missing["event_identity"] is None
    assert pd.isna(missing["occurred_at"])
    assert unused == second_unused == 1


@pytest.mark.parametrize(
    ("assignment", "expected"),
    [
        ("exclusive", ["complete", "incomplete"]),
        ("shared", ["complete", "complete"]),
    ],
)
def test_every_start_terminal_assignment_policy(
    assignment: str,
    expected: list[str],
) -> None:
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    cart_step = mv.step(
        participant=ms.participant_role(event=cart, name="user"),
        key="cart",
    )
    payment_step = mv.step(
        participant=ms.participant_role(event=payment, name="buyer"),
        key="payment",
    )
    pattern = mv.sequence(cart_step, payment_step)
    occurrences = {
        (cart, "user"): (
            _occurrence(cart, "user", "cart_1", "u1", "2026-07-01T01:00:00Z"),
            _occurrence(cart, "user", "cart_2", "u1", "2026-07-01T02:00:00Z"),
        ),
        (payment, "buyer"): (
            _occurrence(payment, "buyer", "payment_1", "u1", "2026-07-01T03:00:00Z"),
        ),
    }

    rows, _unused = _match_rows(
        pattern=pattern,
        matching=mv.every_start(completion_assignment=cast("Any", assignment)),
        resolved=_resolved(cart_step, payment_step),
        occurrence_sets=occurrences,
        cohort_start=pd.Timestamp("2026-07-01T00:00:00Z"),
        cohort_end=pd.Timestamp("2026-07-02T00:00:00Z"),
        coverage_complete=True,
    )

    statuses = (
        rows.loc[:, ["journey_id", "completion_status"]]
        .drop_duplicates()["completion_status"]
        .tolist()
    )
    assert statuses == expected


def test_cross_event_same_timestamp_is_ambiguous() -> None:
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    cart_step = mv.step(
        participant=ms.participant_role(event=cart, name="user"),
        key="cart",
    )
    payment_step = mv.step(
        participant=ms.participant_role(event=payment, name="buyer"),
        key="payment",
    )
    pattern = mv.sequence(cart_step, payment_step)
    same_time = "2026-07-01T01:00:00Z"
    occurrences = {
        (cart, "user"): (_occurrence(cart, "user", "cart_1", "u1", same_time),),
        (payment, "buyer"): (_occurrence(payment, "buyer", "payment_1", "u1", same_time),),
    }

    with pytest.raises(AmbiguousEventOrderError) as captured:
        _match_rows(
            pattern=pattern,
            matching=mv.first_per_subject(),
            resolved=_resolved(cart_step, payment_step),
            occurrence_sets=occurrences,
            cohort_start=pd.Timestamp("2026-07-01T00:00:00Z"),
            cohort_end=pd.Timestamp("2026-07-02T00:00:00Z"),
            coverage_complete=True,
        )

    assert captured.value.kind == "ambiguous_event_order"
    assert captured.value.expected == "distinct timestamps for cross-Event ordering"


@pytest.mark.parametrize(
    ("receipts", "declare_payment", "expected"),
    [
        ("both", False, "observed_watermark"),
        ("cart", True, "mixed"),
        ("none", False, "unknown"),
    ],
)
def test_coverage_basis_uses_receipt_then_exact_declaration(
    receipts: str,
    declare_payment: bool,
    expected: str,
) -> None:
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    cart_step = mv.step(
        participant=ms.participant_role(event=cart, name="user"),
        key="cart",
    )
    payment_step = mv.step(
        participant=ms.participant_role(event=payment, name="buyer"),
        key="payment",
    )
    resolved = _resolved(cart_step, payment_step)
    event_irs = {
        cart.path: SimpleNamespace(
            source_entity="commerce.event_log",
            occurred_at="commerce.event_log.event_time",
        ),
        payment.path: SimpleNamespace(
            source_entity="commerce.event_log",
            occurred_at="commerce.event_log.event_time",
        ),
    }
    complete_receipt = {
        "complete_through": "2026-07-03T00:00:00Z",
        "authority": "warehouse_reconciliation",
        "observed_at": "2026-07-03T01:00:00Z",
        "source_revision": "fixture-v1",
    }

    class _Runtime:
        def event_watermark(self, _datasource: str, request: Any) -> Any:
            if receipts == "both":
                return complete_receipt
            if receipts == "cart" and request.event_ref == cart:
                return complete_receipt
            return None

    session = SimpleNamespace(
        report_tz=ZoneInfo("UTC"),
        catalog=SimpleNamespace(
            _require_index=lambda: SimpleNamespace(registry=SimpleNamespace(events=event_irs))
        ),
        _connection_runtime=_Runtime(),
    )
    declarations = {}
    if declare_payment:
        declarations[payment] = mv.declared_complete_through(
            inputs=(payment,),
            through="2026-07-03T00:00:00Z",
            rationale="The payment fixture is reconciled.",
        )

    coverage, basis = _coverage(
        session=cast("Any", session),
        resolved=resolved,
        completion=pd.Timestamp("2026-07-03T00:00:00Z"),
        completion_through="2026-07-03T00:00:00Z",
        declaration_by_event=declarations,
    )

    assert basis == expected
    if expected == "mixed":
        assert [item.basis for item in coverage] == [
            "observed_watermark",
            "declared_complete",
        ]


def test_same_time_numeric_event_identity_uses_typed_value_order() -> None:
    identities = ((2,), (10,))

    assert tuple(sorted(identities, key=_identity_sort_key)) == ((2,), (10,))


@pytest.mark.parametrize(
    (
        "event_time_type",
        "cart_time_sql",
        "payment_time_sql",
        "model_replacements",
    ),
    [
        (
            "TIMESTAMP",
            "TIMESTAMP '2026-07-01 00:00:00'",
            "TIMESTAMP '2026-07-03 00:00:00'",
            (),
        ),
        (
            "DATE",
            "DATE '2026-07-01'",
            "DATE '2026-07-03'",
            (("granularity='second'", "granularity='day'"),),
        ),
        (
            "VARCHAR",
            "'20260701'",
            "'20260703'",
            (
                (
                    "granularity='second', is_default=True,",
                    ("granularity='day', is_default=True, parse=ms.strptime('%Y%m%d'),"),
                ),
            ),
        ),
    ],
    ids=("timestamp", "date", "day_encoded"),
)
def test_completion_through_is_inclusive_for_governed_time_resolutions(
    tmp_path: Any,
    monkeypatch: Any,
    event_time_type: str,
    cart_time_sql: str,
    payment_time_sql: str,
    model_replacements: tuple[tuple[str, str], ...],
) -> None:
    session = _event_session(
        tmp_path,
        monkeypatch,
        event_time_type=event_time_type,
        event_rows_sql=(
            f"('e1', 'u1', 'cart_created', {cart_time_sql}),"
            f"('e2', 'u1', 'payment_succeeded', {payment_time_sql})"
        ),
        model_replacements=model_replacements,
    )
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    pattern = mv.sequence(
        mv.step(
            participant=ms.participant_role(event=cart, name="user"),
            key="cart",
        ),
        mv.step(
            participant=ms.participant_role(event=payment, name="buyer"),
            key="payment",
        ),
    )
    through = "2026-07-03T00:00:00Z"

    frame = session.events.match(
        pattern=pattern,
        cohort_window=mv.TimeScope(
            start="2026-07-01T00:00:00Z",
            end="2026-07-02T00:00:00Z",
        ),
        completion_through=through,
        matching=mv.first_per_subject(),
        completeness=(
            mv.declared_complete_through(
                inputs=(cart, payment),
                through=through,
                rationale="The date fixture is reconciled through the inclusive bound.",
            ),
        ),
    )

    rows = frame.to_pandas()
    assert rows["completion_status"].unique().tolist() == ["complete"]
    payment_row = rows.loc[rows["step_key"] == "payment"].iloc[0]
    assert payment_row["event_identity"] == ("e2",)
    assert payment_row["occurred_at"] == pd.Timestamp("2026-07-03T00:00:00Z")


def test_multi_participant_event_preserves_null_identity_for_validation(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    original = (
        "        name='user', path=(event_to_user,), cardinality='one'\n"
        "    ),),\n"
        "    ai_context=ms.ai_context(business_definition='A cart was created.'),"
    )
    with_second_role = (
        "        name='user', path=(event_to_user,), cardinality='one'\n"
        "    ), ms.participant(\n"
        "        name='buyer', path=(event_to_user,), cardinality='one'\n"
        "    )),\n"
        "    ai_context=ms.ai_context(business_definition='A cart was created.'),"
    )
    session = _event_session(
        tmp_path,
        monkeypatch,
        event_rows_sql=("(NULL, 'u1', 'cart_created', TIMESTAMP '2026-07-01 01:00:00')"),
        model_replacements=((original, with_second_role),),
    )
    cart = ms.ref.event("commerce.cart_created")
    pattern = mv.sequence(
        mv.step(
            participant=ms.participant_role(event=cart, name="user"),
            key="first",
        ),
        mv.step(
            participant=ms.participant_role(event=cart, name="buyer"),
            key="second",
        ),
    )

    with pytest.raises(EventIdentityError, match="empty identity component"):
        session.events.match(
            pattern=pattern,
            cohort_window=mv.TimeScope(
                start="2026-07-01T00:00:00Z",
                end="2026-07-02T00:00:00Z",
            ),
            completion_through="2026-07-02T00:00:00Z",
            matching=mv.first_per_subject(),
        )


def test_event_identity_duplicate_outside_query_window_is_rejected(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    session = _event_session(
        tmp_path,
        monkeypatch,
        event_rows_sql=(
            "('e1', 'u1', 'cart_created', TIMESTAMP '2026-07-01 01:00:00'),"
            "('e1', 'u1', 'cart_created', TIMESTAMP '2026-07-05 01:00:00')"
        ),
    )
    cart = ms.ref.event("commerce.cart_created")
    pattern = mv.sequence(
        mv.step(
            participant=ms.participant_role(event=cart, name="user"),
            key="cart",
        ),
    )

    with pytest.raises(EventIdentityError, match="identity is not unique"):
        session.events.match(
            pattern=pattern,
            cohort_window=mv.TimeScope(
                start="2026-07-01T00:00:00Z",
                end="2026-07-02T00:00:00Z",
            ),
            completion_through="2026-07-02T00:00:00Z",
            matching=mv.first_per_subject(),
        )


def test_incomplete_completeness_declaration_fails_before_query(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    session = _event_session(
        tmp_path,
        monkeypatch,
        event_rows_sql=("('e1', 'u1', 'cart_created', TIMESTAMP '2026-07-01 01:00:00')"),
    )
    cart = ms.ref.event("commerce.cart_created")
    pattern = mv.sequence(
        mv.step(
            participant=ms.participant_role(event=cart, name="user"),
            key="cart",
        ),
    )
    declaration = mv.declared_complete_through(
        inputs=(cart,),
        through="2026-07-01T12:00:00Z",
        rationale="This declaration intentionally stops before follow-up.",
    )

    session._connection_runtime.begin_query_capture()
    with pytest.raises(
        InvalidCompletenessDeclarationError,
        match="does not cover completion_through",
    ):
        session.events.match(
            pattern=pattern,
            cohort_window=mv.TimeScope(
                start="2026-07-01T00:00:00Z",
                end="2026-07-02T00:00:00Z",
            ),
            completion_through="2026-07-03T00:00:00Z",
            matching=mv.first_per_subject(),
            completeness=(declaration,),
        )
    assert session._connection_runtime.take_captured_queries() == []


def test_session_events_match_materializes_persists_and_recovers(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    _bootstrap_event_project(tmp_path)
    backend = ibis.duckdb.connect(":memory:")
    backend.raw_sql("CREATE TABLE users (user_id VARCHAR)")
    backend.raw_sql("INSERT INTO users VALUES ('u1'), ('u2')")
    backend.raw_sql(
        "CREATE TABLE event_log ("
        "event_id VARCHAR, user_id VARCHAR, event_type VARCHAR, event_time TIMESTAMP)"
    )
    backend.raw_sql(
        "INSERT INTO event_log VALUES "
        "('e1', 'u1', 'cart_created', TIMESTAMP '2026-07-01 01:00:00'),"
        "('e2', 'u1', 'payment_succeeded', TIMESTAMP '2026-07-01 02:00:00'),"
        "('e3', 'u2', 'cart_created', TIMESTAMP '2026-07-01 03:00:00')"
    )
    session = session_attach.get_or_create(
        name="event_match",
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )
    cart = ms.ref.event("commerce.cart_created")
    payment = ms.ref.event("commerce.payment_succeeded")
    pattern = mv.sequence(
        mv.step(
            participant=ms.participant_role(event=cart, name="user"),
            key="cart",
        ),
        mv.step(
            participant=ms.participant_role(event=payment, name="buyer"),
            key="payment",
        ),
    )
    through = "2026-07-02T00:00:00Z"
    declared = mv.declared_complete_through(
        inputs=(cart, payment),
        through=through,
        rationale="The fixture is reconciled through the follow-up bound.",
    )

    frame = session.events.match(
        pattern=pattern,
        cohort_window=mv.TimeScope(
            start="2026-07-01T00:00:00Z",
            end="2026-07-02T00:00:00Z",
        ),
        completion_through=through,
        matching=mv.first_per_subject(),
        completeness=(declared,),
    )

    assert frame.meta.coverage_basis == "declared_complete"
    assert len(frame.meta.query_refs) == 2
    assert frame.meta.subject_identity == ("commerce.users.user_id",)
    assert frame.to_pandas()["completion_status"].value_counts().to_dict() == {
        "complete": 2,
        "incomplete": 2,
    }
    recovered = session.get_frame(frame.ref)
    assert isinstance(recovered, mv.EventFrame)
    assert recovered.meta.model_dump(mode="json") == frame.meta.model_dump(mode="json")
    assert recovered.to_pandas().to_dict("records") == frame.to_pandas().to_dict("records")

    repeated_event = session.events.match(
        pattern=mv.sequence(
            mv.step(
                participant=ms.participant_role(event=cart, name="user"),
                key="first_cart",
            ),
            mv.step(
                participant=ms.participant_role(event=cart, name="user"),
                key="second_cart",
            ),
        ),
        cohort_window=mv.TimeScope(
            start="2026-07-01T00:00:00Z",
            end="2026-07-02T00:00:00Z",
        ),
        completion_through=through,
        matching=mv.first_per_subject(),
        completeness=(
            mv.declared_complete_through(
                inputs=(cart,),
                through=through,
                rationale="The cart fixture is reconciled.",
            ),
        ),
    )

    assert len(repeated_event.meta.query_refs) == 1
    assert repeated_event.to_pandas()["completion_status"].unique().tolist() == ["incomplete"]

    session._connection_runtime.begin_query_capture()
    with pytest.raises(PatternStepMismatchError):
        session.events.match(
            pattern=mv.sequence(
                mv.step(
                    participant=ms.participant_role(
                        event=cart,
                        name="not_declared",
                    ),
                    key="cart",
                ),
            ),
            cohort_window=mv.TimeScope(
                start="2026-07-01T00:00:00Z",
                end="2026-07-02T00:00:00Z",
            ),
            completion_through=through,
            matching=mv.first_per_subject(),
        )
    assert session._connection_runtime.take_captured_queries() == []
