"""Read executable blocks from current bilingual documentation."""

import re

from tests.support.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


def _blocks(language: str, page: str) -> tuple[str, ...]:
    prefix = "docs" if language == "en" else "zh-cn/docs"
    path = ROOT / f"site/src/content/docs/{prefix}/latest/concepts/{page}.mdx"
    return tuple(
        match[1]
        for match in re.findall(
            r"(?m)^(```|~~~)python\n(.*?)^\1\s*$", path.read_text(), flags=re.DOTALL
        )
    )
