#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
default_duckdb_path="$repo_root/data/mvp.duckdb"

if [[ $# -gt 1 ]]; then
  echo "usage: $0 [duckdb_path_or_metadata_path]" >&2
  exit 2
fi

target_input="${1:-$default_duckdb_path}"
if [[ "$target_input" == *.meta.sqlite ]]; then
  metadata_path="$target_input"
else
  metadata_path="${target_input%.*}.meta.sqlite"
fi

removed=0
for path in "$metadata_path" "$metadata_path-wal" "$metadata_path-shm"; do
  if [[ -e "$path" ]]; then
    rm -f "$path"
    removed=1
    echo "removed $path"
  fi
done

if [[ $removed -eq 0 ]]; then
  echo "no metadata sqlite files found for $metadata_path"
fi
