from __future__ import annotations

from pathlib import Path

import pytest

from marivo.adapters.local.sqlite_cache_store import SqliteCacheStore
from marivo.contracts.ids import CacheKey
from marivo.contracts.values import CacheValue


def _make_sqlite_cache_store(tmp_path: Path) -> SqliteCacheStore:
    return SqliteCacheStore(tmp_path / "state.db")


cache_store_factories = [
    ("SqliteCacheStore", _make_sqlite_cache_store),
]


@pytest.mark.parametrize("name,factory", cache_store_factories)
def test_get_returns_none_for_absent(name, factory, tmp_path):
    store = factory(tmp_path)
    assert store.get(CacheKey("missing")) is None


@pytest.mark.parametrize("name,factory", cache_store_factories)
def test_set_and_get_roundtrip(name, factory, tmp_path):
    store = factory(tmp_path)
    store.set(CacheKey("k1"), CacheValue(b'{"v": 1}'))
    result = store.get(CacheKey("k1"))
    assert result is not None
    assert bytes(result) == b'{"v": 1}'


@pytest.mark.parametrize("name,factory", cache_store_factories)
def test_overwrite_existing_key(name, factory, tmp_path):
    store = factory(tmp_path)
    store.set(CacheKey("k1"), CacheValue(b"old"))
    store.set(CacheKey("k1"), CacheValue(b"new"))
    result = store.get(CacheKey("k1"))
    assert result is not None
    assert bytes(result) == b"new"


@pytest.mark.parametrize("name,factory", cache_store_factories)
def test_different_keys_isolated(name, factory, tmp_path):
    store = factory(tmp_path)
    store.set(CacheKey("a"), CacheValue(b"va"))
    store.set(CacheKey("b"), CacheValue(b"vb"))
    assert bytes(store.get(CacheKey("a"))) == b"va"
    assert bytes(store.get(CacheKey("b"))) == b"vb"


@pytest.mark.parametrize("name,factory", cache_store_factories)
def test_ttl_expired_returns_none(name, factory, tmp_path):
    store = factory(tmp_path)
    store.set(CacheKey("ephemeral"), CacheValue(b"gone"), ttl=-1)
    assert store.get(CacheKey("ephemeral")) is None
