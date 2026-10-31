#!/usr/bin/env bash
set -euo pipefail
root=/opt/choplab
umask 077
# Hold this through writer recovery, not just through snapshot creation.
exec 9>"$root/backup.lock"
if ! flock -n 9; then
  echo "Another backup is running; no services were changed." >&2
  exit 1
fi
release=$(cat "$root/current-release")
export CHOPLAB_IMAGE
CHOPLAB_IMAGE=$(cat "$root/current-image")
export CADDY_CONFIG="$release/Caddyfile"
compose=(docker compose --project-name choplab --project-directory "$root" --env-file "$root/.env" -f "$release/compose.yaml")
if [[ -s "$root/secrets/database_url" ]]; then
  compose+=(-f "$release/compose.neon.yaml")
fi
container=$("${compose[@]}" ps --all --quiet app)
if [[ -z "$container" ]]; then
  echo "No deployed app container exists; refusing backup." >&2
  exit 1
fi
was_running=$(docker inspect --format '{{.State.Running}}' "$container")
pending=$(mktemp -d "$root/backups/.pending-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
completed="${pending/\/.pending-/\/backup-}"
recover() {
  local status=$?
  trap - EXIT
  if [[ "$was_running" == true ]]; then
    if ! "${compose[@]}" start --wait --wait-timeout 180 app; then
      echo "Backup failed: app recovery failed; inspect the service before using the studio." >&2
      status=1
    fi
  fi
  if [[ "$status" == 0 ]]; then
    if mv "$pending" "$completed"; then
      echo "Coordinated backup saved in $completed. Copy the entire directory off this host."
    else
      echo "Backup publication failed." >&2
      status=1
    fi
  else
    echo "Backup failed; no completed backup was published." >&2
  fi
  if [[ -d "$pending" ]]; then
    rm -f "$pending/metadata.json" "$pending/audio.tar.gz"
    rmdir "$pending"
  fi
  exit "$status"
}
trap recover EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"${compose[@]}" stop app
# Keep both snapshots under the same stopped single-writer window.
"${compose[@]}" run -T --rm --no-deps app python -m choplab.metadata backup > "$pending/metadata.json"
python3 -m json.tool "$pending/metadata.json" > /dev/null
docker run --rm --network none --user 10001:10001 --entrypoint tar \
  -v choplab_audio:/data:ro "$CHOPLAB_IMAGE" -C /data -czf - . > "$pending/audio.tar.gz"
gzip -t "$pending/audio.tar.gz"
