#!/usr/bin/env bash
set -euo pipefail
root=/opt/choplab
release=$(cat "$root/current-release")
export CHOPLAB_IMAGE
CHOPLAB_IMAGE=$(cat "$root/current-image")
export CADDY_CONFIG="$release/Caddyfile"
compose=(docker compose --project-name choplab --project-directory "$root" --env-file "$root/.env" -f "$release/compose.yaml")
backup="$root/backups/audio-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
umask 077
trap '"${compose[@]}" start app' EXIT
"${compose[@]}" stop app
# A stopped single-writer process makes the SQLite WAL and audio snapshot consistent.
docker run --rm --network none --user 10001:10001 --entrypoint tar \
  -v choplab_audio:/data:ro "$CHOPLAB_IMAGE" -C /data -czf - . > "$backup"
echo "Backup saved on the VM. Copy it off this VM; VM deletion also deletes this backup."
