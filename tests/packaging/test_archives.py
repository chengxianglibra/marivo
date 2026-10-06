"""Archive inventory rejects missing, extra and altered package content."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.package_contents import REQUIRED_PATHS, check_archives


@pytest.mark.parametrize("damage", ("missing", "changed", "extra"))
@pytest.mark.parametrize("archive_kind", ("wheel", "sdist"))
def test_archive_content_damage_is_rejected(tmp_path: Path, damage: str, archive_kind: str) -> None:
    source: dict[str, bytes] = {}
    for name in (*sorted(REQUIRED_PATHS), "marivo/__init__.py"):
        content = ("package source: " + name + "\n").encode()
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        source[name] = content
    damaged = dict(source)
    if damage == "missing":
        del damaged["marivo/__init__.py"]
    elif damage == "changed":
        damaged["marivo/__init__.py"] = b"altered package bytes\n"
    else:
        damaged["marivo/unowned.py"] = b"unexpected package file\n"
    wheel = tmp_path / "candidate.whl"
    sdist = tmp_path / "candidate.tar.gz"
    with ZipFile(wheel, "w") as archive:
        for name, content in (damaged if archive_kind == "wheel" else source).items():
            archive.writestr(name, content)
    with tarfile.open(sdist, "w:gz") as archive:
        for name, content in (damaged if archive_kind == "sdist" else source).items():
            member = tarfile.TarInfo("candidate/" + name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    with pytest.raises(ValueError, match="archive differs"):
        check_archives(tmp_path, wheel, sdist)
