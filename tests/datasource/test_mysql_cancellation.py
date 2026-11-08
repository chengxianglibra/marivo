"""Native MySQL certification timeout and diagnostic cleanup counterexamples."""

import pytest


@pytest.mark.parametrize("code", [3024, True, "3024"])
def test_native_error_diagnostics_read_only_exact_integer_dbapi_codes(code: object) -> None:
    from marivo.datasource.errors import _backend_failure_summary

    summary = _backend_failure_summary(RuntimeError(code, "password=private-test-secret"))
    assert summary.backend_code == ("3024" if type(code) is int else None)
