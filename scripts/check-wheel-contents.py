"""Validate deterministic packaged-skill contents in a built Marivo wheel."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZipFile

from package_contents import check_archives


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check-wheel-contents.py <dist-directory>")
    dist_dir = Path(sys.argv[1])
    wheels = tuple(sorted(dist_dir.glob("marivo-*.whl")))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one Marivo wheel in {dist_dir}; found {wheels!r}")

    wheel = wheels[0]
    sdists = tuple(sorted(dist_dir.glob("marivo-*.tar.gz")))
    if len(sdists) != 1:
        raise SystemExit(f"expected exactly one Marivo sdist; found {sdists!r}")
    evidence = check_archives(Path(__file__).resolve().parents[1], wheel, sdists[0])
    with ZipFile(wheel) as archive:
        names = tuple(sorted(archive.namelist()))

    skill_prefixes = (
        "marivo/skills/marivo-analysis/",
        "marivo/skills/marivo-semantic/",
    )
    for prefix in skill_prefixes:
        packaged_files = tuple(name for name in names if name.startswith(prefix))
        expected = (f"{prefix}SKILL.md",)
        if packaged_files != expected:
            raise SystemExit(
                "packaged skill wheel contract failed: "
                f"expected={expected!r}; received={packaged_files!r}"
            )

    forbidden = tuple(
        name
        for name in names
        if "__pycache__" in name
        or name.endswith((".pyc", ".pyo"))
        or (
            any(name.startswith(prefix) for prefix in skill_prefixes)
            and any(part in name for part in ("/references/", "/examples/"))
        )
    )
    if forbidden:
        raise SystemExit(f"wheel contains forbidden generated or stale files: {forbidden!r}")

    print(f"wheel content contract passed: {wheel}")
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
