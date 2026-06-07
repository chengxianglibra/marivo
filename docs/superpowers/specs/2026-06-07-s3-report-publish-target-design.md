# S3 Report Publish Target — Public-Read HTTPS Sharing

Status: Approved design — revised after agent-usage review (ready for plan)
Parent spec: `docs/superpowers/specs/2026-06-01-semantic-report-publishing-design.md`
Prior phase: `docs/superpowers/plans/2026-06-06-report-artifact-publish-integration.md`

## Problem

`marivo.analysis.publish.report_package` already validates and uploads a staged
report package to a `PublishTarget`, but the only concrete target is
`LocalFilesystemTarget`. The parent spec specifies the S3 layout, config keys,
and credential chain, but leaves three things undecided that block real sharing:

1. **How an uploaded object becomes readable by a recipient**, and what URL they
   open. The parent spec only shows `s3://...` paths, which are not openable in a
   browser and are not what a non-AWS recipient can use.
2. **Whether the HTML actually renders** when opened (Content-Type).
3. **The concrete `PublishTarget` implementation, packaging, and errors.**

The report HTML is intentionally self-contained (parent spec: "HTML can be read
without importing Marivo or contacting S3 APIs"), so the only artifact a sharer
needs to hand out is one URL to `index.html`. This doc defines how that URL is
produced and how the object is made readable.

## Scope

In scope: a thin `S3PublishTarget`, Content-Type tagging, S3 config resolution
and backend dispatch, the shareable link on the result, boto3 packaging, one new
error type, and the publish-identity model (library-owned random `report_id`, no
overwrite).

Unchanged: validation rules, secret scan, data-policy gate, hashing, attribution,
HTML generation, the content-first/manifest-last upload ordering, and the semantic
release path. Deferred: signed/expiring URLs, CloudFront, per-recipient access
control.

## Decisions

Confirmed in the original design (1–4):

1. **Access model: public-read via a one-time bucket policy.** The library does
   **not** set per-object ACLs. Recipients open a permanent virtual-hosted
   **HTTPS** URL. Rationale below.
2. **boto3 is an optional extra `marivo[s3]`**, lazy-imported. The end-user core
   install stays AWS-free.
3. **The shareable link is `entrypoint_uri`** on `PublishReportResult`; the
   existing `uri` keeps meaning "package prefix." It is always populated for
   analysis report publishes (see Decision 8), so its type is `str`, not
   `str | None`.
4. **When `target=None`, S3 is selected only when a bucket is configured**
   (`[storage.s3].bucket` or `MARIVO_PUBLISH_S3_BUCKET`), and it then takes
   precedence over `[storage.local]`. Other S3 variables (region, profile,
   endpoint) only decorate an already-selected S3 target — they never trigger S3
   on their own, so a stray `MARIVO_PUBLISH_S3_REGION` cannot break a local
   publish. Explicit `target` (an `s3://` string, a dir string, or a
   `PublishTarget` instance) always wins over config.

   **Behavioral change (call out in the parent spec):** today
   `resolve_publish_config(target=None)` only ever resolves local. After this
   change, configuring an S3 **bucket** flips a `target=None` publish to S3 even
   when `[storage.local]` is still present. To keep publishing locally, pass an
   explicit dir `target=` or remove the S3 bucket config. Deliberate, documented.

Refined after the agent-usage review (5–8):

5. **Every publish is a new immutable report — there is no overwrite.** The
   library mints a fresh random `report_id` per publish; the path carries it; a
   re-publish produces a *new* URL, not a replacement. The `overwrite` parameter,
   the existence precondition, and the `ReportPublishTargetExistsError` raise are
   **removed** from the publish flow. (Replaces the earlier overwrite/collision
   handling.)

6. **`report_id` is library-generated, not agent-generated** (the field name is
   kept). It is a URL-safe random token stamped into the manifest at publish —
   exactly like `exported_at`/`content_hash` are stamped today — and is injectable
   via an optional `report_id=` kwarg for tests. The publish path becomes
   `…/analysis-reports/<report_id>/`, and **`export_id` is removed from the
   manifest model entirely.** `export_id` only existed to group multiple exports
   under one logical report, which Decision 5 abolishes; `content_hash` recovers
   "same content across publishes" if lineage is ever needed. Because the library
   owns the token, share URLs are always clean and URL-safe (no agent-produced
   spaces/`#`/`?`).

7. **The whole package is uploaded verbatim and is world-readable; all report
   content is treated as public-shareable.** Per project policy, narrative,
   evidence rows, SQL, table/datasource names, and frame parquet are all OK to be
   public; the **only** disallowed content is secrets, enforced by the existing
   secret scanner over packaged text files (and the HTML never carries secrets by
   construction). There is no publish-time content gate, stripping, or
   row-omission requirement. The S3 layout is byte-for-byte identical to the local
   package directory, and the HTML is self-contained (Decision 9), so the same
   bytes serve from `file://` and `https://`.

8. **`entrypoint_uri` is always present for report publishes.** Parent-spec
   validation requires `index.html`, and the manifest always carries the `html`
   entrypoint, so the orchestrator can always resolve a link. (Makes Decision 3's
   type `str`.)

9. **The HTML is self-contained, so "relative addressing" is satisfied by
   construction.** `render_report_html` inlines CSS (`<style>`), JS (`<script>`),
   and the report data (`<script type="application/json" id="marivo-report-data">`);
   the only links are intra-page `#anchors`. `index.html` references no external,
   CDN, or sibling files, so it renders identically from local and public S3 with
   zero dependencies. Sibling files (`flow.json`, `grounding.json`, `replay.py`,
   `frames/`) are provenance/replay only and are not needed to view the report.

**On public reachability (review finding F1, intentionally not handled):** a
successful publish means the bytes are stored and addressable — **not** that the
bucket policy makes them readable. The library neither grants nor verifies public
access; that is the operator's responsibility (Operational prerequisite). No
reachability probe or `public_readable` field is added.

### Why public-read by bucket policy, not per-object ACL

Modern S3 buckets default to **ACLs disabled (bucket-owner-enforced)** and
**Block Public Access on**. Setting `ACL=public-read` per object fails on such
buckets and pushes object-by-object access decisions into the upload path. A
single bucket policy that grants `s3:GetObject` on the publish prefix is the
supported, auditable mechanism and keeps the uploader a dumb writer. The library
therefore writes objects with **no ACL argument** and relies on the bucket being
pre-configured for public read on the prefix.

Tradeoff accepted by the user: anyone with the URL can read the object until it
is deleted or the policy changes. Mitigation is data minimization, not access
control — see Public-safe package invariant.

## Component design

### 1. `S3PublishTarget` (in `marivo/analysis/publish/publish_targets.py`)

Implements the existing `PublishTarget` protocol, so the orchestrator's
`isinstance(target, PublishTarget)` branch already accepts an instance passed as
`target=`. The publish flow now calls only `put_file`/`uri` (the `exists`
precondition is gone with overwrite); `exists` is kept for protocol parity but is
unused by publishing.

```python
class S3PublishTarget:
    """Write package files as public-read S3 objects; serve them over HTTPS."""

    def __init__(
        self,
        bucket: str,
        *,
        region: str | None = None,
        endpoint_url: str | None = None,
        profile: str | None = None,
        client: object | None = None,  # injected in tests; no live AWS
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._endpoint_url = endpoint_url or None
        self._client = client or _make_s3_client(region, self._endpoint_url, profile)

    def uri(self, rel_path: str) -> str:
        # Percent-encode for the HTTP(S) URL while put/exists keep the RAW key.
        # quote(safe="/") preserves separators and round-trips back to the stored
        # key (space -> %20 -> space on GET), so the encoded URL still resolves.
        key = quote(rel_path.lstrip("/"), safe="/")
        if self._endpoint_url:  # MinIO / custom: path-style
            return f"{self._endpoint_url.rstrip('/')}/{self._bucket}/{key}"
        host = (
            f"{self._bucket}.s3.{self._region}.amazonaws.com"
            if self._region
            else f"{self._bucket}.s3.amazonaws.com"
        )
        return f"https://{host}/{key}"

    def put_file(self, rel_path: str, data: bytes) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            # AWS verifies this checksum server-side on PUT and rejects a
            # corrupted/truncated upload; omitted for custom endpoints whose
            # checksum support varies (see Remote verification).
            integrity = {} if self._endpoint_url else {"ChecksumAlgorithm": "SHA256"}
            self._client.put_object(
                Bucket=self._bucket,
                Key=rel_path.lstrip("/"),
                Body=data,
                ContentType=content_type_for(rel_path),
                **integrity,
            )  # NOTE: no ACL= — public read comes from the bucket policy
        except (BotoCoreError, ClientError) as exc:
            raise ReportPublishUploadError(
                message=f"failed to upload S3 object: {rel_path}",
                details={"bucket": self._bucket, "key": rel_path, "reason": _s3_reason(exc)},
            ) from exc

    def exists(self, rel_path: str) -> bool:  # retained for protocol parity; unused by publish
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=rel_path.lstrip("/"))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise ReportPublishUploadError(
                message=f"failed to stat S3 object: {rel_path}",
                details={"bucket": self._bucket, "key": rel_path, "reason": _s3_reason(exc)},
            ) from exc
        return True
```

`uri()` percent-encodes the key via `urllib.parse.quote(key, safe="/")` (add
`from urllib.parse import quote`), while `put_object` uses the raw key. With
library-owned `report_id` (Decision 6) keys are already clean; encoding is the
safety net for the username/prefix segments and matches `LocalFilesystemTarget`,
whose `Path.as_uri()` already percent-encodes.

**Error classification (review finding F2).** Agents act on errors, so the upload
failure carries a coarse, branchable `reason` instead of a single opaque message:

```python
def _s3_reason(exc) -> str:
    from botocore.exceptions import ClientError, NoCredentialsError
    if isinstance(exc, NoCredentialsError):
        return "no_credentials"
    code = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else None
    return {
        "AccessDenied": "access_denied",
        "InvalidAccessKeyId": "no_credentials",
        "SignatureDoesNotMatch": "no_credentials",
        "NoSuchBucket": "no_such_bucket",
        "RequestTimeout": "transient",
        "SlowDown": "transient",
        "ServiceUnavailable": "transient",
        "InternalError": "transient",
    }.get(code, "unknown")
```

An agent reads `err.details["reason"]`: `no_credentials`/`access_denied`/
`no_such_bucket` are not retryable and need user/admin action; `transient` is
safe to retry.

`_make_s3_client` lazy-imports boto3 so importing the publish package never
requires AWS libs:

```python
def _make_s3_client(region, endpoint_url, profile):
    try:
        import boto3
    except ImportError as exc:
        raise ReportPublishConfigError(
            message="S3 publishing requires boto3; install marivo[s3]",
            details={"missing": "boto3"},
        ) from exc
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client("s3", region_name=region, endpoint_url=endpoint_url)
```

Constructing an `S3PublishTarget` without an injected `client` builds the boto3
client eagerly. This requires boto3 installed and the `profile` (if any)
resolvable, but makes **no network call and does not validate credential
validity** — boto3 resolves credentials lazily on the first call. Construction
fails only for packaging/config reasons; auth/permission failures surface as
`ReportPublishUploadError` during upload. Intentional.

### 2. Content-Type mapping

Deterministic explicit table (not OS `mimetypes`, which varies by host). This is
what makes the shared link **render** instead of forcing a download:

| Extension | Content-Type |
|-----------|--------------|
| `.html` | `text/html; charset=utf-8` |
| `.css` | `text/css; charset=utf-8` |
| `.js` | `text/javascript; charset=utf-8` |
| `.json` | `application/json` |
| `.py`, `.txt` | `text/plain; charset=utf-8` |
| `.md` | `text/markdown; charset=utf-8` |
| `.csv` | `text/csv; charset=utf-8` |
| `.svg` | `image/svg+xml` |
| `.png` | `image/png` |
| anything else (incl. `.parquet`) | `application/octet-stream` |

`application/octet-stream` for frame snapshots is intentional: they should
download, not render. The table is not exhaustive by design — unlisted extensions
fall through to `application/octet-stream`.

### 3. Config resolution and backend dispatch (`publish_config.py`)

Add an S3 config dataclass and resolver that follow the parent spec's precedence
exactly (env `MARIVO_PUBLISH_S3_*` > `.marivo/publish.local.toml [storage.s3]` >
`marivo.publish.toml [storage.s3]`), with `profile` honored **only** from env or
the local file, never from tracked project config, never persisted.

```python
@dataclass(frozen=True)
class S3PublishConfig:
    bucket: str
    region: str | None = None
    endpoint_url: str | None = None
    profile: str | None = None
    prefix_template: str = _DEFAULT_PREFIX_TEMPLATE

def resolve_s3_publish_config(target=None, *, env=None, project_root=None) -> S3PublishConfig | None:
    """Return S3 config when a bucket is resolvable (s3:// target,
    MARIVO_PUBLISH_S3_BUCKET, or [storage.s3].bucket); region/profile/endpoint
    alone never select S3. Returns None when no bucket is configured."""

def resolve_publish_target(target=None, *, env=None, project_root=None) -> tuple[PublishTarget, str]:
    # 1. target="s3://bucket"                  -> S3PublishTarget
    # 2. target="/some/dir"                    -> LocalFilesystemTarget (unchanged)
    # 3. target is None and a BUCKET is set    -> S3PublishTarget
    #    (region/profile/endpoint alone do NOT count)
    # 4. otherwise                             -> resolve_publish_config(...) -> Local
```

`resolve_publish_config` (local) stays byte-for-byte unchanged, so existing
config tests keep passing. The orchestrator's non-instance branch becomes:

```python
# report_publish.py, replacing the else-branch of publish_report_package
else:
    tgt, prefix_template = resolve_publish_target(target, project_root=project_root)
```

Only the `s3://bucket` form is accepted. A non-empty path
(`s3://bucket/some/prefix`) raises `ReportPublishConfigError` rather than being
silently ignored: the prefix is owned by `prefix_template` and must contain
`{username}` (the existing prefix guard), which a path baked into the URL could
not carry.

`resolve_publish_target` is exported so an agent can **preflight** (review finding
F6): call it before building the package to fail fast if no bucket/credentials
are configured, and ask the user up front instead of after expensive report
generation.

### 4. Publish identity, result, and orchestrator changes

`report_id` is generated by the library and stamped into the manifest, joining
the fields it already stamps. There is no overwrite and no `export_id` path
segment:

```python
# report_publish.py (publish_report_package), conceptual diff
report_id = report_id or _new_report_id()            # e.g. "9f2c1a7b4e0d8c35" (secrets.token_hex(8))
stamped = artifact.manifest.model_copy(update={
    "report_id": report_id,                          # library-owned (was agent-supplied)
    "exported_by": resolved_by,
    "exported_at": resolved_at,
    "content_hash": content_hash,
})
dest_prefix = f"{prefix}/analysis-reports/{report_id}"   # no <export_id> segment
# no exists()/overwrite precondition: a fresh random report_id never collides
for rel, path in ...:                                 # content first
    tgt.put_file(f"{dest_prefix}/{rel}", path.read_bytes())
tgt.put_file(f"{dest_prefix}/{_MANIFEST_FILE}", manifest_bytes)   # manifest last
entrypoint_uri = tgt.uri(f"{dest_prefix}/{stamped.entrypoints['html']}")  # always present
```

`_new_report_id()` returns a URL-safe random token (`secrets.token_hex(8)`); it is
a safe path segment (passes `_safe_segment`) and contains no URL-significant
characters. The random token lives only in the path and in `manifest.json` (which
is excluded from the content hash), so two publishes of the *same* package share a
`content_hash` but get different `report_id`s and URLs.

Signature: `overwrite` is removed; an optional `report_id=None` is added (kept
`None` by agents, set only by tests):

```python
def publish_report_package(
    package_dir, *, exported_by=None, exported_at=None,
    target=None, project_root=None, report_id=None,
) -> PublishReportResult: ...
```

Result:

```python
@dataclass(frozen=True)
class PublishReportResult:
    uri: str            # package prefix URI
    entrypoint_uri: str  # direct link to index.html — ALWAYS set (Decision 8); hand this out
    report_id: str       # library-generated identity of this publish
    content_hash: str
    exported_by: str
    exported_at: str
    file_count: int
```

### 5. Packaging

`pyproject.toml` — add an `s3` extra, fold it into `all`, and add boto3 to `dev`
so the S3 target's unit tests can run (mirrors how `duckdb` sits in both an extra
and `dev`):

