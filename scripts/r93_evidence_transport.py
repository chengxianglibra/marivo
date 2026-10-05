"""Lossless bounded transport for historical R9.3 invocation attachments."""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path

from scripts import r9_qualification_requirements as freeze

INDEX = "attachment-transport.json"
LIMIT = 512_000


def owned(directory: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or relative.name != name:
        raise ValueError("Attachment must be a local filename")
    path = directory / relative
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Attachment escapes its evidence directory")
    return path


def entries(directory: Path) -> dict[str, freeze.Json]:
    path = directory / INDEX
    if not path.exists():
        return {}
    value = freeze.read(path)
    if value.get("schema") != "marivo.r93.attachment-transport/v1":
        raise ValueError("Unknown attachment transport")
    return freeze.obj(value["attachments"])


def read_attachment(directory: Path, name: str) -> bytes:
    path = owned(directory, name)
    records = entries(directory)
    if name not in records:
        return path.read_bytes()
    record = freeze.obj(records[name])
    packed = bytearray()
    for raw in freeze.arr(record["parts"]):
        part = freeze.obj(raw)
        payload = owned(directory, str(part["path"])).read_bytes()
        if len(payload) != part["bytes"] or freeze.digest(payload) != part["sha256"]:
            raise ValueError("Attachment shard differs from its receipt")
        packed.extend(payload)
    payload = gzip.decompress(bytes(packed))
    if len(payload) != record["bytes"] or freeze.digest(payload) != record["sha256"]:
        raise ValueError("Reconstructed attachment differs from its receipt")
    if path.exists() and path.read_bytes() != payload:
        raise ValueError("Conflicting original and transported attachment")
    return payload


def verify(directory: Path) -> None:
    run = freeze.read(directory / "run.json")
    for name, expected in freeze.obj(run["attachments"]).items():
        if freeze.digest(read_attachment(directory, name)) != expected:
            raise ValueError(f"Original invocation attachment changed: {name}")


def pack(directory: Path) -> int:
    """Preserve the original run manifest and every original attachment digest."""
    verify(directory)
    records = entries(directory)
    originals: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name in {INDEX, "run.json"} or path.stat().st_size <= LIMIT:
            continue
        payload = read_attachment(directory, path.name)
        compressed = gzip.compress(payload, mtime=0)
        parts: list[freeze.Json] = []
        for offset in range(0, len(compressed), LIMIT):
            name = path.name + f".gz.part-{len(parts) + 1:03}"
            target = owned(directory, name)
            chunk = compressed[offset : offset + LIMIT]
            if target.exists() and target.read_bytes() != chunk:
                raise ValueError("Existing attachment shard differs")
            target.write_bytes(chunk)
            parts.append({"path": name, "bytes": len(chunk), "sha256": freeze.digest(chunk)})
        records[path.name] = {
            "bytes": len(payload),
            "sha256": freeze.digest(payload),
            "parts": parts,
        }
        originals.append(path)
    if originals:
        (directory / INDEX).write_bytes(
            freeze.encode({"schema": "marivo.r93.attachment-transport/v1", "attachments": records})
        )
        for path in originals:
            if read_attachment(directory, path.name) != path.read_bytes():
                raise ValueError("Attachment reconstruction failed before replacing original")
            path.unlink()
    verify(directory)
    return len(originals)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pack", "verify"))
    parser.add_argument("directories", type=Path, nargs="+")
    arguments = parser.parse_args()
    for directory in arguments.directories:
        if arguments.action == "pack":
            print(f"{directory}: {pack(directory)} attachments transported")
        else:
            verify(directory)
            print(f"{directory}: original invocation digests verified")
