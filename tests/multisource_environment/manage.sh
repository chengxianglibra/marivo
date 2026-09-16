#!/usr/bin/env bash
# Explicit local qualification lifecycle; never called by pytest or default gates.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
state_dir="$HOME/.cache/marivo-multisource"
profile="marivo-multisource"
socket="unix://$HOME/.colima/$profile/docker.sock"

if [[ $# -ne 2 || ! "$1" =~ ^(start|stop|status|logs)$ || ! "$2" =~ ^(trino|clickhouse|postgres-analysis|mysql-analysis)$ ]]; then
    echo "Usage: $0 {start|stop|status|logs} {trino|clickhouse|postgres-analysis|mysql-analysis}" >&2
    exit 2
fi
if [[ ! -f "$state_dir/secrets.env" || ! -f "$state_dir/docker/config.json" ]]; then
    echo "Missing private environment setup; follow README.md before starting services." >&2
    exit 1
fi
docker_command=(docker --config "$state_dir/docker" --host "$socket")
compose=("${docker_command[@]}" compose --progress quiet --env-file "$state_dir/secrets.env"
    -f "$repo_root/tests/multisource_environment/compose.yaml" --profile "$2")

case "$1" in
    start)
        "$repo_root/.venv/bin/python" -c 'import shutil, sys; sys.exit(0 if shutil.disk_usage(sys.argv[1]).free >= 8 * 1024**3 else "Need at least 8 GiB free before starting qualification")' "$repo_root"
        if [[ "$2" == trino ]]; then
            "${compose[@]}" stop clickhouse
            # Named volumes must be writable by the pinned Trino image's uid 1000.
            "${compose[@]}" run --rm --no-deps --user root --entrypoint sh trino \
                -c 'mkdir -p /warehouse && chown 1000:1000 /warehouse'
        elif [[ "$2" == clickhouse ]]; then
            "${compose[@]}" stop trino postgres
        fi
        "${compose[@]}" up -d --wait --wait-timeout 180
        if [[ "$2" == postgres-analysis ]]; then
            "$repo_root/.venv/bin/python" "$repo_root/tests/multisource_environment/postgres_analysis.py"
        elif [[ "$2" == mysql-analysis ]]; then
            "$repo_root/.venv/bin/python" "$repo_root/tests/multisource_environment/mysql_analysis.py"
        fi
        ;;
    stop)
        if [[ "$2" == trino ]]; then
            "${compose[@]}" stop trino postgres
        else
            "${compose[@]}" stop "$2"
        fi
        ;;
    status) "${compose[@]}" ps -a ;;
    logs) "${compose[@]}" logs --tail 60 "$2" ;;
esac
