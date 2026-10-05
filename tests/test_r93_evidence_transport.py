"""Independent reconstruction and damaged transport rejection."""

import gzip
import json
from pathlib import Path

import pytest

from scripts import r9_qualification_requirements as freeze
from scripts import r93_evidence_transport as transport
from scripts.r9_qualification_requirements import digest


def test_large_receipt_preserves_original_manifest_and_bytes(tmp_path: Path) -> None:
    payload = b'{"actual_sql":"' + b"x" * (transport.LIMIT * 3) + b'"}'
    original = tmp_path / "read.json"
    original.write_bytes(payload)
    manifest = json.dumps({"attachments": {"read.json": digest(payload)}}).encode()
    (tmp_path / "run.json").write_bytes(manifest)
    assert transport.pack(tmp_path) == 1
    assert not original.exists()
    assert (tmp_path / "run.json").read_bytes() == manifest
    index = freeze.read(tmp_path / transport.INDEX)
    parts = freeze.arr(freeze.obj(freeze.obj(index["attachments"])["read.json"])["parts"])
    compressed = b"".join((tmp_path / str(freeze.obj(part)["path"])).read_bytes() for part in parts)
    assert gzip.decompress(compressed) == payload
    assert transport.pack(tmp_path) == 0
    shard = tmp_path / str(freeze.obj(parts[0])["path"])
    shard.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="shard differs"):
        transport.verify(tmp_path)


def test_attachment_paths_cannot_escape_the_evidence_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="local filename"):
        transport.read_attachment(tmp_path, "../outside.json")