```toml
[project.optional-dependencies]
s3 = ["boto3>=1.34"]            # boto3 pulls in botocore
all = [..., "boto3>=1.34"]
dev = [..., "boto3>=1.34"]
```

boto3 is imported lazily inside `_make_s3_client`; a missing dependency raises
`ReportPublishConfigError` with the install hint. `import marivo` and
`import marivo.analysis.publish` stay boto3-free.

**"AWS-free" vs "no live AWS":** the end-user *runtime* core (installed without
`[s3]`) carries no AWS libraries. The *dev/test* environment **does** install
boto3, because `S3PublishTarget` imports `botocore.exceptions` to classify errors
even with a fake client. "No live AWS" in tests means no network and no
credentials — not "no botocore on the path."

### 6. Errors

```python
class ReportPublishUploadError(ReportPublishError):
    """An S3 upload (or stat) failed. details carries bucket, key, and a coarse
    `reason` in {no_credentials, access_denied, no_such_bucket, transient, unknown}."""
```

Reuse `ReportPublishConfigError` for missing boto3, unresolved bucket, and a
non-empty `s3://bucket/path`. `ReportPublishTargetExistsError` is **no longer
raised** by the publish flow (Decision 5); the class may stay in `errors.py` or be
removed as a follow-up.

## The shareable link (worked example)

