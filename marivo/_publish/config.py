"""Resolve S3 publish configuration for the private CLI uploader."""

from __future__ import annotations

import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from marivo.config import PROJECT_MANIFEST
from marivo.project import resolve_project_root

_INSECURE_SECRET_FILE_BITS = stat.S_IRWXG | stat.S_IRWXO
_S3_SECTION = "[publish.s3]"
_PROJECT_BUCKET_KEY = "S3_BUCKET_PATH"
_PROJECT_ENDPOINT_KEY = "AWS_ENDPOINT_URL_S3"
_SECRET_ACCESS_KEY_ID = "AWS_ACCESS_KEY_ID"
_SECRET_ACCESS_KEY = "AWS_SECRET_ACCESS_KEY"


class PublishConfigError(Exception):
    """Raised when CLI S3 publish configuration cannot be resolved."""


@dataclass(frozen=True)
class PublishConfig:
    """Resolved S3 publish configuration for the CLI uploader."""

    bucket: str
    prefix: str
    endpoint_url: str
    aws_access_key_id: str
    aws_secret_access_key: str

    @property
    def root_uri(self) -> str:
        suffix = f"/{self.prefix}" if self.prefix else ""
        return f"s3://{self.bucket}{suffix}"

    def url(self, rel_path: str) -> str:
        clean_rel = rel_path.replace("\\", "/").strip("/")
        parts = [part for part in clean_rel.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise ValueError(f"publish path is not a safe relative object path: {rel_path!r}")
        prefix = self.prefix.strip("/")
        key = "/".join([prefix, *parts]) if prefix else "/".join(parts)
        return f"{self.endpoint_url.rstrip('/')}/{self.bucket}/{key}"


def _missing_message(display_path: str, actual_path: Path, key: str) -> str:
    return f"missing required configuration {display_path} {_S3_SECTION} {key} at {actual_path}"


def _read_toml(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        parsed = tomllib.load(handle)
    return parsed


def _s3_table(path: Path) -> dict[str, object]:
    parsed = _read_toml(path)
    publish = parsed.get("publish")
    if not isinstance(publish, dict):
        return {}
    s3 = publish.get("s3")
    if not isinstance(s3, dict):
        return {}
    return s3


def _required_string(
    table: dict[str, object],
    *,
    key: str,
    display_path: str,
    actual_path: Path,
) -> str:
    value = table.get(key)
    if not isinstance(value, str) or value == "":
        raise PublishConfigError(_missing_message(display_path, actual_path, key))
    return value


def _assert_secret_file_permissions(path: Path) -> None:
    if not path.exists():
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & _INSECURE_SECRET_FILE_BITS:
        raise PublishConfigError(
            f"secret store {path} has insecure permissions {oct(mode)}; expected 0o600"
        )


def _parse_bucket_path(bucket_path: str, manifest_path: Path) -> tuple[str, str]:
    parsed = urlparse(bucket_path)
    if parsed.scheme != "s3":
        raise PublishConfigError(
            f"{PROJECT_MANIFEST} {_S3_SECTION} {_PROJECT_BUCKET_KEY} must start with s3:// "
            f"at {manifest_path}"
        )
    if not parsed.netloc:
        raise PublishConfigError(
            f"{PROJECT_MANIFEST} {_S3_SECTION} {_PROJECT_BUCKET_KEY} must include a bucket "
            f"at {manifest_path}"
        )
    return parsed.netloc, parsed.path.strip("/")


def resolve_s3_publish_config(project_root: str | Path | None = None) -> PublishConfig:
    """Resolve S3 publish config from ``marivo.toml`` and user secrets."""

    root = resolve_project_root(Path(project_root) if project_root is not None else None)
    manifest_path = root / PROJECT_MANIFEST
    secret_path = Path.home() / ".marivo" / "secrets.toml"

    project_table = _s3_table(manifest_path)
    bucket_path = _required_string(
        project_table,
        key=_PROJECT_BUCKET_KEY,
        display_path=PROJECT_MANIFEST,
        actual_path=manifest_path,
    )
    endpoint_url = _required_string(
        project_table,
        key=_PROJECT_ENDPOINT_KEY,
        display_path=PROJECT_MANIFEST,
        actual_path=manifest_path,
    )

    _assert_secret_file_permissions(secret_path)
    secret_table = _s3_table(secret_path)
    access_key_id = _required_string(
        secret_table,
        key=_SECRET_ACCESS_KEY_ID,
        display_path="~/.marivo/secrets.toml",
        actual_path=secret_path,
    )
    secret_access_key = _required_string(
        secret_table,
        key=_SECRET_ACCESS_KEY,
        display_path="~/.marivo/secrets.toml",
        actual_path=secret_path,
    )

    bucket, prefix = _parse_bucket_path(bucket_path, manifest_path)
    return PublishConfig(
        bucket=bucket,
        prefix=prefix,
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    )
