#!/usr/bin/env bash
# Runs the real /opt/choplab deployment backup on a disposable Linux Docker runner.
set -euo pipefail
mode=${1:?sqlite or postgres}
[[ "$mode" == sqlite || "$mode" == postgres ]]
root=/opt/choplab
test_dir="$RUNNER_TEMP/backup-$mode"
mkdir -p "$test_dir/bin"
trap 'echo "Backup test failed; diagnostic logs:" >&2; find "$test_dir" -maxdepth 1 -name "*.log" -exec cat {} \; >&2' ERR
sudo install -d -o "$USER" -g "$(id -gn)" -m 700 "$root" "$root/secrets" "$root/backups" "$root/releases"
release="$root/releases/backup-check"
mkdir -p "$release"
cp deploy/{compose.yaml,compose.neon.yaml,Caddyfile,backup.sh} "$release/"
printf '%s\n' "$release" > "$root/current-release"
printf '%s\n' choplab:check > "$root/current-image"
printf '%s\n' 'CHOPLAB_DOMAIN=localhost' 'ACME_EMAIL=check@example.com' > "$root/.env"
cp deploy/secrets/owner_password_hash "$root/secrets/"
compose=(docker compose --project-name choplab --project-directory "$root" --env-file "$root/.env" -f "$release/compose.yaml")
if [[ "$mode" == postgres ]]; then
  cp deploy/secrets/database_url "$root/secrets/database_url"
  compose+=(-f "$release/compose.neon.yaml")
fi
app=$("${compose[@]}" ps --all --quiet app)
test -n "$app"
running() { test "$(docker inspect --format '{{.State.Running}}' "$app")" = "$1"; }
complete_count() { find "$root/backups" -maxdepth 1 -type d -name 'backup-*' | wc -l; }
no_partial() { test -z "$(find "$root/backups" -maxdepth 1 -type d -name '.pending-*' -print -quit)"; }
export REAL_DOCKER
REAL_DOCKER=$(command -v docker)
export BACKUP_TEST_DIR="$test_dir"
cat > "$test_dir/bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "${BACKUP_FAULT:-}:$*" in
  metadata:*"choplab.metadata backup"*) printf '{"partial":'; exit 1 ;;
  invalid:*"choplab.metadata backup"*) printf 'invalid json'; exit 0 ;;
  archive:*"--entrypoint tar"*) printf 'partial archive'; exit 1 ;;
  invalid-archive:*"--entrypoint tar"*) printf 'invalid archive'; exit 0 ;;
  recovery:*"start app"*) exit 1 ;;
  pause:*"--entrypoint tar"*)
    touch "$BACKUP_TEST_DIR/archiving"
    for i in {1..60}; do
      if [[ -e "$BACKUP_TEST_DIR/continue" ]]; then break; fi
      sleep 1
    done
    test -e "$BACKUP_TEST_DIR/continue"
    ;;
esac
exec "$REAL_DOCKER" "$@"
SH
chmod 700 "$test_dir/bin/docker"
run_backup() { PATH="$test_dir/bin:$PATH" bash "$release/backup.sh"; }
"${compose[@]}" stop app
"${compose[@]}" run -T --rm --no-deps -v "$PWD/scripts/verify_backup.py:/verify_backup.py:ro" \
  app python /verify_backup.py seed
"${compose[@]}" up -d --no-deps --wait --wait-timeout 180 app
app=$("${compose[@]}" ps --all --quiet app)
before=$(complete_count)
run_backup > "$test_dir/success.log" 2>&1
running true
test "$(complete_count)" -eq "$((before + 1))"
grep -q 'Coordinated backup saved' "$test_dir/success.log"
pair=$(find "$root/backups" -maxdepth 1 -type d -name 'backup-*' | sort | tail -1)
test -s "$pair/metadata.json" && test -s "$pair/audio.tar.gz"
no_partial
for fault in metadata invalid archive invalid-archive recovery; do
  before=$(complete_count)
  if BACKUP_FAULT="$fault" run_backup > "$test_dir/$fault.log" 2>&1; then
    echo "Fault $fault incorrectly reported backup success." >&2
    exit 1
  fi
  test "$(complete_count)" -eq "$before"
  ! grep -q 'Coordinated backup saved' "$test_dir/$fault.log"
  no_partial
  if [[ "$fault" == recovery ]]; then
    running false
    grep -q 'app recovery failed' "$test_dir/$fault.log"
    "${compose[@]}" up -d --no-deps --wait --wait-timeout 180 app
  else
    running true
  fi
done
# A concurrent real run must fail without restarting the stopped writer.
BACKUP_FAULT=pause run_backup > "$test_dir/concurrent-first.log" 2>&1 &
first=$!
for i in {1..60}; do
  if [[ -e "$test_dir/archiving" ]]; then break; fi
  sleep 1
done
test -e "$test_dir/archiving"
running false
if run_backup > "$test_dir/concurrent-second.log" 2>&1; then
  echo "Concurrent backup bypassed the writer lock." >&2
  exit 1
fi
grep -q 'Another backup is running' "$test_dir/concurrent-second.log"
running false
touch "$test_dir/continue"
wait "$first"
running true
no_partial
# An intentionally stopped service stays stopped on both success and failure.
"${compose[@]}" stop app
run_backup > "$test_dir/stopped.log" 2>&1
running false
if BACKUP_FAULT=archive run_backup > "$test_dir/stopped-failure.log" 2>&1; then exit 1; fi
running false
no_partial

# Restore the first complete pair into a fresh volume and dedicated empty database.
restore_volume="choplab_backup_restore_$mode"
docker volume create "$restore_volume" > /dev/null
docker run -i --rm --network none --user 10001:10001 --entrypoint tar \
  -v "$restore_volume:/data" choplab:check -C /data -xzf - < "$pair/audio.tar.gz"
if [[ "$mode" == postgres ]]; then
  postgres=$(docker compose --project-directory deploy -f deploy/compose.yaml \
    -f deploy/compose.neon.yaml -f deploy/compose.postgres-test.yaml ps -q postgres)
  docker exec "$postgres" psql -U choplab -d choplab -c 'CREATE DATABASE backup_restore;' > /dev/null
  sed 's@/choplab?@/backup_restore?@' deploy/secrets/database_url > "$root/secrets/database_url"
else
  "${compose[@]}" run -T --rm --no-deps -v "$restore_volume:/data" \
    -v "$PWD/scripts/verify_backup.py:/verify_backup.py:ro" app python /verify_backup.py reset-sqlite
fi
"${compose[@]}" run -T --rm --no-deps -v "$restore_volume:/data" \
  app python -m choplab.metadata restore < "$pair/metadata.json"
"${compose[@]}" run -T --rm --no-deps -v "$restore_volume:/data" \
  -v "$PWD/scripts/verify_backup.py:/verify_backup.py:ro" \
  app python /verify_backup.py verify < "$pair/metadata.json"
if [[ "$mode" == postgres ]]; then
  cp deploy/secrets/database_url "$root/secrets/database_url"
fi
"${compose[@]}" run -T --rm --no-deps -v "$PWD/scripts/verify_backup.py:/verify_backup.py:ro" \
  app python /verify_backup.py cleanup
"${compose[@]}" up -d --no-deps --wait --wait-timeout 180 app
running true
docker volume rm "$restore_volume" > /dev/null
echo "Real $mode backup/paired restore, export/JSON/archive/recovery failures, stopped-state and concurrent writer checks passed."
