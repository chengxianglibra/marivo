# Upload HTML report to S3

Use this reference when a materialized HTML report package needs to be shared
outside the local workspace via S3. The `marivo-upload-report` command
supersedes ad-hoc per-session upload scripts and works against both AWS S3
and S3-compatible endpoints (Bilibili BOSS, MinIO, etc.).

For the session-scoped publish API (manifest stamping, content hash, secret
scanning), see `session.save_report(artifact)` and
`session.publish_report(report_id)` in `final-report.md`. This command is
the lighter-weight variant: it uploads
whatever directory it is given, with no manifest contract.

## Command

`marivo-upload-report` — installed alongside the `marivo` package via
`pip install marivo`. Also invocable as
`python -m marivo.analysis.scripts.upload_html_report`.

Implementation lives at
`marivo/analysis/scripts/upload_html_report.py` inside the installed package.

## Prerequisites

- `boto3` available in the venv. Marivo does not declare boto3 as a core
  dependency, so install it explicitly when first using this command:

  ```bash
  pip install boto3
  ```

  `--help` and `--dry-run` work without boto3 installed; only real uploads
  require it.

- AWS credentials configured via env vars or `~/.marivo/secrets.toml`.
- An S3 bucket path (CLI flag, env var, or secrets file).

## Credential sources (priority order)

`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are resolved via
`marivo.analysis.datasources.secrets.resolve()`, which walks env first, then
the user-global plaintext cache:

1. Environment variables `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.
2. Flat top-level keys in `~/.marivo/secrets.toml`.

The `~/.marivo/secrets.toml` schema is **flat top-level quoted strings only**.
The reader drops non-string values, so an `[aws]` table silently fails. The
file mode must be `0o600`:

```bash
chmod 600 ~/.marivo/secrets.toml
```

Example `~/.marivo/secrets.toml`:

```toml
"AWS_ACCESS_KEY_ID" = "AKIAEXAMPLE"
"AWS_SECRET_ACCESS_KEY" = "examplesecretkey"
"AWS_ENDPOINT_URL_S3" = "https://jssz-inner-boss.bilibili.co"
"S3_BUCKET_PATH" = "s3://my-bucket/reports/"
```

## Bucket path sources (priority order)

1. `--bucket-path s3://<bucket>/<key-prefix>/` CLI argument.
2. `S3_BUCKET_PATH` environment variable (same `s3://` URI format).
3. Flat top-level `S3_BUCKET_PATH` key in `~/.marivo/secrets.toml`.

If all three are empty the command exits with code 2 and prints the list of
accepted sources. Trailing slashes are optional; an empty key-prefix uploads
to the bucket root.

## Upload id (collision avoidance)

Every upload inserts an `upload-id` segment into the S3 key path so two
reports that share a name never overwrite each other. The final key for each
file is:

```
s3://<bucket>/<key-prefix>/<upload-id>/<relative-path>
```

The `upload-id` is resolved in this order:

1. `--upload-id <value>` CLI argument.
2. A random 8-char hex token (`os.urandom(4).hex()`).

Pass `--upload-id ""` to disable the segment and upload directly under
`<key-prefix>` (rare; only when you intentionally want to overwrite).

The command prints `upload id: <value>` at the start of every run so the
chosen value is visible in logs and agent transcripts.

## S3-compatible endpoints

Set `AWS_ENDPOINT_URL_S3` to target an S3-compatible service. Resolution
order:

1. `AWS_ENDPOINT_URL_S3` environment variable (boto3-native).
2. Flat top-level `AWS_ENDPOINT_URL_S3` key in `~/.marivo/secrets.toml`.

```bash
export AWS_ENDPOINT_URL_S3=https://jssz-inner-boss.bilibili.co
```

Or in `~/.marivo/secrets.toml`:

```toml
"AWS_ENDPOINT_URL_S3" = "https://jssz-inner-boss.bilibili.co"
```

When resolved, the command applies path-style addressing and the checksum
configuration those services require. When unset, boto3's AWS S3 defaults are
used.

## Usage

Minimal invocation, assuming `S3_BUCKET_PATH` and AWS credentials are already
in the environment or `~/.marivo/secrets.toml`:

```bash
marivo-upload-report .marivo/analysis/sessions/<session>/reports/<report-package>
```

Explicit bucket path on the command line:

```bash
marivo-upload-report \
  .marivo/analysis/sessions/<session>/reports/<report-package> \
  --bucket-path s3://my-bucket/reports/my-report/
```

Preview planned uploads without making any S3 calls (also works without
boto3 installed):

```bash
marivo-upload-report <report_dir> --dry-run
```

Pin a specific upload-id (for traceability, e.g. tying the upload to a
session or report version):

```bash
marivo-upload-report <report_dir> --upload-id sess-7d8bbe31-2026w23
```

If `marivo-upload-report` is not on PATH (e.g. running from a source
checkout without `pip install`), use the module form:

```bash
python -m marivo.analysis.scripts.upload_html_report <report_dir> [--dry-run]
```

## Output

The command prints, in order:

1. `upload id: <value>` — the random or explicit upload-id segment.
2. `uploading N files from <dir> -> s3://<bucket>/<key-prefix>/<upload-id>/`
   (or `dry-run:` prefix when `--dry-run` is set).
3. Per-file status lines:
   - `PUT  <key>  (<n> bytes)` — file was uploaded.
   - `SKIP <key>  (unchanged, <n> bytes)` — file already in S3 with matching
     ETag (MD5) and ContentLength; skipped.
4. `=== verification ===` block (only when the report dir contains an
   `index.html`): `HEAD` result on the uploaded index with ContentLength,
   ContentType, LastModified, and a final `URL:` line.
5. `done: N put, M skipped`.
6. `file urls:` block — one shareable URL per file (PUT or SKIP), sorted by
   key. For S3-compatible endpoints the URL is
   `<endpoint>/<bucket>/<key-prefix>/<upload-id>/<relative-path>`; for AWS S3
   the command prints the `s3://` URI (region is not always known without an
   extra call).

Agents should read the `file urls:` block to obtain the canonical paths of
every uploaded file.

## Idempotency

Re-running the command is safe. Each file's MD5 is compared against the
existing object's ETag (for single-part `put_object`, ETag is the object
MD5). Files whose ETag and ContentLength both match are skipped, so partial
edits to a report package re-upload only what changed.

## Exit codes

- `0` — success, including runs where every file was skipped.
- `2` — config or input error (missing directory, missing credentials,
  missing bucket path, malformed `s3://` URI, boto3 not installed).
- `1` — boto3 runtime error during upload (re-raised after logging).
