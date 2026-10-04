"""Verify hashed evidence fragments and their byte-identical gzip archives."""

from __future__ import annotations

import argparse
import gzip
import hashlib
from pathlib import Path
from typing import TypedDict

from pydantic import TypeAdapter


class Attachment(TypedDict):
    path: str
    sha256: str
    bytes: int


class ArchivePart(Attachment):
    pass


class ShardedArchive(TypedDict):
    path: str
    sha256: str
    bytes: int
    json_record_count: int
    json_records_key: str | None
    parts: list[ArchivePart]


class Manifest(TypedDict):
    attachments: list[Attachment]
    sharded_attachments: list[ShardedArchive]


MANIFEST: TypeAdapter[Manifest] = TypeAdapter(Manifest)
JSON_VALUE: TypeAdapter[dict[str, object] | list[object]] = TypeAdapter(
    dict[str, object] | list[object]
)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify(evidence_dir: Path) -> tuple[str, ...]:
    """Check all manifest receipts and reconstruct original archives in memory."""
    manifest = MANIFEST.validate_json((evidence_dir / "manifest.json").read_bytes(), strict=True)
    verified: list[str] = []
    for attachment in manifest["attachments"]:
        raw = (evidence_dir / attachment["path"]).read_bytes()
        if len(raw) != attachment["bytes"] or _digest(raw) != attachment["sha256"]:
            raise ValueError(f"attachment receipt differs: {attachment['path']}")
    for archive in manifest["sharded_attachments"]:
        chunks = []
        for part in archive["parts"]:
            raw = (evidence_dir / part["path"]).read_bytes()
            if len(raw) != part["bytes"] or _digest(raw) != part["sha256"]:
                raise ValueError(f"shard receipt differs: {part['path']}")
            if len(raw) > 800_000:
                raise ValueError(f"shard exceeds the repository file limit: {part['path']}")
            chunks.append(raw)
        original = b"".join(chunks)
        if len(original) != archive["bytes"] or _digest(original) != archive["sha256"]:
            raise ValueError(f"reconstructed archive receipt differs: {archive['path']}")
        value = JSON_VALUE.validate_json(gzip.decompress(original), strict=True)
        key = archive["json_records_key"]
        records = value if key is None else value.get(key) if type(value) is dict else None
        if type(records) is not list:
            raise ValueError(f"reconstructed JSON record shape differs: {archive['path']}")
        count = len(records)
        if count != archive["json_record_count"]:
            raise ValueError(f"reconstructed record count differs: {archive['path']}")
        verified.append(archive["path"])
    return tuple(verified)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_dir", type=Path)
    args = parser.parse_args()
    paths = verify(args.evidence_dir)
    print(f"Verified {len(paths)} sharded archives: {', '.join(paths)}")


if __name__ == "__main__":
    main()
