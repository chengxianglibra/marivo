from __future__ import annotations

from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_compute_package_hash_is_deterministic(tmp_path) -> None:
    from marivo.analysis.publish.publish_hash import compute_package_hash

    _write(tmp_path / "report_spec.json", '{"a": 1}\n')
    _write(tmp_path / "datasets" / "d.json", "[]\n")

    first = compute_package_hash(tmp_path)
    second = compute_package_hash(tmp_path)

    assert first == second
    assert first.startswith("sha256:")


def test_compute_package_hash_changes_with_content(tmp_path) -> None:
    from marivo.analysis.publish.publish_hash import compute_package_hash

    _write(tmp_path / "report_spec.json", '{"a": 1}\n')
    before = compute_package_hash(tmp_path)
    _write(tmp_path / "report_spec.json", '{"a": 2}\n')
    after = compute_package_hash(tmp_path)

    assert before != after


def test_compute_package_hash_excludes_manifest(tmp_path) -> None:
    from marivo.analysis.publish.publish_hash import compute_package_hash

    _write(tmp_path / "report_spec.json", '{"a": 1}\n')
    without_manifest = compute_package_hash(tmp_path)
    _write(tmp_path / "manifest.json", '{"kind": "x"}\n')
    with_manifest = compute_package_hash(tmp_path)

    assert without_manifest == with_manifest
