# Isolated Codex benchmark runner

Run a fresh, observable CLI session with a supplied project, prompt, and optional
explicit skill files. The runner defaults to `gpt-6-luna` with `max` reasoning.
It contains no business scenarios, expected answers, or analytical recipes.

```sh
.venv/bin/python scripts/agent_bench/run.py \
  --executable /opt/homebrew/bin/codex \
  --model gpt-6-luna --reasoning max \
  --project /absolute/path/to/fresh-project \
  --prompt /absolute/path/to/prompt.txt \
  --skill /absolute/path/to/explicit/SKILL.md \
  --output /absolute/path/to/new-evidence-directory
```

The output directory must be new and outside the project. `--skill` is
repeatable; the runner asks the agent to read those exact files. It does not
install skills or edit Codex configuration, credentials, `HOME`, or `CODEX_HOME`.
Project instructions remain in effect. `--sandbox` selects the child policy;
the default permits local analysis processes and their data access. Extension
isolation does not restrict what the agent can read through its shell.
The CLI's user-global `AGENTS.md` also remains in native prompt input; the
tested installation contains only generic CLI working rules there. Its exact
content is visible in every captured prompt. Both benchmark arms must use the
same global/project instruction policy.

Before model execution the runner uses the selected executable's local
`debug prompt-input` and `mcp list --json` facilities. It disables plugins,
remote plugins, apps, hooks, memory, skill search, automatic skill MCP
installation, browser/computer capabilities, and agent delegation. It enumerates
MCP names, disables each individually, and verifies that none remain enabled.
An empty MCP table does **not** disable existing servers in CLI 0.162.1.
It captures the discovered skills catalog, disables every discovered full
`SKILL.md` path, and rejects a remaining skills/plugin/deferred-tool catalog.
Folder paths did **not** disable skills in the tested CLI. Unrecognized catalog
formats fail before model execution. These checks make CLI upgrades reviewable.

All child commands use `--no-daemon` so the shared daemon cannot supply a
different configuration. The fresh `exec` invocation additionally uses
`--ignore-rules` and `--ephemeral`. Diagnostics and execution load the same
inherited configuration with the same explicit overrides. The exec-only
`--ignore-user-config` flag is deliberately omitted: it removes transport
definitions while leaving disabled MCP override entries, which this CLI rejects
as invalid transports. The isolation inventory is established by the explicit
settings and observable inventory, not by assuming an empty user configuration.
Only MCP names and enabled states are retained from MCP inventory. Transport
configuration, environment values, and credentials are never recorded or copied.
Authentication continues through the CLI's existing credential store.

Evidence includes the exact submitted prompt, model-visible prompt input,
extension inventory and disable flags, executable/version and source hashes,
raw model catalog, byte-preserved native JSONL, receipt-timestamped events,
native `turn.completed.usage` records, stderr, and the full final response.
`isolation.json` also records a normalized extension inventory and hash that
exclude project paths and message IDs, for comparisons across fresh projects.
The catalog fingerprint describes the advertised model configuration, not
server model weights. No model-internal turn count is inferred.

Measurement instructions ask the agent to write `first_result.json` only after
an actual successful computation produces task-relevant business values.
`--marker` changes that relative path. A preexisting marker is rejected. The
runner records an explicitly labeled observation snapshot when it sees the
marker and checks again after process exit, retaining markers created just
before exit. It then reconciles the full event stream against the marker's
self-reported epoch, retaining both views. Receipt delays and marker semantics
still require independent trace validation. An action is an item with a native start/completion event other
than a message, reasoning, or todo list; missing start events are not invented.
Receipt times are controller observations. Marker contents and the claim that
the result is substantive require independent acceptance against the trace;
a JSON object is not semantic proof. Missing or invalid markers remain visible
as missing evidence. The runner never substitutes metadata for a first result.

`tool-failures.json` separately preserves timestamped stderr router error
records, their source lines, and a bounded category. Tool arguments and patch
contents stay in raw stderr. Router records have no assumed native tool ID and
may overlap native events; the runner does not add them to shell-command,
action, token, or model-turn counts. `action_observability` labels native starts
as a lower bound for all attempts when router records require reconciliation.
The first-result record includes that qualification for failures before the
marker or with unknown timestamps. An incomplete, unreconciled action count
does not establish an upper-bound action gate. Absence of known router errors
only establishes the native-stream count, not exhaustive tool observability.

The delivery directory retains every new or modified project file, including
nested files, binary artifacts, and `.marivo` state; the manifest records all
input/output hashes and deletions. Unmodified inputs are fingerprinted rather
than duplicated. `.git`, `.venv`, and `__pycache__` directory names are excluded.
Symlinks are recorded and copied as links without following them. Keep complete
analysis deliverables outside those excluded directories. The runner neither
rewrites nor filters the agent's final answer.
