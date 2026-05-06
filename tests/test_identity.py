from __future__ import annotations

import os
from unittest.mock import patch

from app.identity import current_user, resolve_user


def test_returns_contextvar_value_when_set():
    token = current_user.set("alice")
    try:
        assert resolve_user() == "alice"
    finally:
        current_user.reset(token)


def test_falls_back_to_env_var():
    token = current_user.set(None)
    try:
        with patch.dict(os.environ, {"MARIVO_DEFAULT_USER": "env_user"}):
            assert resolve_user() == "env_user"
    finally:
        current_user.reset(token)


def test_returns_none_when_both_absent():
    token = current_user.set(None)
    try:
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_user() is None
    finally:
        current_user.reset(token)


def test_normalizes_whitespace_only_to_none():
    token = current_user.set("   ")
    try:
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_user() is None
    finally:
        current_user.reset(token)


def test_strips_whitespace_from_contextvar():
    token = current_user.set("  alice  ")
    try:
        assert resolve_user() == "alice"
    finally:
        current_user.reset(token)


def test_strips_whitespace_from_env_var():
    token = current_user.set(None)
    try:
        with patch.dict(os.environ, {"MARIVO_DEFAULT_USER": "  env_user  "}):
            assert resolve_user() == "env_user"
    finally:
        current_user.reset(token)


def test_empty_env_var_falls_through_to_none():
    token = current_user.set(None)
    try:
        with patch.dict(os.environ, {"MARIVO_DEFAULT_USER": ""}):
            assert resolve_user() is None
    finally:
        current_user.reset(token)


def test_contextvar_takes_priority_over_env():
    token = current_user.set("context_user")
    try:
        with patch.dict(os.environ, {"MARIVO_DEFAULT_USER": "env_user"}):
            assert resolve_user() == "context_user"
    finally:
        current_user.reset(token)
