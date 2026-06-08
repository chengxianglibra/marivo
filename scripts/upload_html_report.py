#!/usr/bin/env python3
"""Upload a local report package directory to an S3 bucket.

Required argument:
  report_dir            Local directory whose contents are uploaded recursively.

Optional arguments:
  --bucket-path PATH    S3 destination as s3://<bucket>/<key-prefix>/.
  --upload-id ID        Identifier inserted into the key path to avoid collisions
                        between reports that share a name. Defaults to a random
                        8-char hex token. Pass an empty string to disable.
  --dry-run             Resolve config and list planned uploads without any S3 calls.

AWS credentials are resolved in this order:
  1. Environment: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
  2. User-global plaintext cache: ~/.marivo/secrets.toml (flat top-level string keys)

The S3 bucket path is resolved in this order:
  1. --bucket-path CLI argument
  2. Environment: S3_BUCKET_PATH
  3. User-global plaintext cache: ~/.marivo/secrets.toml

For S3-compatible endpoints (Bilibili BOSS, MinIO, etc.) set
AWS_ENDPOINT_URL_S3 (env var, boto3-native) or the same key in
~/.marivo/secrets.toml; the script then applies path-style addressing and the
checksum configuration those services require. Without that, the default AWS S3
endpoint and boto3 defaults are used.

The final S3 key for each uploaded file is
``s3://<bucket>/<key-prefix>/<upload-id>/<relative-path>``. The upload-id
segment defaults to a random 8-char hex token so two reports with the same name
never overwrite each other; override with --upload-id.

Files already present in S3 with a matching ETag (MD5) and ContentLength are
skipped, so re-running the script after partial edits re-uploads only what
changed.

After the upload the script prints the public URL of every file so agents can
read and share them without reconstructing paths.

This script contains no hardcoded secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from marivo.analysis.datasources import secrets
from marivo.analysis.errors import DatasourceEnvVarMissingError

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
_MISSING_HEAD_CODES = {"404", "NoSuchKey"}


def content_type_for(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix, _DEFAULT_CONTENT_TYPE)


def parse_bucket_path(raw: str) -> tuple[str, str]:
    """Split an s3://bucket/key/prefix string into (bucket, key_prefix)."""
    value = raw.strip()
    if not value.startswith("s3://"):
        raise ValueError(f"bucket path must start with s3:// (got: {raw!r})")
    rest = value[len("s3://") :].lstrip("/")
    if not rest:
        raise ValueError(f"bucket path has empty bucket name (got: {raw!r})")
    parts = rest.split("/", 1)
    bucket = parts[0]
    key_prefix = parts[1].rstrip("/") if len(parts) == 2 else ""
    if not bucket:
        raise ValueError(f"bucket path has empty bucket name (got: {raw!r})")
    return bucket, key_prefix


def resolve_optional_secret(name: str) -> str | None:
    try:
        return secrets.resolve(name).value
    except DatasourceEnvVarMissingError:
        return None


