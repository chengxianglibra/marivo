from __future__ import annotations

import pytest

from app.adapters.local.noop_authz import NoopAuthZ
from app.contracts.ids import Action, ResourceId, UserId
from app.contracts.values import AuthZDecision

noop_authz_factories = [
    ("NoopAuthZ", lambda _: NoopAuthZ()),
]


@pytest.mark.parametrize("name,factory", noop_authz_factories)
def test_always_allows(name, factory, tmp_path):
    authz = factory(tmp_path)
    decision = authz.check(UserId("anyone"), Action("read"), ResourceId("anything"))
    assert isinstance(decision, AuthZDecision)
    assert decision.allowed is True


@pytest.mark.parametrize("name,factory", noop_authz_factories)
def test_allows_write(name, factory, tmp_path):
    authz = factory(tmp_path)
    decision = authz.check(UserId("anyone"), Action("write"), ResourceId("sensitive"))
    assert decision.allowed is True


@pytest.mark.parametrize("name,factory", noop_authz_factories)
def test_returns_authz_decision(name, factory, tmp_path):
    authz = factory(tmp_path)
    decision = authz.check(UserId("user1"), Action("delete"), ResourceId("r1"))
    assert isinstance(decision, AuthZDecision)
