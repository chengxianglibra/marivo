"""Retrieval contracts over captured documents; model doubles cover I/O only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from marivo.semantic import _catalog_search_model as models
from marivo.semantic.catalog_search import (
    BrowseRetrieval,
    HybridSearchConfig,
    LexicalSearchConfig,
    LocalReranker,
    SearchConfig,
    SearchDocument,
    SearchResult,
    load_search_config,
    search_documents,
)
from marivo.semantic.errors import CatalogSearchError


def _document(
    name: str,
    text: str,
    *,
    domain: str = "sales",
    kind: str = "metric",
    entity: str = "sales.orders",
) -> SearchDocument:
    return SearchDocument(
        id=f"{kind}:{domain}.{name}",
        kind=kind,
        path=f"{domain}.{name}",
        name=name,
        domain=domain,
        entity=entity,
        fields=(("business_definition", text),),
    )


def _search(
    tmp_path: Path,
    documents: tuple[SearchDocument, ...],
    query: str = "",
    *,
    config: SearchConfig | None = None,
    kind: str | None = None,
    domain: str | None = None,
    entity: str | None = None,
    limit: int = 5,
    cursor: str | None = None,
    snapshot: str = "captured-snapshot",
) -> SearchResult:
    return search_documents(
        documents,
        workspace_dir=tmp_path,
        snapshot=snapshot,
        query=query,
        kind=kind,
        domain=domain,
        entity=entity,
        limit=limit,
        cursor=cursor,
        config=LexicalSearchConfig() if config is None else config,
    )


class _Tokenizer:
    def __call__(
        self,
        text: list[str],
        text_pair: list[str] | None = None,
        *,
        truncation: bool,
        return_attention_mask: bool,
    ) -> dict[str, object]:
        assert truncation is False
        return {
            "input_ids": [
                list(range(len(item.split()) + (len(text_pair[index].split()) if text_pair else 0)))
                for index, item in enumerate(text)
            ]
        }


class _Encoder:
    """Vectors distinguish cache/inference boundaries, not semantic quality."""

    max_seq_length = 512
    tokenizer = _Tokenizer()

    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.fail_on: str | None = None

    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> np.ndarray[tuple[int, int], np.dtype[np.float32]]:
        self.inputs.extend(sentences)
        if self.fail_on is not None and any(self.fail_on in sentence for sentence in sentences):
            raise RuntimeError("injected encoder fault")
        return np.array(
            [[0.0, 1.0] if "unrelated" in sentence else [1.0, 0.0] for sentence in sentences],
            dtype=np.float32,
        )


def _hybrid() -> HybridSearchConfig:
    config = HybridSearchConfig(
        embedding_model="test/local",
        embedding_revision="a" * 40,
        calibration_id="external-boundary-test-v1",
        min_similarity=0.8,
        document_prefix="passage: ",
        query_prefix="query: ",
    )
    return config


def test_lexical_chinese_identifier_aliases_and_typo(tmp_path: Path) -> None:
    count = _document("query_count", "查询总量，不表示成功率。")
    success = _document("query_success_rate", "Trino 查询成功率，包括所有错误。")
    excluded = _document("platform_success_rate", "Trino 查询成功率，排除 USER_ERROR。")
    excluded = replace(excluded, fields=(*excluded.fields, ("aliases", "平台可靠性")))
    documents = (count, success, excluded)
    page = _search(tmp_path, documents, "查询成功率")
    assert {hit.id for hit in page.items[:2]} == {success.id, excluded.id}
    assert _search(tmp_path, documents, "平台可靠性").items[0].id == excluded.id
    assert _search(tmp_path, documents, "query_sucess_rate").items[0].id == success.id
    assert _search(tmp_path, documents, success.id).items[0].id == success.id
    assert _search(tmp_path, documents, "volcano eruption forecast").candidate_count == 0
    assert any(label == "business_definition" for label, _ in page.items[0].snippets)


def test_cold_lexical_ranking_is_independent_of_process_hash_seed(tmp_path: Path) -> None:
    # Equal inputs in separate empty caches must keep near-tie rankings stable.
    script = """
