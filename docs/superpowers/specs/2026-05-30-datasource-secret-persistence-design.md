# Datasource Secret Persistence

## Summary

Today a datasource definition is project-local and secret-free: sensitive
fields are forbidden as literals and must be referenced as `*_env`
(`password_env="TRINO_PASSWORD"`), resolved from `os.environ` at backend-build
time. The reference persists, but the *value* does not. An agent that exports a
secret for one session loses it on the next, so a previously working datasource
fails with `DatasourceEnvVarMissingError` until the secret is re-supplied.

This spec adds a **user-global secret store** that captures resolved secrets on
the first validated connection, so a secret supplied once is reusable across
sessions without relying on the agent to take a separate "save" action.

The mechanism is a thin **secret-provider chain** behind the existing `*_env`
indirection. The default and only provider shipped now is a plaintext
write-through cache under `~/.marivo`. Keyring and external-fetcher providers
are left as an interface seam only — no speculative implementations.

## Decisions (locked)

- **Indirection unchanged.** `.marivo/datasource/*.py` stays project-local,
  committable, and secret-free. Only the `*_env` *reference* lives there.
- **Storage medium: plaintext**, user-global, under `~/.marivo` (the home
  directory — distinct from the project-local `<project_root>/.marivo`).
- **Agent may write.** Persistence is automatic, not a separate agent action.
- **Capture point: a validated round-trip**, not bare `build_backend()` (ibis
  `connect()` is lazy for several backends, so it does not prove the secret is
  correct). Only known-good secrets are cached.
- **Resolution order:** `os.environ` → user-global store → error. Env first, so
  an explicit export overrides the cache and secret rotation is self-healing.
- **Key granularity:** keyed by the env var name (the credential identity), not
  by datasource object and not by `backend_type`. Multiple datasources that
  reference the same env name share one cached value.

## Non-goals

- No keyring / OS-keychain provider in this change (interface only).
- No external `credential_process`-style fetcher in this change (interface
  only).
- No project-local secret persistence — that would reintroduce the commit risk
  the current design exists to prevent.
- No change to the `*_env` authoring surface or to `SENSITIVE_FIELD_STEMS`.

## Architecture

### Provider seam

A minimal protocol decouples the stable reference (env name) from where the
value lives, mirroring git credential helpers / AWS `credential_process` /
kubectl `exec` plugins.

```python
class SecretProvider(Protocol):
    def get(self, name: str) -> str | None: ...

# Default chain, in priority order:
#   EnvProvider()            -> os.environ.get(name)
#   LocalPlaintextCache(...)  -> ~/.marivo/secrets.toml
```

Only `EnvProvider` and `LocalPlaintextCache` are implemented. Future providers
(keyring, fetcher command) slot into the chain without touching datasource
files, the IR, or the connection chokepoint.

### Resolution + write-through

```
resolve(name):
    for provider in chain:
        if (v := provider.get(name)) not in (None, ""):
            return v, provider          # provenance: who supplied it
    raise DatasourceEnvVarMissingError   # neither env nor cache had it

# after a VALIDATED round-trip succeeds, for each value that came from
# os.environ (not already in the cache), LocalPlaintextCache.persist(name, value)
```

`os.environ` ranking first means: a fresh export always wins over a stale cache,
and the next validated round-trip refreshes the cache — rotation self-heals.
Persisting only post-validation means a typo'd secret never poisons the cache.

## Changes

### New: `marivo/analysis/datasources/secrets.py`

- `SecretProvider` protocol, `EnvProvider`, `LocalPlaintextCache`.
- `resolve(name) -> tuple[str, SecretProvider]` running the default chain.
- `LocalPlaintextCache`:
  - Path: `Path.home() / ".marivo" / "secrets.toml"`, flat table keyed by env
    var name: `TRINO_PASSWORD = "..."`.
  - `get(name)`: read the value; **refuse to read** (raise
    `DatasourceSecretStorePermissionsError`) if the file is group/world
    readable or writable — the libpq `~/.pgpass` hardening pattern.
  - `persist(name, value)`: create dir `0o700`, write atomically (temp file +
    `os.replace`) with mode `0o600`, re-assert perms after write.
  - `persistence_enabled()`: `False` when `MARIVO_PERSIST_SECRETS=0` or when
    `CI` is truthy; reads still work, writes are skipped. Persistence is NOT
    gated on an interactive TTY — agents run non-interactively and are the
    primary actor that must persist.
  - Path guard: refuse to write if the resolved store path lies inside a git
    repository / project tree (defends against an unusual `HOME`).