Given `exported_by="alice"`, library-generated `report_id="9f2c1a7b4e0d8c35"`,
bucket `my-marivo-share`, region `ap-southeast-1`:

- S3 key: `marivo/users/alice/analysis-reports/9f2c1a7b4e0d8c35/index.html`
- `result.uri` (prefix):
  `https://my-marivo-share.s3.ap-southeast-1.amazonaws.com/marivo/users/alice/analysis-reports/9f2c1a7b4e0d8c35`
- `result.entrypoint_uri` (**hand this out**):
  `…/9f2c1a7b4e0d8c35/index.html`
- `result.report_id`: `9f2c1a7b4e0d8c35`

Re-publishing the same package yields a *different* `report_id` → a different URL
(a new report; the old one is untouched). The HTML is self-contained and links
its siblings by relative path, so the recipient needs only that one URL.

## Public content policy

The whole package is uploaded verbatim and the entire prefix is world-readable,
so **every file is public** — `index.html`, `flow.json`, `grounding.json`,
`replay.py`, and any `frames/`. Per project policy this is acceptable: all report
content is considered public-shareable.

- **What may be public (everything by default).** The HTML inlines evidence
  dataset rows (`report_html_adapter.py` `_dataset_payload`: `"rows": [...]`),
  source provenance and SQL (`_source_payload`: `datasource_refs`/`tables_used`
  and `sql` when `sql_status == "available"`), filters, metric definitions, flow
  steps, and claims. Frame parquet under `frames/` may also be published. All of
  this is accepted as shareable.
