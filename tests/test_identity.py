from __future__ import annotations

import pytest

from marivo.identity import current_user, require_user, resolve_user


def test_returns_contextvar_value_when_set():
    token = current_user.set("alice")
    try:
        assert resolve_user() == "alice"
    finally:
        current_user.reset(token)


def test_returns_none_when_not_set():
    token = current_user.set(None)
    try:
        assert resolve_user() is None
    finally:
        current_user.reset(token)


def test_normalizes_whitespace_only_to_none():
    token = current_user.set("   ")
    try:
        assert resolve_user() is None
    finally:
        current_user.reset(token)


def test_strips_whitespace_from_contextvar():
    token = current_user.set("  alice  ")
    try:
        assert resolve_user() == "alice"
    finally:
        current_user.reset(token)


def test_require_user_returns_user_when_set():
    token = current_user.set("alice")
    try:
        assert require_user() == "alice"
    finally:
        current_user.reset(token)


def test_require_user_raises_when_not_set():
    token = current_user.set(None)
    try:
        with pytest.raises(RuntimeError, match="User identity not set"):
            require_user()
    finally:
        current_user.reset(token)
