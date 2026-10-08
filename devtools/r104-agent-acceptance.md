# Installed Agent acceptance

`r104_agent_acceptance` is an explicitly invoked qualification tool. Default
tests exercise its isolation and evaluation guards without making model calls.
It uses a fresh Claude CLI session, a frozen installed wheel, and one business
question per project. It does not publish packages or modify packaged skills. The invocation follows
the [official CLI reference](https://code.claude.com/docs/en/cli-reference)
and [sandbox settings](https://code.claude.com/docs/en/sandboxing), with actual
isolation probes against the installed CLI version.

Freeze the repository HEAD before creating an external `git archive` snapshot.
Run `make pypi-build pypi-check` in that snapshot with Python 3.12, record both
archive hashes and dependency constraints, then install the wheel non-editably
in an external environment. The candidate state JSON supplies `root`, `head`,
`wheel`, `interpreter`, and the archive-name-to-SHA256 `archives` object.

Prepare inputs with formal declarations under `models/`, `marivo.toml`, and
local DuckDB or Parquet sources. No prior `.marivo` state, answer script, or
oracle belongs in these inputs. Preparation rewrites authored source paths and
copies the installed package's two skills verbatim:

```sh
.venv/bin/python -m devtools.r104_agent_acceptance prepare \
  --source /absolute/external/inputs/question \
  --destination /absolute/external/projects/question \
  --interpreter /absolute/external/environment/bin/python
```

Provide the business question, explicit business definitions and budget, this
interpreter, the packaged skill paths, and `marivo.help()` as the discovery
start. Keep capability-to-call mappings and scoring material outside the
project. Before business trials, run a separate isolation probe that confirms
the interpreter works and repository, evaluation material, memories and other
question projects cannot be read. The installed Claude CLI must enforce the
sandbox; an emitted answer alone is not proof that its settings worked.
Reserve every producer and offline project directory before starting concurrent
trials. The runner denies both peer project paths and their Claude temporary task
output paths, including paths that do not exist yet. Probe an actual peer task
output as well as its project directory; background output is another read boundary.

```sh
.venv/bin/python -m devtools.r104_agent_acceptance run \
  --state /absolute/external/state.json \
  --repository /absolute/repository \
  --project /absolute/external/projects/question \
  --prompt /absolute/external/prompts/question.txt \
  --receipt /absolute/external/receipts/question/produce-01 \
  --model exact-provider-model-id --timeout 2700
```

The runner uses `--safe-mode`, `--bare`, disabled slash commands and memory,
empty MCP configuration, only sandboxed Bash, and `--effort max`. It imports
only existing authentication from the process environment or the user's Claude
settings, without loading that settings file as customization. It records the
requested model, actual init event and response model IDs, CLI version, session,
prompt and all visible file hashes (including initial persistent state),
configuration, complete stream-json trajectory, stderr, exit
status, and a final project snapshot. An init model differing from the requested
identity fails the attempt. Independent evaluation also checks that response
model identities remain uniform and match the frozen provider preflight. The
requested effort is recorded separately from actual effort, which the installed
provider does not verify. There is no automatic model fallback. Raw CLI usage fields are retained as part of the
trajectory; this tool does not run the skipped cost benchmark.

Every attempt gets a new receipt directory. Preserve unsuccessful attempts and
classify Agent, product, tool and infrastructure failures separately. A process
success is only `awaiting_evaluation`.

For the separate offline trial, obtain exact identities from the producer and
copy only persistent state, project identity and the original packaged skills:

```sh
.venv/bin/python -m devtools.r104_agent_acceptance recover-project \
  --source /absolute/external/projects/question \
  --destination /absolute/external/offline/question \
  --identities /absolute/external/projects/question/identities.json
```

The identity file has exactly `session_id` and an `artifacts` label-to-exact-ID
map. Models, sources, answers, and output scripts are excluded. Use another
fresh `run` receipt with a prompt asking for exact Artifact reads and legitimate
fixed continuations. Verify all values, complete key domains, Cell states,
grain, times, retained evidence, explanations and required public execution
paths outside the Agent boundary, against raw business facts and an independent
oracle. Duration and statistical methods retain their strict contracts; native
numeric carriers use their actual disclosed contract.

Numeric and semantic assessments must each bind the candidate wheel hash and
trace hash, list the reviewed assertions, and say `passed` before qualification
can be recorded:

```sh
.venv/bin/python -m devtools.r104_agent_acceptance evaluate \
  --receipt /absolute/external/receipts/question/produce-01 \
  --numerical /absolute/external/evaluation/question-numeric.json \
  --semantic /absolute/external/evaluation/question-semantic.json
```

The evaluation command binds external assessments; it is not itself a numeric
oracle. Retain failed assessments as evidence and leave incomplete obligations
unqualified. Hash a durable evidence bundle and update the acceptance index only
to the scope that the actual trials discharge. A single model's result does not
establish a general success rate.
