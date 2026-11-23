#!/usr/bin/env sh

set -eu

if [ "$#" -eq 0 ]; then
    set -- python
fi
venv_dir=".venv"

if [ -x "$venv_dir/bin/python" ]; then
    venv_bin="$venv_dir/bin"
    tool_suffix=""
elif [ -x "$venv_dir/Scripts/python.exe" ]; then
    venv_bin="$venv_dir/Scripts"
    tool_suffix=".exe"
else
    echo "error: missing .venv Python; create the project venv first" >&2
    exit 1
fi

venv_python="$venv_bin/python$tool_suffix"

if [ ! -x "$venv_python" ]; then
    echo "error: missing $venv_python; create the project venv first" >&2
    exit 1
fi

for tool_name do
    tool_path="$venv_bin/$tool_name"
    if [ ! -x "$tool_path" ] && [ ! -x "$tool_path$tool_suffix" ]; then
        echo "error: missing $tool_path; install the required project dependencies in $venv_dir" >&2
        exit 1
    fi
done

active_prefix="$("$venv_python" -c 'import os; print(os.path.realpath(os.environ.get("VIRTUAL_ENV", "")))')"
expected_prefix="$("$venv_python" -c 'import os; print(os.path.realpath(".venv"))')"
project_prefix="$("$venv_python" -c 'import os; print(os.path.realpath("."))')"

if [ -n "$active_prefix" ] && [ "$active_prefix" != "$expected_prefix" ] && [ "$active_prefix" != "$project_prefix" ]; then
    echo "error: active VIRTUAL_ENV is $active_prefix, expected $expected_prefix" >&2
    exit 1
fi
