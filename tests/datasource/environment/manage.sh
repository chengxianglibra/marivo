#!/usr/bin/env bash
# Explicit local qualification lifecycle; never called by pytest or default gates.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
state_dir="$HOME/.cache/marivo-multisource"
project="marivo-multisource"
machine="${MARIVO_PODMAN_MACHINE:-podman-machine-default}"
profiles='trino|clickhouse|postgres-analysis|mysql-analysis|mariadb-analysis|minio|all'

if [[ $# -ne 2 || ! "$1" =~ ^(start|stop|status|logs)$ || ! "$2" =~ ^($profiles)$ ]]; then
    echo "Usage: $0 {start|stop|status|logs} {$profiles}" >&2
    exit 2
fi
if [[ ! -f "$state_dir/secrets.env" || ! -f "$state_dir/docker/config.json" ]]; then
    echo "Missing private environment setup; follow README.md before starting services." >&2
    exit 1
fi
if ! socket="$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' "$machine")" || [[ ! -S "$socket" ]]; then
    echo "Podman machine '$machine' has no active API socket. See tests/datasource/environment/README.md." >&2
    exit 1
fi

profile="$2"
compose_profile="$profile"
if [[ "$profile" == all ]]; then
    compose_profile='*'
fi
compose=(docker --config "$state_dir/docker" --host "unix://$socket" compose --progress quiet
    --project-name "$project" --env-file "$state_dir/secrets.env"
    -f "$repo_root/tests/datasource/environment/compose.yaml" --profile "$compose_profile")
cd "$repo_root"
python="$repo_root/.venv/bin/python"

case "$1" in
    start)
        "$python" -c 'import shutil, sys; sys.exit(0 if shutil.disk_usage(sys.argv[1]).free >= 8 * 1024**3 else "Need at least 8 GiB free before starting qualification")' "$repo_root"
        if [[ "$profile" == trino || "$profile" == all ]]; then
            "${compose[@]}" up -d --wait --wait-timeout 180 postgres-analysis
            "$python" -c 'from tests.datasource.environment.postgres_analysis import setup_catalog; setup_catalog()'
            # Named volumes must be writable by the pinned Trino image's uid 1000.
            "${compose[@]}" run --rm --no-deps --user root --entrypoint sh trino \
                -c 'mkdir -p /warehouse && chown 1000:1000 /warehouse'
        fi
        "${compose[@]}" up -d --wait --wait-timeout 180
        if [[ "$profile" == postgres-analysis || "$profile" == trino || "$profile" == all ]]; then
            "$python" -m tests.datasource.environment.postgres_analysis
        fi
        if [[ "$profile" == mysql-analysis || "$profile" == all ]]; then
            "$python" -m tests.datasource.environment.mysql_analysis
        fi
        if [[ "$profile" == mariadb-analysis || "$profile" == all ]]; then
            "$python" -c '
import json
from tests.datasource.environment.mysql_analysis import MARIADB_PORT, initialize

print(json.dumps(initialize(port=MARIADB_PORT), sort_keys=True))
'
        fi
        ;;
    stop)
        case "$profile" in
            all) "${compose[@]}" stop ;;
            *) "${compose[@]}" stop "$profile" ;;
        esac
        ;;
    status) "${compose[@]}" ps -a --orphans=false ;;
    logs)
        case "$profile" in
            all) "${compose[@]}" logs --tail 60 ;;
            *) "${compose[@]}" logs --tail 60 "$profile" ;;
        esac
        ;;
esac