- **What must never be public: secrets.** The existing secret scanner runs over
  packaged text files (including `index.html`) and fails the publish on
  credential/token-shaped content. The HTML never carries secrets by
  construction, so this is a backstop, not an expected hit.
- **No content gate or stripping.** There is no publish-time omission of rows/SQL
  and no "private mode." The data-policy `frames/` check remains only as an
  internal-consistency guard (a manifest that declares `row_level_data="omitted"`
  may not also ship `frames/`); it is **not** a public-safety control, and frames
  may be shared publicly when the manifest does not omit them.

## Failure behavior

Content objects upload first and `manifest.json` is written **last** (the
content-first/manifest-last loop). A mid-upload failure raises
`ReportPublishUploadError` (with `details["reason"]`) before the manifest exists,
so the partial upload is correctly "not published." There is **no overwrite or
precondition step**: each publish uses a fresh random `report_id`, so the
destination prefix is always new and cannot collide.

On failure the partial objects are orphaned under that publish's random
`report_id` prefix (no `manifest.json`). They are harmless and never advertised —
`entrypoint_uri` is only returned on success — and are reclaimed by the parent
spec's later sweep of prefixes without a completed manifest. For a public bucket
this means a failed publish can leave a few world-readable partial files until the
sweep runs (review finding F9); an agent must never share a URL it did not get
back from a successful call.

