"""Event constructor repairs keep truthful business choices and runnable retries."""

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import InvalidEventMatchingPolicyError, InvalidEventPatternError
from marivo.analysis.event import _event_repair


def test_event_value_constructor_repairs_require_truthful_user_choice() -> None:
    with pytest.raises(InvalidEventPatternError) as pattern_error:
        mv.sequence()
    with pytest.raises(InvalidEventMatchingPolicyError) as matching_error:
        mv.every_start(completion_assignment="invalid")  # type: ignore[arg-type]

    for error in (
        pattern_error.value,
        matching_error.value,
    ):
        assert error.expected
        assert error.received
        assert error.location
        assert error.repair is not None
        assert error.repair.kind == "user_choice"
        assert error.repair.help_target.canonical_id == "events.match"
        assert error.repair.snippet is None
    assert matching_error.value.repair is not None
    assert matching_error.value.repair.candidates == (
        'completion_assignment="exclusive"',
        'completion_assignment="shared"',
    )


def test_event_repair_factory_enforces_retry_snippet_invariant() -> None:
    with pytest.raises(ValueError, match="requires a runnable snippet"):
        _event_repair(kind="retry", action="Retry the call.")
    with pytest.raises(ValueError, match="only Event retry repairs"):
        _event_repair(
            kind="inspect",
            action="Inspect the current Event.",
            snippet="session.events.match(...)",
        )
