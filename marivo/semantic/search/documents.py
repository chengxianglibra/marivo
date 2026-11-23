"""Stable projections of captured search documents."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from marivo.semantic.search.models import (
    SearchDocument,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


_PROJECTION_VERSION = "catalog-search-document-v1"


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _terms(text: str) -> tuple[str, ...]:
    text = _normalized(re.sub(r"([a-z])([A-Z])", r"\1 \2", text))
    words = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", text)
    terms: list[str] = []
    for word in words:
        if "\u3400" <= word[0] <= "\u9fff":
            terms.extend(word[index : index + 2] for index in range(max(1, len(word) - 1)))
        else:
            terms.append(word)
    return tuple(terms)


def _fields(document: SearchDocument) -> tuple[tuple[str, str], ...]:
    return (("id", document.id), ("path", document.path), ("name", document.name), *document.fields)


def _projection(document: SearchDocument) -> str:
    return "\n".join(f"{label}: {text}" for label, text in _fields(document) if text)


def exact_identity_matches(documents: Iterable[SearchDocument], query: str) -> set[str]:
    """Resolve exact canonical IDs or paths within the selected search scope."""
    normalized = _normalized(query)
    return {
        document.id
        for document in documents
        if normalized in {_normalized(document.id), _normalized(document.path)}
    }