## Remote verification (parent pipeline step)

The parent spec's pipeline lists `verify remote object size/hash` before writing
`manifest.json`. The local target does not re-verify (filesystem durability is
assumed); this design does not silently drop the guarantee for S3:

- **Integrity on PUT.** On the AWS path, `put_object` is given
  `ChecksumAlgorithm="SHA256"`, so S3 verifies the payload checksum server-side
  and rejects a corrupted/truncated upload with an error → `ReportPublishUploadError`.
  No extra HEAD round-trip. Size is implicitly checked (boto3 sets
  `Content-Length`; S3 rejects a length mismatch).
- **Strong consistency + manifest-last.** S3 PUTs are atomic and read-after-write
  consistent, and `manifest.json` is written last, so an interrupted publish is
  never a readable "published" state.
- **Endpoint fallback.** The checksum param is sent only for the AWS path (no
  `endpoint_url`); for a custom `endpoint_url` it is omitted (support varies) and
  those endpoints rely on PUT atomicity + manifest-last ordering. Auto-detecting
  checksum support, and a full remote re-read+hash compare, are deferred.

This amends the parent guarantee from "always re-read and compare" to "verify via
server-side checksum on PUT (AWS), with a documented endpoint fallback."

## Operational prerequisite (one-time, by a bucket admin)

