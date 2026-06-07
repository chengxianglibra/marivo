from __future__ import annotations

from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_clean_package_finds_nothing(tmp_path) -> None:
    from marivo.analysis.publish.publish_secrets import scan_package_for_secrets

    _write(tmp_path / "report_spec.json", '{"title": "ok"}\n')

    assert scan_package_for_secrets(tmp_path) == ()


def test_scan_detects_aws_key(tmp_path) -> None:
    from marivo.analysis.publish.publish_secrets import scan_package_for_secrets

    _write(tmp_path / "datasets" / "d.json", '{"k": "AKIAIOSFODNN7EXAMPLE"}\n')
    issues = scan_package_for_secrets(tmp_path)

    assert len(issues) == 1
    assert issues[0].check == "aws_key"
    assert issues[0].rel_path == "datasets/d.json"


def test_scan_detects_secret_assignment(tmp_path) -> None:
    from marivo.analysis.publish.publish_secrets import scan_package_for_secrets

    _write(tmp_path / "replay.py", 'password = "hunter2xyz"\n')
    issues = scan_package_for_secrets(tmp_path)

    assert any(issue.check == "secret_kv" for issue in issues)
    assert issues[0].rel_path == "replay.py"


def test_scan_ignores_binary_and_unknown_suffixes(tmp_path) -> None:
    from marivo.analysis.publish.publish_secrets import scan_package_for_secrets

    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "snapshot.parquet").write_bytes(b"AKIAIOSFODNN7EXAMPLE")

    assert scan_package_for_secrets(tmp_path) == ()
