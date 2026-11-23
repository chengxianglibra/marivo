"""Read and validate one Catalog search configuration."""

from __future__ import annotations

import math
import re
from pathlib import Path

from marivo._kernel.compat import tomllib
from marivo._kernel.config import PROJECT_MANIFEST
from marivo.semantic.errors import CatalogSearchError
from marivo.semantic.search.models import (
    DisabledReranker,
    HybridSearchConfig,
    LexicalSearchConfig,
    LocalReranker,
    Reranker,
    SearchConfig,
)


def _config_error(field: str, expected: str, received: object) -> CatalogSearchError:
    return CatalogSearchError(
        stage="configuration",
        message=f"Invalid marivo.toml [semantic.search] setting: {field}.",
        expected=expected,
        received=repr(received),
        action="Correct [semantic.search] in marivo.toml and reload the Catalog.",
    )


def _hub_model_id(value: str) -> str:
    """Require a pinned Hub identity, never a local path that ignores revision."""
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", value)
        or Path(value).exists()
    ):
        raise _config_error(
            "model", "a namespace/model Hub identifier without a conflicting local path", value
        )
    return value


def _table(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _config_error(field, "a TOML table", value)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _text(table: dict[str, object], key: str, *, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or (default is None and not value.strip()):
        raise _config_error(key, "non-empty text" if default is None else "text", value)
    return value


def _positive_integer(table: dict[str, object], key: str, default: int) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _config_error(key, "a positive integer", value)
    return value


def _number(table: dict[str, object], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _config_error(key, "an explicitly calibrated finite number", value)
    return float(value)


def _revision(table: dict[str, object], key: str) -> str:
    revision = _text(table, key)
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise _config_error(key, "a fixed 40-character lowercase model commit hash", revision)
    return revision


def load_search_config(workspace_dir: Path) -> SearchConfig:
    """Read and freeze search settings without importing or invoking a model.

    ``[semantic.search]`` defaults to lexical. Hybrid requires embedding_model,
    embedding_revision and ``[semantic.search.relevance]`` calibration_id and
    min_similarity. Prefixes default to empty text. Model files must already be
    present in the local Hugging Face cache at the fixed revision.
    """
    manifest = workspace_dir / PROJECT_MANIFEST
    if not manifest.is_file():
        return LexicalSearchConfig()
    try:
        with manifest.open("rb") as stream:
            raw: object = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise _config_error("manifest", "readable valid TOML", str(exc)) from exc
    root = _table(raw, "manifest")
    semantic = _table(root.get("semantic", {}), "semantic")
    table = _table(semantic.get("search", {}), "semantic.search")
    mode = table.get("mode", "hybrid" if "embedding_model" in table else "lexical")
    window = _positive_integer(table, "candidate_window", 30)
    if mode == "lexical":
        if "embedding_model" in table or "reranker" in table:
            raise _config_error("mode", "hybrid when model settings are present", mode)
        return LexicalSearchConfig(candidate_window=window)
    if mode != "hybrid":
        raise _config_error("mode", "'lexical' or 'hybrid'", mode)
    gate = _table(table.get("relevance"), "semantic.search.relevance")
    similarity = _number(gate, "min_similarity")
    if not -1.0 <= similarity <= 1.0:
        raise _config_error(
            "min_similarity", "a calibrated cosine cutoff between -1 and 1", similarity
        )
    reranker: Reranker = DisabledReranker()
    rerank_score = 0.0
    if "reranker" in table:
        rerank = _table(table["reranker"], "semantic.search.reranker")
        reranker = LocalReranker(
            model=_hub_model_id(_text(rerank, "model")),
            revision=_revision(rerank, "revision"),
            max_tokens=_positive_integer(rerank, "max_tokens", 512),
        )
        rerank_score = _number(gate, "min_reranker_score")
    return HybridSearchConfig(
        embedding_model=_hub_model_id(_text(table, "embedding_model")),
        embedding_revision=_revision(table, "embedding_revision"),
        calibration_id=_text(gate, "calibration_id"),
        min_similarity=similarity,
        query_prefix=_text(table, "query_prefix", default=""),
        document_prefix=_text(table, "document_prefix", default=""),
        max_tokens=_positive_integer(table, "max_tokens", 512),
        batch_size=_positive_integer(table, "batch_size", 16),
        candidate_window=window,
        reranker=reranker,
        min_reranker_score=rerank_score,
    )