The library cannot grant public access on a Block-Public-Access bucket and does
not try to; it also does not verify reachability (Decision F1 note). An admin
configures the bucket once:

1. Allow public bucket policies for the bucket (disable `BlockPublicPolicy` /
   `RestrictPublicBuckets` for it, or set this at the account level).
2. Attach a read-only policy scoped to the publish prefix:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicReadMarivoReports",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::my-marivo-share/marivo/users/*"
  }]
}
```

The uploader's IAM principal needs only `s3:PutObject` (the publish flow no longer
issues `HeadObject`/`GetObject`). Public read for recipients comes entirely from
the bucket policy above.

## Public surface changes

- New: `S3PublishTarget` (exported from `marivo.analysis.publish`, parallel to
  `LocalFilesystemTarget`).
- `PublishReportResult`: **+ `entrypoint_uri: str`** (always set), **+ `report_id:
  str`** (library-generated).
- `publish_report_package`: **`overwrite` removed**; optional `report_id=None`
  added (tests only); `report_id` is library-generated, no longer agent-supplied.
- New error `ReportPublishUploadError` (with `details["reason"]`);
  `ReportPublishTargetExistsError` no longer raised by the flow.
- Supporting exports: `S3PublishConfig`, `resolve_s3_publish_config`,
  `resolve_publish_target`.

These public-symbol changes require updating matching skill references/examples.

## Alternatives considered

- **B — `ACL=public-read` per object.** Rejected: fails on ACL-disabled buckets
  (the modern default). Possible later opt-in for legacy buckets.
- **Reachability probe / `public_readable` field (F1).** Rejected per user
  direction: bucket visibility is the user's responsibility; the library does not
  verify it.
- **Overwrite/versioning of an existing report.** Rejected per user direction:
  every publish is a new immutable report (random `report_id`); no overwrite.
- **Publish-time content gate / "private mode" that strips frames or SQL.**
  Rejected per user direction: all report content (SQL, tables, rows, parquet) is
  public-shareable; only secrets are disallowed (existing secret scan).
- **Presigned URLs / CloudFront+OAC.** Deferred (permanent public link is the
  goal).

## Testing

All S3 tests inject a fake client (`client=`) — no network, no credentials.
botocore is available because boto3 is in `dev`; the fake raises real
`botocore.exceptions` types so error classification is exercised.

- `S3PublishTarget`: `put_file` sends correct `Key`/`Body`/`ContentType` and
  **no `ACL`**; `uri` builds virtual-hosted (with/without region) and path-style
  (endpoint); URL-significant chars in username/prefix are percent-encoded in
  `uri` but raw in `Key`, and the encoded URL decodes back to the key.
- Error classification: simulated `NoCredentialsError`, `AccessDenied`,
  `NoSuchBucket`, and a 5xx map to `reason` `no_credentials`/`access_denied`/
  `no_such_bucket`/`transient`.
- Integrity: `ChecksumAlgorithm="SHA256"` on the AWS path, omitted when
  `endpoint_url` is set; a simulated checksum-mismatch surfaces as
  `ReportPublishUploadError`.
- Content-Type table (incl. `.parquet` → octet-stream and unknown default).
- Config: precedence (env > local > project), `s3://` parsing, non-empty
  `s3://bucket/path` raises, `profile` only from env/local, and
  `MARIVO_PUBLISH_S3_REGION`/`_PROFILE` **without** a bucket stays local.
