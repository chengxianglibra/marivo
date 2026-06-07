from __future__ import annotations

import pytest


def test_filesystem_target_put_and_exists(tmp_path) -> None:
    from marivo.analysis.publish.publish_targets import LocalFilesystemTarget

    target = LocalFilesystemTarget(tmp_path)

    assert target.exists("a/b.json") is False
    target.put_file("a/b.json", b'{"x": 1}')
    assert target.exists("a/b.json") is True
    assert (tmp_path / "a" / "b.json").read_bytes() == b'{"x": 1}'


def test_filesystem_target_uri_is_file_scheme(tmp_path) -> None:
    from marivo.analysis.publish.publish_targets import LocalFilesystemTarget

    target = LocalFilesystemTarget(tmp_path)

    assert target.uri("a/b.json").startswith("file://")


def test_filesystem_target_rejects_path_traversal(tmp_path) -> None:
    from marivo.analysis.publish.publish_targets import LocalFilesystemTarget

    target = LocalFilesystemTarget(tmp_path)

    with pytest.raises(ValueError):
        target.put_file("../escape.json", b"x")


def test_local_filesystem_target_satisfies_protocol(tmp_path) -> None:
    from marivo.analysis.publish.publish_targets import LocalFilesystemTarget, PublishTarget

    assert isinstance(LocalFilesystemTarget(tmp_path), PublishTarget)
