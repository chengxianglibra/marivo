"""Validate deterministic packaged-skill contents in a built Marivo wheel."""

from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check-wheel-contents.py <dist-directory>")
    dist_dir = Path(sys.argv[1])
    wheels = tuple(sorted(dist_dir.glob("marivo-*.whl")))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one Marivo wheel in {dist_dir}; found {wheels!r}")

    wheel = wheels[0]
    with ZipFile(wheel) as archive:
        names = tuple(sorted(archive.namelist()))

    semantic_prefix = "marivo/skills/marivo-semantic/"
    semantic_files = tuple(name for name in names if name.startswith(semantic_prefix))
    expected = (f"{semantic_prefix}SKILL.md",)
    if semantic_files != expected:
        raise SystemExit(
            "semantic skill wheel contract failed: "
            f"expected={expected!r}; received={semantic_files!r}"
        )

    forbidden = tuple(
        name
        for name in names
        if "__pycache__" in name
        or name.endswith((".pyc", ".pyo"))
        or (
            name.startswith(semantic_prefix)
            and any(part in name for part in ("/references/", "/examples/"))
        )
    )
    if forbidden:
        raise SystemExit(f"wheel contains forbidden generated or stale files: {forbidden!r}")

    print(f"wheel content contract passed: {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
