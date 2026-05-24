"""BackendCache memoizing wrapper around a caller-supplied factory."""

import ibis
import pytest

from marivo.analysis_py.errors import NoBackendFactoryError
from marivo.analysis_py.executor.backend import BackendCache


def test_get_or_create_calls_factory_once():
    calls = []

    def factory(name: str):
        calls.append(name)
        return ibis.duckdb.connect(":memory:")

    cache = BackendCache(factory)
    a = cache.get_or_create("warehouse")
    b = cache.get_or_create("warehouse")
    assert a is b
    assert calls == ["warehouse"]


def test_get_or_create_different_names_distinct():
    cache = BackendCache(lambda name: ibis.duckdb.connect(":memory:"))
    a = cache.get_or_create("warehouse")
    b = cache.get_or_create("analytics")
    assert a is not b


def test_no_factory_raises():
    cache = BackendCache(None)
    with pytest.raises(NoBackendFactoryError):
        cache.get_or_create("anything")


def test_close_all_calls_disconnect_when_present():
    closed = []

    class FakeBackend:
        def disconnect(self):
            closed.append("done")

    cache = BackendCache(lambda name: FakeBackend())
    cache.get_or_create("x")
    cache.close_all()
    assert closed == ["done"]
    assert cache._cache == {}


def test_close_all_ignores_disconnect_failures():
    class FakeBackend:
        def disconnect(self):
            raise RuntimeError("nope")

    cache = BackendCache(lambda name: FakeBackend())
    cache.get_or_create("x")
    cache.close_all()
    assert cache._cache == {}
