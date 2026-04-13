#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -gt 1 ]]; then
  echo "usage: $0 [metadata_path_or_duckdb_path]" >&2
  exit 2
fi

target_input="${1:-}"
if [[ -z "$target_input" ]]; then
  echo "error: metadata sqlite path required. Pass the configured metadata path." >&2
  exit 1
fi
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
