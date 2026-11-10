"""Structured fields for analysis errors."""

import pytest

from marivo.analysis.core.model import CoreRuleError, reject
from marivo.analysis.materialization.errors import MaterializationError


@pytest.mark.parametrize("location", ("frame.attribute", "frame.method_facts", "frame.show"))
def test_core_errors_do_not_infer_help_identity_from_diagnostic_location(location: str) -> None:
    error = CoreRuleError(
        expected="valid input", received="invalid", repair="Repair the input.", location=location
    )
    assert error.repair is not None
    assert error.repair.help_target.display == "analysis"
    assert error.location == location


def test_core_reject_passes_explicit_help_identity_independently_of_location() -> None:
    with pytest.raises(CoreRuleError) as captured:
        reject(
            "valid input",
            "invalid",
            "Inspect the Frame.",
            "a private diagnostic location",
            canonical_id="analysis_frame.show",
        )
    error = captured.value
    assert error.location == "a private diagnostic location"
    assert error.repair is not None
    assert error.repair.help_target.display == "analysis.analysis_frame.show"


def test_error_contract_omits_internal_publication_identity() -> None:
    error = MaterializationError(
        expected="complete input", received="partial input", repair="Restore the input."
    )
    assert not hasattr(error, "run_ref")
