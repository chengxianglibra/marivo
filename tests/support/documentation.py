"""Read named executable examples from current bilingual documentation."""

import re
from pathlib import Path

from tests.support.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT / "site/src/content/docs"
_PATTERN = re.compile(
    r"\{/\* example: ([a-z0-9-]+) \*/\}\s*\n(```|~~~)python\n(.*?)^\2\s*$",
    re.MULTILINE | re.DOTALL,
)


def _root(language: str) -> Path:
    edition = "docs" if language in ("en", "docs") else "zh-cn/docs"
    return ROOT / edition / "latest"


def _examples(language: str, page: str | None = None) -> dict[str, str]:
    """Read explicit example identities without depending on page/block order."""
    root = _root(language)
    paths = (root / f"{page}.mdx",) if page else sorted(root.rglob("*.mdx"))
    examples: dict[str, str] = {}
    for path in paths:
        if "release-notes" in path.parts or path.name == "contributing.mdx":
            continue
        for identifier, _, source in _PATTERN.findall(path.read_text()):
            if identifier in examples:
                raise ValueError(f"Duplicate documentation example: {identifier}")
            examples[identifier] = source
    return examples


def _example(language: str, identifier: str, *, page: str | None = None) -> str:
    return _examples(language, page)[identifier]
