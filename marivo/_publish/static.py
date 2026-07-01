"""Upload files or directories to the configured S3 publish prefix."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from marivo._publish.config import resolve_s3_publish_config
from marivo._publish.s3 import PublishResult, S3Client, S3Uploader, default_s3_client_factory


def _iter_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(item for item in path.rglob("*") if item.is_file())


def publish_path(
    path: str | Path,
    *,
    project_root: str | Path | None = None,
    client_factory: Callable[..., S3Client] = default_s3_client_factory,
) -> PublishResult:
    """Upload a file or directory without inspecting its content."""

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"publish path does not exist: {source}")
    config = resolve_s3_publish_config(project_root=project_root)
    uploader = S3Uploader(config, client_factory=client_factory)

    files = _iter_files(source)
    if source.is_file():
        for file_path in files:
            uploader.put_file(file_path.name, file_path.read_bytes())
        return PublishResult(
            uri=uploader.uri(source.name),
            url=uploader.url(source.name),
            file_count=len(files),
        )

    root_name = source.name
    for file_path in files:
        rel = file_path.relative_to(source).as_posix()
        uploader.put_file(f"{root_name}/{rel}", file_path.read_bytes())
    return PublishResult(
        uri=uploader.uri(root_name),
        url=uploader.url(root_name),
        file_count=len(files),
    )
