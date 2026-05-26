"""Pattern: persist a datasource connection in the profile registry, then
let mv.session.create build the backend automatically.

When to use: any time the user has given you connection details for a
datasource declared in the semantic model. Persisting them once avoids
re-asking on every session.

Sensitive credentials (password / token / api_key / ...) must be supplied
via ``<field>_env="VAR_NAME"`` so the literal value lives only in
``os.environ``; the profile file stores the variable name, not the secret.
"""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

import os
import tempfile

# Isolate the example from the developer's real ~/.marivo/profiles/.
# In real usage MARIVO_HOME is unset and the registry lives at ~/.marivo/.
os.environ["MARIVO_HOME"] = tempfile.mkdtemp(prefix="marivo-profiles-example-")

import marivo.analysis_py as mv

mv.profiles.set("local_warehouse", backend_type="duckdb", path=":memory:")

print(sorted(p.name for p in mv.profiles.list()))

description = mv.profiles.describe("local_warehouse")
print(f"backend_type={description.backend_type}")
print(f"literal_fields={description.literal_fields}")
print(f"env_refs={description.env_refs}")

# Expected output:
# ['local_warehouse']
# backend_type=duckdb
# literal_fields={'path': ':memory:'}
# env_refs={}