### `marivo/analysis/datasources/backends.py`

- `_effective_kwargs(datasource)` resolves each `env_ref` through
  `secrets.resolve(...)` instead of bare `os.environ.get(...)`, and returns
  which stems were sourced from env (eligible for write-through) alongside the
  resolved kwargs.
- `build_backend` is otherwise unchanged; it does **not** persist (it may be a
  lazy, unvalidated connection).

### `marivo/analysis/datasources/registry.py`

- `test(name)`: after `backend.raw_sql("SELECT 1")` succeeds, call the shared
  `secrets` write-through for env-sourced values. This is the primary, already
  agent-facing validation path (the existing `DatasourceConnectionError` fix
  snippet already directs agents to `mv.datasources.test(name)`).
- The executor's first successful query against a freshly built backend calls
  the same write-through helper, so a datasource used directly (without an
  explicit `test()`) is still captured. Both call one helper; no duplicated
  logic.

### Errors (`marivo/analysis/errors.py`)

- New `DatasourceSecretStorePermissionsError(AnalysisError)` with structured
  fields (`path`, `mode`) and the shared template; fix snippet:
  `chmod 600 ~/.marivo/secrets.toml`.
- `DatasourceEnvVarMissingError`: unchanged class, refined semantics (fires only
  when neither env nor cache supplies the value); fix snippet notes the secret
  is remembered after the first successful `mv.datasources.test(name)`.

## Security posture

This change deliberately flips one invariant and must say so loudly:

- **Before:** Marivo never writes secrets to disk.
- **After:** secrets are cached **in plaintext** under `~/.marivo/secrets.toml`
  (user-global, never in the project tree).

Guardrails that bound the blast radius:

- File `0o600`, dir `0o700`, atomic writes, perms re-asserted on every write.
- Refuse to read a store file with loose permissions (libpq pattern).
- Writes disabled in CI and via `MARIVO_PERSIST_SECRETS=0`; reads always work
  (not gated on a TTY, so non-interactive agents still persist locally).
- Store path guarded to stay outside any git repo / project tree.
- Project-local `.marivo/datasource/*.py` remains secret-free; `describe()`
  stays redacted; no error message echoes a secret *value* (only env names).

## Testing

Follow the existing backend test patterns in
`tests/test_analysis_profiles_backends.py`, with `Path.home` monkeypatched to a
`tmp_path` so no real home file is touched.

- Resolution precedence: env value wins over a different cached value.
- Cache hit when env is unset; both unset → `DatasourceEnvVarMissingError`.
- `persist` writes `0o600`; loose perms on read → permissions error.
- Path guard rejects a store path inside a repo tree.
- `MARIVO_PERSIST_SECRETS=0` and `CI=1` disable writes but not reads.
- `test()` success triggers write-through; failed `test()` does not persist.

Run: `make test`, `make typecheck`, `make lint`.

## Compatibility

Fully backward compatible. Existing datasource files are unchanged. With no
cache file present and no new env var, behavior is identical to today
(env-only). The cache only ever *adds* a fallback value source.

## Follow-up docs (in the implementing change)

- `marivo-skills/marivo-semantic/references/datasource.md` (referenced by the
  error templates): document the cache, its location, and the plaintext caveat.
- `agent-guide.md`: the "Credentials are read from environment variables ... and
  must not be written into project state" bullet needs the nuance that *project
  state* still holds no secrets, while a *user-global* cache may persist them.

## Open knob

- **Store file format.** Defaulting to flat TOML keyed by env name
  (`TRINO_PASSWORD = "..."`). INI profiles (AWS-style) would be the path to
  named credential profiles later; that is additive and not needed now.
