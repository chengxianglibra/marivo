# S4 P3 installed-package journeys

This directory owns four user-view scripts, a governed DuckDB project builder,
an independent source-fact oracle, cold-recovery checks, and an external Agent
trial driver. None of the user scripts imports tests or private Marivo modules.

Run from the repository root and record the exact source revision and diff used
for the wheel. Keep the wheel, virtual environment, workspace and evidence paths
outside Agent project folders.

```sh
uv build --wheel --out-dir /tmp/marivo-s4-p3-wheel
uv venv /tmp/marivo-s4-p3-venv --python .venv/bin/python
uv pip install --python /tmp/marivo-s4-p3-venv/bin/python '/tmp/marivo-s4-p3-wheel/marivo-0.5.3.dev0-py3-none-any.whl[duckdb]'

.venv/bin/python devtools/analysis_dsl_s4_p3/controller.py prepare \
  --workspace /tmp/marivo-s4-p3-projects \
  --evidence docs/superpowers/plans/s4-p3-evidence
.venv/bin/python devtools/analysis_dsl_s4_p3/controller.py scripts \
  --workspace /tmp/marivo-s4-p3-projects \
  --evidence docs/superpowers/plans/s4-p3-evidence \
  --python /tmp/marivo-s4-p3-venv/bin/python \
  --wheel /tmp/marivo-s4-p3-wheel/marivo-0.5.3.dev0-py3-none-any.whl
```

The controller launches each script with the installed interpreter from its
own project. It compares results with SQL and rank arithmetic over source facts,
then removes the database and launches a fresh recovery process. J1 also attempts
a new source evaluation while offline and verifies that its earlier Artifact
remains recoverable.

For independent Agent acceptance, run each journey in a fresh project and
Claude session:

```sh
.venv/bin/python devtools/analysis_dsl_s4_p3/agent_trial.py j1 \
  --workspace /tmp/marivo-s4-p3-projects \
  --evidence docs/superpowers/plans/s4-p3-evidence \
  --python /tmp/marivo-s4-p3-venv/bin/python
```

Repeat for `j2`, `j3`, and `j4`. The Agent sees its authored project, the installed
public package, the business question and `marivo.help()`. It receives no user
script, private implementation, oracle or target answer. The trial uses Claude
safe mode with local tools and writes a complete JSONL trace. The business
question specifies UTC reporting periods so its answers can be compared with
the UTC user-script oracle. Check the trace,
`answer.py`, actual output and independent oracle separately before assigning
pass/fail status. A failed trial stays in evidence; use a new workspace and a
new `--attempt` name for a repair trial. No API cost cap is applied.

Archive and inspect each completed attempt:

```sh
.venv/bin/python devtools/analysis_dsl_s4_p3/audit_trace.py \
  --trace docs/superpowers/plans/s4-p3-evidence/agent/j1/attempt-01/trace.jsonl \
  --answer /tmp/marivo-s4-p3-projects/agent/j1/answer.py \
  --project /tmp/marivo-s4-p3-projects/agent/j1 \
  --output docs/superpowers/plans/s4-p3-evidence/agent/j1/attempt-01/audit.json
.venv/bin/python devtools/analysis_dsl_s4_p3/replay_agent.py \
  --project /tmp/marivo-s4-p3-projects/agent/j1 \
  --python /tmp/marivo-s4-p3-venv/bin/python \
  --evidence docs/superpowers/plans/s4-p3-evidence/agent/j1/attempt-01
```

The audit copies the final Agent code beside its complete trace, and the replay
records its actual output. The acceptance record under `docs/superpowers/plans/`
is local evidence in a Git-ignored directory; it is not part of the wheel.
