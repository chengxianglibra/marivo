from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest


class _FakeS3Client:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, object]:
        self.puts.append(kwargs)
        return {}


class _FakeClientFactory:
    def __init__(self) -> None:
        self.client = _FakeS3Client()
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        service_name: str,
        *,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        endpoint_url: str,
        config: object,
    ) -> _FakeS3Client:
        self.calls.append(
            {
                "service_name": service_name,
                "aws_access_key_id": aws_access_key_id,
                "aws_secret_access_key": aws_secret_access_key,
                "endpoint_url": endpoint_url,
                "config": config,
            }
        )
        return self.client


def _write_project_config(root: Path, bucket_path: str = "s3://bucket/base") -> None:
    (root / "marivo.toml").write_text(
        "[project]\n"
        'name = "demo"\n\n'
        "[publish.s3]\n"
        f'S3_BUCKET_PATH = "{bucket_path}"\n'
        'AWS_ENDPOINT_URL_S3 = "https://s3.example.com"\n',
        encoding="utf-8",
    )


def _write_secret_config(home: Path, *, access_key: str = "ak", secret_key: str = "sk") -> None:
    path = home / ".marivo" / "secrets.toml"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(
        "[publish.s3]\n"
        f'AWS_ACCESS_KEY_ID = "{access_key}"\n'
        f'AWS_SECRET_ACCESS_KEY = "{secret_key}"\n',
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_resolves_s3_publish_config_from_project_and_user_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._publish.config import resolve_s3_publish_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_project_config(tmp_path)
    _write_secret_config(home)

    config = resolve_s3_publish_config(project_root=tmp_path)

    assert config.bucket == "bucket"
    assert config.prefix == "base"
    assert config.endpoint_url == "https://s3.example.com"
    assert config.aws_access_key_id == "ak"
    assert config.aws_secret_access_key == "sk"


def test_missing_config_error_names_key_and_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._publish.config import PublishConfigError, resolve_s3_publish_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    _write_secret_config(home)

    with pytest.raises(PublishConfigError) as exc_info:
        resolve_s3_publish_config(project_root=tmp_path)

    message = str(exc_info.value)
    assert "marivo.toml [publish.s3] S3_BUCKET_PATH" in message
    assert str(tmp_path / "marivo.toml") in message


def test_missing_secret_error_names_key_and_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._publish.config import PublishConfigError, resolve_s3_publish_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_project_config(tmp_path)

    with pytest.raises(PublishConfigError) as exc_info:
        resolve_s3_publish_config(project_root=tmp_path)

    message = str(exc_info.value)
    assert "~/.marivo/secrets.toml [publish.s3] AWS_ACCESS_KEY_ID" in message
    assert str(home / ".marivo" / "secrets.toml") in message


def test_secret_file_with_loose_permissions_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._publish.config import PublishConfigError, resolve_s3_publish_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_project_config(tmp_path)
    _write_secret_config(home)
    (home / ".marivo" / "secrets.toml").chmod(0o644)

    with pytest.raises(PublishConfigError) as exc_info:
        resolve_s3_publish_config(project_root=tmp_path)

    assert "expected 0o600" in str(exc_info.value)


def test_malformed_bucket_path_fails_before_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._publish.config import PublishConfigError, resolve_s3_publish_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_project_config(tmp_path, bucket_path="https://not-s3/path")
    _write_secret_config(home)

    with pytest.raises(PublishConfigError) as exc_info:
        resolve_s3_publish_config(project_root=tmp_path)

    assert "S3_BUCKET_PATH must start with s3://" in str(exc_info.value)


def test_publish_file_uploads_basename_and_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._publish.static import publish_path

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_project_config(tmp_path)
    _write_secret_config(home)
    source = tmp_path / "report.html"
    source.write_text("<h1>report</h1>", encoding="utf-8")
    factory = _FakeClientFactory()

    result = publish_path(source, project_root=tmp_path, client_factory=factory)

    assert result.uri == "s3://bucket/base/report.html"
    assert result.file_count == 1
    assert factory.calls[0]["service_name"] == "s3"
    assert factory.calls[0]["endpoint_url"] == "https://s3.example.com"
    put = factory.client.puts[0]
    assert put["Bucket"] == "bucket"
    assert put["Key"] == "base/report.html"
    assert put["Body"] == b"<h1>report</h1>"
    assert put["ContentLength"] == len(b"<h1>report</h1>")
    assert put["ContentType"] == "text/html"


def test_publish_directory_preserves_nested_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo._publish.static import publish_path

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_project_config(tmp_path)
    _write_secret_config(home)
    source = tmp_path / "output"
    (source / "assets").mkdir(parents=True)
    (source / "index.html").write_text("index", encoding="utf-8")
    (source / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    factory = _FakeClientFactory()

    result = publish_path(source, project_root=tmp_path, client_factory=factory)

    assert result.uri == "s3://bucket/base/output"
    assert result.file_count == 2
    keys = sorted(put["Key"] for put in factory.client.puts)
    assert keys == ["base/output/assets/app.js", "base/output/index.html"]
    puts_by_key = {put["Key"]: put for put in factory.client.puts}
    assert puts_by_key["base/output/index.html"]["ContentType"] == "text/html"
    assert "ContentType" not in puts_by_key["base/output/assets/app.js"]


def test_publish_html_content_type_matches_case_insensitive_suffix() -> None:
    from marivo._publish.s3 import PublishConfig, S3Uploader

    factory = _FakeClientFactory()
    uploader = S3Uploader(
        PublishConfig(
            bucket="bucket",
            prefix="base",
            endpoint_url="https://s3.example.com",
            aws_access_key_id="ak",
            aws_secret_access_key="sk",
        ),
        client_factory=factory,
    )

    uploader.put_file("REPORT.HTM", b"<h1>report</h1>")

    put = factory.client.puts[0]
    assert put["Key"] == "base/REPORT.HTM"
    assert put["ContentType"] == "text/html"


def test_publish_rejects_path_traversal_segments(tmp_path: Path) -> None:
    from marivo._publish.s3 import PublishConfig, S3Uploader

    uploader = S3Uploader(
        PublishConfig(
            bucket="bucket",
            prefix="base",
            endpoint_url="https://s3.example.com",
            aws_access_key_id="ak",
            aws_secret_access_key="sk",
        ),
        client_factory=_FakeClientFactory(),
    )

    with pytest.raises(ValueError):
        uploader.put_file("../escape.txt", b"x")


def test_secret_fixture_writes_owner_only_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_secret_config(home)

    assert stat.S_IMODE((home / ".marivo" / "secrets.toml").stat().st_mode) == 0o600
