#!/usr/bin/env sh

set -eu

tool_name="${1:-python}"
venv_dir=".venv"
venv_python="$venv_dir/bin/python"
tool_path="$venv_dir/bin/$tool_name"

if [ ! -x "$venv_python" ]; then
    echo "error: missing $venv_python; create the project venv first" >&2
    exit 1
fi

if [ ! -x "$tool_path" ]; then
    echo "error: missing $tool_path; install project dev dependencies in $venv_dir" >&2
    exit 1
fi

active_prefix="$("$venv_python" -c 'import os; print(os.path.realpath(os.environ.get("VIRTUAL_ENV", "")))')"
expected_prefix="$("$venv_python" -c 'import os; print(os.path.realpath(".venv"))')"

if [ -n "$active_prefix" ] && [ "$active_prefix" != "$expected_prefix" ]; then
    echo "error: active VIRTUAL_ENV is $active_prefix, expected $expected_prefix" >&2
    exit 1
fi
