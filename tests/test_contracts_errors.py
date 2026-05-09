from __future__ import annotations

import pytest

from marivo.contracts.errors import (
    ConflictError,
    DomainError,
    ErrorCode,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)

# --- ErrorCode ---


def test_error_code_all_codes_defined() -> None:
    expected = {
        "not_found",
        "conflict",
        "forbidden",
        "validation",
        "session_closed",
        "session_not_found",
        "model_not_found",
        "model_revision_conflict",
        "evidence_not_found",
        "evidence_hash_mismatch",
        "query_execution_failed",
        "datasource_unavailable",
    }
    actual = {code.value for code in ErrorCode}
    assert actual == expected


# --- DomainError ---


def test_domain_error_str_returns_message() -> None:
    err = DomainError(ErrorCode.NOT_FOUND, "thing not found")
    assert str(err) == "thing not found"


def test_domain_error_code_and_message() -> None:
    err = DomainError(ErrorCode.VALIDATION, "bad input", detail={"field": "x"})
    assert err.code == ErrorCode.VALIDATION
    assert err.message == "bad input"
    assert err.detail == {"field": "x"}


def test_domain_error_default_detail_is_empty() -> None:
    err = DomainError(ErrorCode.CONFLICT, "conflict")
    assert err.detail == {}


def test_domain_error_is_exception() -> None:
    with pytest.raises(DomainError, match="not found"):
        raise DomainError(ErrorCode.NOT_FOUND, "not found")


# --- Error Subclasses ---


def test_not_found_error() -> None:
    err = NotFoundError(ErrorCode.NOT_FOUND, "session missing")
    assert isinstance(err, DomainError)
    assert isinstance(err, Exception)
    assert str(err) == "session missing"


def test_conflict_error() -> None:
    err = ConflictError(ErrorCode.CONFLICT, "revision conflict")
    assert isinstance(err, DomainError)


def test_forbidden_error() -> None:
    err = ForbiddenError(ErrorCode.FORBIDDEN, "no access")
    assert isinstance(err, DomainError)


def test_validation_error() -> None:
    err = ValidationError(ErrorCode.VALIDATION, "bad input")
    assert isinstance(err, DomainError)


def test_subclass_raises() -> None:
    with pytest.raises(NotFoundError):
        raise NotFoundError(ErrorCode.NOT_FOUND, "gone")

    with pytest.raises(DomainError):
        raise ConflictError(ErrorCode.CONFLICT, "clash")


# --- IntegrityError ---


def test_integrity_error_is_domain_error() -> None:
    from marivo.contracts.errors import ErrorCode, IntegrityError

    err = IntegrityError(message="evidence corrupt")
    assert isinstance(err, DomainError)
    assert err.code == ErrorCode.EVIDENCE_HASH_MISMATCH
    assert "evidence corrupt" in err.message