- Identity / no-overwrite: two publishes of the same package produce **different**
  `report_id`s, prefixes, and `entrypoint_uri`s, and never raise an exists error;
  an injected `report_id=` is honored; `content_hash` is equal across the two
  (random id is path/manifest-only).
- `entrypoint_uri` is always set and resolves to `<prefix>/<report_id>/index.html`.
- Local↔S3 parity: publishing the same package to `LocalFilesystemTarget` and a
  fake S3 client yields the **same relative key set and bytes** (the upload does
  not rewrite file contents).
- Content policy: a package whose `index.html` inlines SQL/rows and that ships
  `frames/` (manifest does not omit row data) publishes successfully — no content
  gate; only the secret scanner can fail a publish (on credential-shaped text).
- Orchestrator end-to-end with a fake client: content keys first, `manifest.json`
  last, a mid-upload `ReportPublishUploadError` leaves no manifest.
- boto3-missing path raises `ReportPublishConfigError`.

New test file `tests/test_analysis_report_artifact_publish_s3.py`; extend
`tests/test_analysis_report_artifact_publish_config.py`,
`…_targets.py`, and `…_publish.py`.

## Files touched

- `marivo/analysis/publish/publish_targets.py` — `S3PublishTarget`,
  `content_type_for`, `_make_s3_client`, `_s3_reason`.
- `marivo/analysis/publish/publish_config.py` — `S3PublishConfig`,
  `resolve_s3_publish_config`, `resolve_publish_target`.
- `marivo/analysis/publish/report_publish.py` — dispatch via
  `resolve_publish_target`; generate/stamp random `report_id`; drop `export_id`
  path segment; remove `overwrite` + exists precondition; add `entrypoint_uri` and
  `report_id` to the result.
- `marivo/analysis/publish/report_models.py` / `report_validation.py` —
  `report_id` is library-stamped (not required from the agent); **remove
  `export_id`** from the manifest model and validation.
- `marivo/analysis/publish/report_html_adapter.py` / `report_mcp_adapter.py` —
  drop `export_id` from emitted payloads/manifests.
- `marivo/analysis/errors.py` — `ReportPublishUploadError`.
- `marivo/analysis/publish/__init__.py` (+ `marivo/analysis/__init__.py` if
  re-exported) — new exports.
- `pyproject.toml` — `s3` extra; boto3 in `all` and `dev`.

## Docs to update (same change)

- **Parent spec** (`2026-06-01-…`): public-read access model, HTTPS
  `entrypoint_uri`, Content-Type, bucket-policy prerequisite; the `target=None`
  S3-over-local behavioral change; **Decisions 5–9** (random library `report_id`
  with `export_id` removed, no overwrite, public content policy, self-contained
  HTML).
- **`marivo-skills/marivo-analysis/references/final-report.md`** (the agent's
  actual interface — Publishing handoff): make these explicit for agents:
  - Hand the user **`result.entrypoint_uri`** (always set) and note `report_id`.
  - **Every publish is a new report** — re-publishing creates a new URL; there is
    no overwrite. Never reuse or guess a URL.
  - **`report_id` is library-generated** — the agent must not invent ids.
  - **Everything in the report is shared publicly as-is** — SQL, table names,
    detail rows, and frame parquet are all OK to be public; the only hard rule is
    no secrets (already secret-scanned; the HTML carries none by construction). No
    need to omit rows for sharing.
  - **Preflight (F6)** — call `resolve_publish_target(...)` before building to
    fail fast on missing bucket/credentials; ask the user up front.
  - **Headless credentials (F8)** — non-interactive AWS credentials are required
    (env keys, instance/container role); interactive SSO won't work in
    headless/cron runs.
  - **Error handling (F2)** — on `ReportPublishUploadError`, branch on
    `details["reason"]`: `no_credentials`/`access_denied`/`no_such_bucket` → ask
    the user to fix config/IAM/bucket; `transient` → retry.
  - **Reachability is the user's responsibility (F1)** — the link works only if
    the bucket policy allows public read; 403s for recipients mean an admin must
    set the policy.
- Any `marivo-skills/marivo-*/references/examples/` showing `PublishReportResult`
  or report publishing (verify during implementation).

## Open questions

- `Cache-Control` on `index.html` (short max-age) so re-shared links refresh —
  omit for now.
