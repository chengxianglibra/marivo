"""Localization catalogs for Marivo report HTML rendering.

Label translations live in JSON resources under ``locales/``. ``en.json`` is the
canonical key set and English source of truth; other languages provide overrides
and fall back to English for any missing key.

Language codes follow BCP 47 (e.g. ``zh-Hans``, ``zh-Hant``, ``pt-BR``). The
catalog lookup applies progressive-prefix fallback so ``zh-Hans-CN`` resolves to
the ``zh-Hans`` catalog when no region-specific file exists. A bare macrolanguage
tag such as ``zh`` does *not* auto-pick a script variant — callers should pass
``zh-Hans`` or ``zh-Hant`` explicitly to avoid ambiguity once both are bundled.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources

DEFAULT_LANGUAGE = "en"
_PUBLISH_PACKAGE = "marivo.analysis.publish"


@cache
def _catalog(language: str) -> dict[str, str]:
    resource = resources.files(_PUBLISH_PACKAGE) / "locales" / f"{language}.json"
    try:
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    loaded = json.loads(text)
    return {str(key): str(value) for key, value in loaded.items()}


@cache
def _resolve_catalog(language: str) -> dict[str, str]:
    """Return the best catalog for *language* using BCP 47 prefix fallback.

    Tries the full tag, then progressively shorter prefixes (``zh-Hans-CN`` →
    ``zh-Hans`` → ``zh``), returning the first non-empty catalog. Returns an
    empty dict if no prefix matches.
    """
    subtags = [part for part in language.split("-") if part]
    for end in range(len(subtags), 0, -1):
        candidate = "-".join(subtags[:end])
        catalog = _catalog(candidate)
        if catalog:
            return catalog
    return {}


def labels_for(language: str) -> dict[str, str]:
    """Return localized report labels for ``language`` with English fallback."""
    base = _catalog(DEFAULT_LANGUAGE)
    if not language or language == DEFAULT_LANGUAGE:
        return dict(base)
    resolved = _resolve_catalog(language)
    if not resolved:
        return dict(base)
    return {**base, **resolved}


def available_languages() -> tuple[str, ...]:
    """Return the language codes for which a locale catalog is bundled."""
    locales_dir = resources.files(_PUBLISH_PACKAGE) / "locales"
    return tuple(
        sorted(
            str(path.name).removesuffix(".json")
            for path in locales_dir.iterdir()
            if str(path.name).endswith(".json")
        )
    )
