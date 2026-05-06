from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.api.middleware import UserIdentityMiddleware
from app.identity import current_user


def _capture_user(request: Request) -> JSONResponse:
    return JSONResponse({"user": current_user.get()})


def _ping(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


_app = Starlette(
    routes=[Route("/capture", _capture_user), Route("/ping", _ping)],
)
_app.add_middleware(UserIdentityMiddleware)

_client = TestClient(_app)


def test_sets_contextvar_from_header():
    response = _client.get("/capture", headers={"X-Marivo-User": "alice"})
    assert response.status_code == 200
    assert response.json()["user"] == "alice"


def test_empty_header_treated_as_none():
    response = _client.get("/capture", headers={"X-Marivo-User": ""})
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_whitespace_header_treated_as_none():
    response = _client.get("/capture", headers={"X-Marivo-User": "   "})
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_no_header_no_error():
    response = _client.get("/capture")
    assert response.status_code == 200
    assert response.json()["user"] is None


def test_header_value_stripped():
    response = _client.get("/capture", headers={"X-Marivo-User": "  alice  "})
    assert response.status_code == 200
    assert response.json()["user"] == "alice"


def test_contextvar_reset_after_request():
    token = current_user.set(None)
    try:
        _client.get("/ping", headers={"X-Marivo-User": "bob"})
        assert current_user.get() is None
    finally:
        current_user.reset(token)