import json, sys
from pathlib import Path
from marivo.semantic.catalog_search import SearchDocument, LexicalSearchConfig, search_documents
terms = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
documents = tuple(SearchDocument(f"metric:sales.item_{index}", "metric", f"sales.item_{index}", f"item_{index}", "sales", "sales.orders", tuple((f"part_{part}", " ".join(terms[part::index+1])) for part in range(index+1))) for index in range(7))
documents += tuple(SearchDocument(f"metric:other.extra_{index}", "metric", f"other.extra_{index}", f"extra_{index}", "other", "other.orders", (("definition", " ".join(terms[:index+1])),)) for index in range(6))
page = search_documents(documents, workspace_dir=Path(sys.argv[1]), snapshot="seed-proof", query="golf foxtrot echo delta charlie bravo alpha", kind=None, domain=None, entity=None, limit=20, cursor=None, config=LexicalSearchConfig())
print(json.dumps([(hit.id, hit.score) for hit in page.items]))
"""
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path / seed)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    ]
    assert json.loads(outputs[0]) == json.loads(outputs[1])


def test_browse_prefilters_and_never_loads_a_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("browse attempted model loading")

    monkeypatch.setattr(models, "_load_encoder", forbidden)
    documents = (
        _document("count", "Orders"),
        _document("count", "Queries", domain="ops", entity="ops.events"),
        _document("status", "Status", kind="dimension"),
    )
    page = _search(
        tmp_path, documents, config=_hybrid(), domain="sales", entity="sales.orders", kind="metric"
    )
    assert [hit.id for hit in page.items] == ["metric:sales.count"]
    assert isinstance(page.retrieval, BrowseRetrieval)
    assert page.items[0].channels == ()


def test_hybrid_prefilters_both_channels_and_applies_calibrated_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder()
    monkeypatch.setattr(models, "_load_encoder", lambda model, revision: encoder)
    included = _document("orders", "Customers complete purchases")
    unrelated = _document("unrelated", "unrelated instrument readings")
    excluded = _document("outside", "Customers complete purchases", domain="ops")
    page = _search(
        tmp_path, (included, unrelated, excluded), "buying intent", config=_hybrid(), domain="sales"
    )
    assert [hit.id for hit in page.items] == [included.id]
    assert page.items[0].channels == ("vector",)
    assert all("outside" not in text for text in encoder.inputs)
    assert page.retrieval.mode == "hybrid"


def test_lexical_overlap_cannot_bypass_the_configured_hybrid_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder()
    monkeypatch.setattr(models, "_load_encoder", lambda model, revision: encoder)
    document = _document("policy", "unrelated customer retention policy")
    assert _search(tmp_path, (document,), "customer retention").candidate_count == 1
    rejected = _search(tmp_path, (document,), "customer retention", config=_hybrid())
    assert rejected.items == ()
    assert rejected.candidate_count == 0


def test_lexical_candidates_outside_vector_window_receive_the_same_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder()
    monkeypatch.setattr(models, "_load_encoder", lambda model, revision: encoder)
    documents = (_document("a", "Purchases"), _document("z", "Revenue"))
    page = _search(tmp_path, documents, "revenue", config=replace(_hybrid(), candidate_window=1))
    assert page.candidate_count == 2
    assert [hit.id for hit in page.items] == [document.id for document in documents]
    assert page.items[1].channels == ("lexical",)


def test_exact_identity_is_pinned_even_when_reranker_rejects_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder()
    monkeypatch.setattr(models, "_load_encoder", lambda model, revision: encoder)
    target = _document("unrelated", "unrelated")
    neighbor = _document("neighbor", "sales unrelated metric")
    monkeypatch.setattr(
        models,
        "rerank_documents",
        lambda query, documents, config: {target.id: -100.0, neighbor.id: 100.0},
    )
    config = replace(
        _hybrid(), reranker=LocalReranker("test/reranker", "b" * 40), min_reranker_score=50.0
    )
    page = _search(tmp_path, (target, neighbor), target.id, config=config)
    assert page.items[0].id == target.id
    assert target.id not in {
        hit.id
        for hit in _search(
            tmp_path, (target, neighbor), target.id, config=config, domain="ops"
        ).items
    }


def test_vector_cache_is_incremental_and_revisions_invalidate_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder()
    monkeypatch.setattr(models, "_load_encoder", lambda model, revision: encoder)
    documents = (_document("orders", "Purchases"), _document("revenue", "Sales value"))
    _search(tmp_path, documents, "first intent", config=_hybrid())
    assert sum(text.startswith("passage:") for text in encoder.inputs) == 2
    _search(tmp_path, documents, "second intent", config=_hybrid())
    assert sum(text.startswith("passage:") for text in encoder.inputs) == 2
    changed = (
        replace(documents[0], fields=(("business_definition", "Updated purchases"),)),
        documents[1],
    )
    _search(tmp_path, changed, "third intent", config=_hybrid(), snapshot="changed")
    assert sum(text.startswith("passage:") for text in encoder.inputs) == 3
    _search(
        tmp_path, changed, "fourth intent", config=replace(_hybrid(), embedding_revision="b" * 40)
    )
    assert sum(text.startswith("passage:") for text in encoder.inputs) == 5
    _search(tmp_path, (changed[1],), "fifth intent", config=_hybrid(), snapshot="deleted")
    latest = _search(tmp_path, (changed[1],), "fifth intent", config=_hybrid(), snapshot="deleted")
    assert [hit.id for hit in latest.items] == [changed[1].id]


def test_encoding_failure_keeps_reusable_vectors_but_publishes_no_partial_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder()
    encoder.fail_on = "broken"
    monkeypatch.setattr(models, "_load_encoder", lambda model, revision: encoder)
    documents = (_document("a", "Valid"), _document("b", "broken"))
    with pytest.raises(CatalogSearchError) as captured:
        _search(tmp_path, documents, "intent", config=replace(_hybrid(), batch_size=1))
    assert captured.value.stage == "embedding"
    assert captured.value.repair is not None
    cache = tmp_path / ".marivo/semantic/search"
    assert len(list((cache / "vectors").glob("*.json"))) == 1
    assert not list((cache / "indices").glob("*.json"))
    assert not list((cache / "results").glob("*.json"))
    encoder.fail_on = None
    assert (
        _search(
            tmp_path, documents, "intent", config=replace(_hybrid(), batch_size=1)
        ).candidate_count
        == 2
    )


def test_token_overflow_is_explicit_before_encoder_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder()
    monkeypatch.setattr(models, "_load_encoder", lambda model, revision: encoder)
    with pytest.raises(CatalogSearchError) as captured:
        _search(
            tmp_path,
            (_document("long", "word " * 30),),
            "intent",
            config=replace(_hybrid(), max_tokens=10),
        )
    assert captured.value.stage == "input_length"
    assert "metric:sales.long" in (captured.value.received or "")
    assert encoder.inputs == []


def test_hybrid_runtime_unavailable_is_explicit_without_lexical_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(model: str, revision: str) -> models._Encoder:
        raise ImportError("sentence_transformers is not installed")

    monkeypatch.setattr(models, "_load_encoder", unavailable)
    with pytest.raises(CatalogSearchError) as captured:
        _search(tmp_path, (_document("orders", "Orders"),), "orders", config=_hybrid())
    assert captured.value.stage == "embedding"
    assert "test/local@" in captured.value.configuration
    assert captured.value.expected and captured.value.received and captured.value.repair


def test_frozen_configuration_rejects_unpinned_or_uncalibrated_models(tmp_path: Path) -> None:
    manifest = tmp_path / "marivo.toml"
    manifest.write_text(
        '[semantic.search]\nembedding_model="test/local"\nembedding_revision="main"\n',
        encoding="utf-8",
    )
    with pytest.raises(CatalogSearchError) as captured:
        load_search_config(tmp_path)
    assert captured.value.stage == "configuration"
    manifest.write_text(
        '[semantic.search]\nembedding_model="test/local"\nembedding_revision="'
        + "a" * 40
        + '"\nquery_prefix="query: "\n[semantic.search.relevance]\ncalibration_id="project-v1"\nmin_similarity=0.8\n',
        encoding="utf-8",
    )
    frozen = load_search_config(tmp_path)
    manifest.write_text('[semantic.search]\nmode="lexical"\n', encoding="utf-8")
    assert frozen.mode == "hybrid"
    assert load_search_config(tmp_path).mode == "lexical"


def test_candidate_count_limit_independence_and_stale_cursor_rejection(tmp_path: Path) -> None:
    documents = tuple(_document(f"orders_{index}", "Order count") for index in range(6))
    first = _search(tmp_path, documents, "orders", limit=2)
    assert first.candidate_count == 6
    assert first.next_cursor
    second = _search(tmp_path, documents, "orders", limit=3, cursor=first.next_cursor)
    assert second.candidate_count == 6
    assert {hit.id for hit in first.items}.isdisjoint(hit.id for hit in second.items)
    assert _search(tmp_path, documents, "orders", limit=6).items[:2] == first.items
    with pytest.raises(CatalogSearchError) as captured:
        _search(tmp_path, documents, "orders", cursor=first.next_cursor, domain="ops")
    assert captured.value.stage == "cursor"
    for path in (tmp_path / ".marivo/semantic/search/results").glob("*.json"):
        path.unlink()
    with pytest.raises(CatalogSearchError) as missing:
        _search(tmp_path, documents, "orders", cursor=first.next_cursor)
    assert missing.value.kind == "catalog_search_stale_cursor"


def test_hybrid_cursor_reuses_fixed_ranking_in_a_fresh_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder()
    monkeypatch.setattr(models, "_load_encoder", lambda model, revision: encoder)
    documents = tuple(_document(f"orders_{index}", "Orders") for index in range(3))
    first = _search(tmp_path, documents, "purchase intent", config=_hybrid(), limit=1)
    assert first.next_cursor
    script = """