def resolve_bucket_path(cli_value: str | None) -> tuple[str, str]:
    raw = cli_value or resolve_optional_secret("S3_BUCKET_PATH")
    if not raw:
        print(
            "ERROR: S3 bucket path not configured. Set one of: "
            "--bucket-path, S3_BUCKET_PATH env var, or S3_BUCKET_PATH in "
            "~/.marivo/secrets.toml",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        return parse_bucket_path(raw)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def build_full_prefix(key_prefix: str, upload_id: str) -> str:
    """Join the bucket key prefix and upload id into the S3 key prefix used
    for all uploaded files. Empty segments are dropped, so an empty upload_id
    disables the random segment and an empty key_prefix uploads to the bucket
    root under the upload id."""
    segments = [s for s in (key_prefix, upload_id) if s]
    return "/".join(segments)


def format_public_url(bucket: str, key: str, endpoint_url: str | None) -> str:
    """Build the shareable URL for an object. Uses endpoint_url when set
    (S3-compatible services); otherwise returns the s3:// URI for AWS S3,
    whose HTTPS form depends on region."""
    if endpoint_url:
        return f"{endpoint_url.rstrip('/')}/{bucket}/{key}"
    return f"s3://{bucket}/{key}"


def build_s3_client(endpoint_url: str | None) -> object:
    """Construct the boto3 S3 client. boto3 is imported lazily so --help and
    --dry-run work without it installed."""
    try:
        import boto3
        from botocore.client import Config
    except ImportError as exc:
        print(
            "ERROR: boto3 is required for S3 upload. Install with: .venv/bin/pip install boto3",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    access_key = secrets.resolve("AWS_ACCESS_KEY_ID", datasource="s3", field="access_key_id").value
    secret_key = secrets.resolve(
        "AWS_SECRET_ACCESS_KEY", datasource="s3", field="secret_access_key"
    ).value

    config_kwargs: dict[str, object] = {}
    if endpoint_url:
        config_kwargs = {
            "signature_version": "s3v4",
            "s3": {"addressing_style": "path"},
            "request_checksum_calculation": "when_required",
            "response_checksum_validation": "when_required",
        }
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(**config_kwargs),
    )


def s3_key_for(s3_prefix: str, path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return f"{s3_prefix}/{relative}" if s3_prefix else relative


def upload_is_unchanged(client: object, bucket: str, key: str, body: bytes) -> bool:
    from botocore.exceptions import ClientError

    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _MISSING_HEAD_CODES:
            return False
        raise
    etag = str(head.get("ETag", "")).strip('"')
    size = head.get("ContentLength")
    return etag == hashlib.md5(body).hexdigest() and size == len(body)


def plan_uploads(report_dir: Path) -> list[Path]:
    return sorted(p for p in report_dir.rglob("*") if p.is_file())


def run_upload(
    client: object,
    files: list[Path],
    report_dir: Path,
    bucket: str,
    s3_prefix: str,
) -> tuple[int, int]:
    put_count = 0
    skip_count = 0
    for path in files:
        key = s3_key_for(s3_prefix, path, report_dir)
        body = path.read_bytes()
        if upload_is_unchanged(client, bucket, key, body):
            print(f"  SKIP {key}  (unchanged, {len(body)} bytes)")
            skip_count += 1
            continue
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type_for(path),
        )
        print(f"  PUT  {key}  ({len(body)} bytes)")
        put_count += 1
    return put_count, skip_count


def print_file_urls(
    files: list[Path],
    report_dir: Path,
    bucket: str,
    s3_prefix: str,
    endpoint_url: str | None,
) -> None:
    print()
    print("file urls:")
    for path in files:
        key = s3_key_for(s3_prefix, path, report_dir)
        print(f"  {format_public_url(bucket, key, endpoint_url)}")


def print_dry_run(
    files: list[Path],
    report_dir: Path,
    bucket: str,
    s3_prefix: str,
    endpoint_url: str | None,
    upload_id: str,
) -> None:
    destination = f"s3://{bucket}/{s3_prefix + '/' if s3_prefix else ''}"
    print(f"upload id: {upload_id}")
    print(f"dry-run: {len(files)} files would be uploaded")
    print(f"  from: {report_dir}")
    print(f"  to:   {destination}")
    for path in files:
        key = s3_key_for(s3_prefix, path, report_dir)
        print(f"  PUT  {key}  ({path.stat().st_size} bytes)")
    print_file_urls(files, report_dir, bucket, s3_prefix, endpoint_url)


def verify_index(
    client: object,
    bucket: str,
    s3_prefix: str,
    endpoint_url: str | None,
) -> int:
    from botocore.exceptions import ClientError

    index_key = f"{s3_prefix}/index.html" if s3_prefix else "index.html"
    try:
        head = client.head_object(Bucket=bucket, Key=index_key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        print(
            f"WARNING: index.html verification failed ({code}); report may not have an index.html",
            file=sys.stderr,
        )
        return 0
    print()
    print("=== verification ===")
    print(f"HEAD s3://{bucket}/{index_key}")
    print(f"  ContentLength: {head.get('ContentLength')}")
    print(f"  ContentType:   {head.get('ContentType')}")
    print(f"  LastModified:  {head.get('LastModified')}")
    print(f"  URL: {format_public_url(bucket, index_key, endpoint_url)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload a report package directory to S3.",
    )
    parser.add_argument(
        "report_dir",
        type=Path,
        help="Local directory whose contents are uploaded recursively.",
    )
    parser.add_argument(
        "--bucket-path",
        default=None,
        help="S3 destination as s3://<bucket>/<key-prefix>/. "
        "Falls back to S3_BUCKET_PATH env var, then ~/.marivo/secrets.toml.",
    )
    parser.add_argument(
        "--upload-id",
        default=None,
        help="Identifier inserted into the S3 key path to avoid name "
        "collisions between reports. Defaults to a random 8-char hex token. "
        "Pass an empty string to disable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve config and list planned uploads without any S3 calls.",
    )
    args = parser.parse_args()

    report_dir: Path = args.report_dir
    if not report_dir.is_dir():
        print(
            f"ERROR: report directory not found or not a directory: {report_dir}", file=sys.stderr
        )
        return 2

    bucket, key_prefix = resolve_bucket_path(args.bucket_path)
    endpoint_url = os.environ.get("AWS_ENDPOINT_URL_S3") or resolve_optional_secret(
        "AWS_ENDPOINT_URL_S3"
    )
    upload_id = args.upload_id if args.upload_id is not None else os.urandom(4).hex()
    s3_prefix = build_full_prefix(key_prefix, upload_id)
    files = plan_uploads(report_dir)

    if not files:
        print(f"WARNING: no files under {report_dir}", file=sys.stderr)
        return 0

    if args.dry_run:
        print_dry_run(files, report_dir, bucket, s3_prefix, endpoint_url, upload_id)
        return 0

    client = build_s3_client(endpoint_url)
    destination = f"s3://{bucket}/{s3_prefix + '/' if s3_prefix else ''}"
    print(f"upload id: {upload_id}")
    print(f"uploading {len(files)} files from {report_dir} -> {destination}")
    put_count, skip_count = run_upload(client, files, report_dir, bucket, s3_prefix)

    has_index = any(p.name == "index.html" for p in files)
    if has_index:
        verify_index(client, bucket, s3_prefix, endpoint_url)

    print()
    print(f"done: {put_count} put, {skip_count} skipped")
    print_file_urls(files, report_dir, bucket, s3_prefix, endpoint_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
