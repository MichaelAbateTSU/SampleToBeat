#!/usr/bin/env bash
set -euo pipefail
root=/opt/choplab
release=$(cat "$root/current-release")
export CHOPLAB_IMAGE
CHOPLAB_IMAGE=$(cat "$root/current-image")
export CADDY_CONFIG="$release/Caddyfile"
compose=(docker compose --project-name choplab --project-directory "$root" --env-file "$root/.env" -f "$release/compose.yaml")
if [[ -s "$root/secrets/database_url" ]]; then
  compose+=(-f "$release/compose.neon.yaml")
fi
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$root/backups/audio-$stamp.tar.gz"
snapshot="$root/backups/metadata-$stamp.json"
umask 077
trap '"${compose[@]}" start app' EXIT
"${compose[@]}" stop app
# Keep both snapshots under the same stopped single-writer window.
"${compose[@]}" run -T --rm --no-deps app python -m choplab.metadata backup > "$snapshot"
python3 -m json.tool "$snapshot" > /dev/null
docker run --rm --network none --user 10001:10001 --entrypoint tar \
  -v choplab_audio:/data:ro "$CHOPLAB_IMAGE" -C /data -czf - . > "$backup"
echo "Coordinated audio archive and metadata snapshot saved. Copy both off this host; host deletion removes both."
