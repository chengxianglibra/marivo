"""Current package-owner and exact archive-content validation."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path
from zipfile import ZipFile

SKILLS = ("marivo/skills/marivo-analysis/SKILL.md", "marivo/skills/marivo-semantic/SKILL.md")
REQUIRED_PATHS = frozenset(SKILLS)


def source_contents(root: Path) -> dict[str, str]:
    """Hash current product code and the two unchanged packaged workflow resources."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root / "marivo").rglob("*")
        if path.is_file() and (path.suffix == ".py" or path.name == "SKILL.md")
    }


def validate_contents(contents: dict[str, str], expected: dict[str, str]) -> None:
    """Reject missing workflow resources, extra package files and changed bytes."""
    missing = REQUIRED_PATHS.difference(contents)
    if missing:
        raise ValueError(f"missing package resources={sorted(missing)!r}")
    if contents != expected:
        raise ValueError("archive differs from the current product code/resource hashes")


def check_archives(root: Path, wheel: Path, sdist: Path) -> dict[str, str | int]:
    """Check wheel and sdist against one exact source inventory without installing."""
    expected = source_contents(root)
    with ZipFile(wheel) as archive:
        wheel_contents = {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name.startswith("marivo/")
        }
    with tarfile.open(sdist) as archive:
        sdist_contents = {}
        for member in archive.getmembers():
            parts = member.name.split("/", 1)
            if member.isfile() and len(parts) == 2 and parts[1].startswith("marivo/"):
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("sdist member has no readable bytes")
                with stream:
                    sdist_contents[parts[1]] = hashlib.sha256(stream.read()).hexdigest()
    validate_contents(wheel_contents, expected)
    validate_contents(sdist_contents, expected)
    return {
        "evidence_kind": "archive_contents_only",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "sdist_sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
        "product_files": len(expected),
    }
