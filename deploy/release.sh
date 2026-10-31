#!/usr/bin/env bash
set -euo pipefail
root=/opt/choplab
release=$(cd "$(dirname "$0")" && pwd)
image=${1:?Pass the loaded choplab image tag}
export CHOPLAB_IMAGE="$image" CADDY_CONFIG="$release/Caddyfile"
compose=(docker compose --project-name choplab --project-directory "$root" --env-file "$root/.env" -f "$release/compose.yaml")
test -s "$root/secrets/owner_password_hash"
previous=$(cat "$root/current-image" 2>/dev/null || true)
previous_release=$(cat "$root/current-release" 2>/dev/null || true)
"${compose[@]}" config --quiet
"${compose[@]}" run --rm --no-deps proxy caddy validate --config /etc/caddy/Caddyfile
if ! "${compose[@]}" up -d --wait --wait-timeout 180; then
  echo "Deployment did not become healthy; attempting previous image." >&2
  if [[ -n "$previous" && -n "$previous_release" ]]; then
    export CHOPLAB_IMAGE="$previous"
    export CADDY_CONFIG="$previous_release/Caddyfile"
    docker compose --project-name choplab --project-directory "$root" --env-file "$root/.env" \
      -f "$previous_release/compose.yaml" up -d --wait --wait-timeout 180
  fi
  exit 1
fi
printf '%s\n' "$image" > "$root/current-image"
printf '%s\n' "$release" > "$root/current-release"
echo "Backend ready. Verify HTTPS, owner login and an import/export in the browser."
