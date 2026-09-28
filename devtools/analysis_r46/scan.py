"""Inventory retired scenario symbols and deferred private execution consumers."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
RETIRED = re.compile(
    r"_public_snapshot_text|execute_j1|dsl_j[1-4]|dsl\.j[1-4]\.|\bJ1(?:Node|Context)\b"
)
DEFERRED = re.compile(
    r"\bv6\b|legacy|def execution_key\(|def encode_descriptor\(|def decode_descriptor\(|marivo\.analysis_exchange/v1"
)


def scan() -> dict[str, object]:
    retired: list[dict[str, object]] = []
    deferred: list[dict[str, object]] = []
    for base in (ROOT / "marivo", ROOT / "scripts", ROOT / "site/src/content/docs"):
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".md", ".mdx"):
                continue
            if "site" in path.parts and "latest" not in path.parts:
                continue
            relative = str(path.relative_to(ROOT))
            for line, text in enumerate(path.read_text().splitlines(), 1):
                hit = {"path": relative, "line": line, "text": text.strip()}
                if RETIRED.search(text):
                    retired.append(hit)
                if relative.startswith("marivo/analysis/") and DEFERRED.search(text):
                    deferred.append(hit)
    archives = []
    for wheel in sorted((ROOT / "dist/pypi").glob("*.whl")):
        with ZipFile(wheel) as archive:
            matches = [
                name
                for name in archive.namelist()
                if RETIRED.search(name)
                or (name.endswith(".py") and RETIRED.search(archive.read(name).decode()))
            ]
        archives.append(
            {
                "wheel": wheel.name,
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "retired_matches": matches,
            }
        )
    return {
        "retired": retired,
        "deferred_inventory": deferred,
        "archives": archives,
        "passed": not retired and all(not archive["retired_matches"] for archive in archives),
    }


if __name__ == "__main__":
    report = scan()
    Path(sys.argv[1]).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    assert report["passed"] is True, "Retired scenario symbols require consumer review"