import json, sys
from pathlib import Path
from marivo.semantic.catalog_search import HybridSearchConfig, SearchDocument, search_documents
root = Path(sys.argv[1])
documents = tuple(SearchDocument(f"metric:sales.orders_{i}", "metric", f"sales.orders_{i}", f"orders_{i}", "sales", "sales.orders", (("business_definition", "Orders"),)) for i in range(3))
config = HybridSearchConfig("test/local", "a" * 40, "external-boundary-test-v1", 0.8, query_prefix="query: ", document_prefix="passage: ")
page = search_documents(documents, workspace_dir=root, snapshot="captured-snapshot", query="purchase intent", kind=None, domain=None, entity=None, limit=2, cursor=sys.argv[2], config=config)
print(json.dumps({"ids": [hit.id for hit in page.items], "count": page.candidate_count}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), first.next_cursor],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "ids": ["metric:sales.orders_1", "metric:sales.orders_2"],
        "count": 3,
    }


def test_local_model_paths_cannot_ignore_the_declared_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    local_model = tmp_path / "test" / "local"
    local_model.mkdir(parents=True)
    manifest = tmp_path / "marivo.toml"
    manifest.write_text(
        '[semantic.search]\nembedding_model="test/local"\n'
        f'embedding_revision="{"a" * 40}"\n'
        '[semantic.search.relevance]\ncalibration_id="probe"\nmin_similarity=0.5\n'
    )
    with pytest.raises(CatalogSearchError, match="conflicting local path"):
        load_search_config(tmp_path)
    with pytest.raises(CatalogSearchError, match="Hub identifier"):
        models._load_encoder(str(local_model), "a" * 40)
    with pytest.raises(CatalogSearchError, match="Hub identifier"):
        models._load_reranker(str(local_model), "a" * 40, 512)


def test_observed_corruption_never_unlinks_a_concurrently_published_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.semantic.catalog_search as search

    documents = tuple(_document(name, name) for name in ("one", "two", "three"))
    winner = _search(tmp_path, documents, limit=1)
    assert winner.next_cursor is not None
    path = next((tmp_path / ".marivo/semantic/search/results").glob("*.json"))
    valid = path.read_bytes()
    path.write_text("corrupt derived cache")
    read = search._read_ranking
    first = True

    def concurrent_publish(
        cache_path: Path, key: str, allowed: set[str]
    ) -> tuple[search.SearchHit, ...]:
        nonlocal first
        if first:
            first = False
            cache_path.write_bytes(valid)
            raise ValueError("reader observed corruption before another writer published")
        return read(cache_path, key, allowed)

    monkeypatch.setattr(search, "_read_ranking", concurrent_publish)
    with pytest.raises(CatalogSearchError) as captured:
        _search(tmp_path, documents, limit=1)
    assert captured.value.stage == "cache"
    assert path.read_bytes() == valid
    continued = _search(tmp_path, documents, limit=1, cursor=winner.next_cursor)
    assert continued.items[0].id != winner.items[0].id
