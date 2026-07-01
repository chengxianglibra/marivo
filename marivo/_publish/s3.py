"""S3 upload primitives for the private CLI publisher."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

from marivo._publish.config import PublishConfig


class S3Client(Protocol):
    def put_object(self, **kwargs: object) -> object: ...


@dataclass(frozen=True)
class PublishResult:
    uri: str
    url: str
    file_count: int


def _is_html_path(rel_path: str) -> bool:
    return rel_path.lower().endswith((".html", ".htm"))


def default_s3_client_factory(
    service_name: str,
    *,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    endpoint_url: str,
    config: object | None,
) -> S3Client:
    """Create a boto3 S3 client, importing boto3 only when publish runs."""

    boto3 = import_module("boto3")
    return cast(
        "S3Client",
        boto3.client(
            service_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            endpoint_url=endpoint_url,
            config=default_botocore_config() if config is None else config,
        ),
    )


def default_botocore_config() -> object:
    """Return the botocore Config for S3-compatible endpoints."""

    config_module = import_module("botocore.config")
    config_class = config_module.Config
    return config_class(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )


class S3Uploader:
    """Upload byte payloads under a configured S3 prefix."""

    def __init__(
        self,
        config: PublishConfig,
        *,
        client_factory: Callable[..., S3Client] = default_s3_client_factory,
        botocore_config: object | None = None,
    ) -> None:
        self._config = config
        self._client = client_factory(
            "s3",
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
            endpoint_url=config.endpoint_url,
            config=botocore_config,
        )

    @property
    def root_uri(self) -> str:
        return self._config.root_uri

    def uri(self, rel_path: str) -> str:
        return f"s3://{self._config.bucket}/{self._key(rel_path)}"

    def url(self, rel_path: str) -> str:
        return self._config.url(rel_path)

    def put_file(self, rel_path: str, data: bytes) -> str:
        key = self._key(rel_path)
        put_kwargs: dict[str, object] = {
            "Bucket": self._config.bucket,
            "Key": key,
            "Body": data,
            "ContentLength": len(data),
        }
        if _is_html_path(rel_path):
            put_kwargs["ContentType"] = "text/html"
        self._client.put_object(**put_kwargs)
        return f"s3://{self._config.bucket}/{key}"

    def _key(self, rel_path: str) -> str:
        clean_rel = rel_path.replace("\\", "/").strip("/")
        parts = [part for part in clean_rel.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise ValueError(f"publish path is not a safe relative object path: {rel_path!r}")
        prefix = self._config.prefix.strip("/")
        return "/".join([prefix, *parts]) if prefix else "/".join(parts)
