"""Freeze a stable Git working-tree inventory for production-code accounting.

Run with .venv/bin/python scripts/production_code_baseline.py --output PATH.
PATH must be a new ignored directory. Repeat --context for ignored plan files.
The archive retains current bytes, including pre-existing uncommitted changes;
the manifest does not attribute any reduction to a task.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import subprocess
import sys
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNTER_VERSION = "nonempty-physical-v1"


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    bytes: int
    module: str
    category: str
    nonempty_lines: int | None
    mode: int
    link_target: str | None


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *arguments])


def paths(root: Path, context: list[Path]) -> tuple[str, ...]:
    names = {
        name.decode("utf-8")
        for name in git(root, "ls-files", "-co", "--exclude-standard", "-z").split(b"\0")
        if name
    }
    for path in context:
        name = path.resolve().relative_to(root).as_posix()
        if not (root / name).is_file():
            raise ValueError(f"Context file does not exist: {name}")
        names.add(name)
    return tuple(sorted(names))


def read_files(root: Path, names: tuple[str, ...]) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    for name in names:
        path = root / name
        if path.is_symlink():
            path.resolve().relative_to(root)
        if path.is_file():
            contents[name] = path.read_bytes()
        elif path.exists():
            raise ValueError(f"Snapshot requires regular files: {name}")
    return contents


def record(root: Path, name: str, content: bytes) -> FileRecord:
    path = Path(name)
    in_package = path.parts[0] == "marivo"
    module = (
        path.parts[1]
        if in_package and path.parts[1] in {"analysis", "semantic", "datasource"}
        else "shared"
        if in_package
        else "outside_package"
    )
    category = (
        "production_python"
        if in_package and path.suffix == ".py"
        else "typing_stub"
        if in_package and path.suffix == ".pyi"
        else "package_resource"
        if in_package
        else "outside_python"
        if path.suffix == ".py"
        else "repository_context"
    )
    lines = (
        sum(bool(line.strip()) for line in content.decode("utf-8").splitlines())
        if path.suffix in {".py", ".pyi"}
        else None
    )
    source = root / name
    return FileRecord(
        name,
        hashlib.sha256(content).hexdigest(),
        len(content),
        module,
        category,
        lines,
        source.stat().st_mode & 0o777,
        str(source.readlink()) if source.is_symlink() else None,
    )


def capture(root: Path, output: Path, context: list[Path]) -> dict[str, int]:
    root = root.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Snapshot destination already exists: {output}")
    relative_output = output.relative_to(root)
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", str(relative_output)], check=False
    )
    if ignored.returncode != 0:
        raise ValueError("Snapshot destination must be ignored by Git")
    started = datetime.now(timezone.utc).isoformat()
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    diff = git(root, "diff", "--binary", "HEAD")
    index = git(root, "ls-files", "--stage", "-z")
    names = paths(root, context)
    contents = read_files(root, names)
    records = [record(root, name, content) for name, content in sorted(contents.items())]
    if (
        names != paths(root, context)
        or contents != read_files(root, names)
        or records != [record(root, name, content) for name, content in sorted(contents.items())]
        or head != git(root, "rev-parse", "HEAD")
        or status != git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        or diff != git(root, "diff", "--binary", "HEAD")
        or index != git(root, "ls-files", "--stage", "-z")
    ):
        raise RuntimeError("Working tree changed during capture; rerun before using this baseline")
    totals = dict.fromkeys(("analysis", "semantic", "datasource", "shared"), 0)
    for item in records:
        if item.category == "production_python":
            assert item.nonempty_lines is not None
            totals[item.module] += item.nonempty_lines
    files = [asdict(item) for item in records]
    manifest = {
        "schema": "marivo.production-code-baseline.v1",
        "counter_version": COUNTER_VERSION,
        "counter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_utc": started,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "head": head.decode().strip(),
        "branch": git(root, "branch", "--show-current").decode().strip(),
        "root": str(root),
        "policy": {
            "selection": "git ls-files -co --exclude-standard; deduplicated; missing files excluded",
            "primary": "marivo/**/*.py including marivo/*.py; UTF-8 splitlines; bool(line.strip())",
            "nonempty_includes": ["comments", "docstrings", "imports", "declarations"],
            "auxiliary": "all package non-Python resources, stubs and outside-package Python",
            "ignored_context": [str(path.resolve().relative_to(root)) for path in context],
            "attribution": "all changes pre-exist this snapshot; no reduction claim",
            "symlinks": "in-repository file links materialized in archive; link targets recorded",
        },
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "dependencies": dict(
                sorted((dist.metadata["Name"], dist.version) for dist in distributions())
            ),
        },
        "module_nonempty_lines": totals,
        "total_nonempty_lines": sum(totals.values()),
        "production_python_files": sum(item.category == "production_python" for item in records),
        "missing_selected_files": sorted(set(names) - contents.keys()),
        "inventory_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "files": files,
    }
    output.mkdir(parents=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (output / "worktree.patch").write_bytes(diff)
    (output / "status.z").write_bytes(status)
    (output / "index.z").write_bytes(index)
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for item in records:
            content = contents[item.path]
            info = tarfile.TarInfo(item.path)
            info.size = len(content)
            info.mode = item.mode
            archive.addfile(info, io.BytesIO(content))
    return totals


class Arguments(argparse.Namespace):
    root: Path
    output: Path
    context: list[Path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context", type=Path, action="append", default=[])
    arguments = Arguments()
    parser.parse_args(namespace=arguments)
    totals = capture(arguments.root, arguments.output, arguments.context)
    print(json.dumps({"modules": totals, "total": sum(totals.values())}, sort_keys=True))


if __name__ == "__main__":
    main()
