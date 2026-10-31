#!/usr/bin/env bash
set -euo pipefail
ROOT="$(pwd)"
WORK="$(mktemp -d)"
export PROBE_TOKEN="$(openssl rand -hex 32)"
echo "::add-mask::$PROBE_TOKEN"
docker build -q -t spool-probe tools/render-spool-probe >/dev/null
mkdir -p spool-results
cleanup() { docker rm -f spool >/dev/null 2>&1 || true; }
trap cleanup EXIT
run_case() {
  name="$1"; expected="$2"; shift 2
  docker run -d --name spool --memory=512m --memory-swap=512m -p 10000:10000 \
    -e PROBE_TOKEN "$@" spool-probe >/dev/null
  for i in $(seq 120); do
    if curl -fsS http://127.0.0.1:10000/healthz >/dev/null 2>&1; then break; fi
    if ! docker inspect spool --format '{{.State.Running}}' | grep -q true; then
      docker logs spool; exit 1
    fi
    sleep 1
  done
  test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:10000/status)" = 401
  test "$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:10000/probe)" = 401
  python3 tools/render-spool-probe/client.py --url http://127.0.0.1:10000 \
    --allow-http --status --out "spool-results/$name-status.json"
  python3 tools/render-spool-probe/client.py --url http://127.0.0.1:10000 \
    --allow-http --size 16MiB --expect-status "$expected" --out "spool-results/$name.json"
  docker logs spool >"spool-results/$name.log" 2>&1
  docker inspect spool --format '{{.State.OOMKilled}}' | grep -q false
  docker rm -f spool >/dev/null
}
run_case durable 200 -e PROBE_DURABLE_BRANCH=true
run_case sqlite-control 200 -e PROBE_DURABLE_BRANCH=false
run_case reserve 507 --tmpfs /data:rw,size=64m,uid=10001,gid=10001
run_case spool-full 400 --tmpfs /tmp:rw,size=8m,uid=10001,gid=10001
python3 - <<'PY'
import json
from pathlib import Path
def body(name):
    return json.loads(Path(f"spool-results/{name}.json").read_text())["response"]["body"]
a = body("durable")
assert a["release_calls"] == 3, a
assert a["spool_rolled"] and a["saved_bytes"] == 16 * 1024 * 1024
assert a["reserve_bytes"] == 2048 * 1024 * 1024
assert a["production_open_spools_before_probe_cleanup"] == 0
assert body("sqlite-control")["release_calls"] == 0
r = body("reserve")
assert r["production_status"] == 507
assert r["phase"] == "save" and not r["part_exists_before_probe_cleanup"]
s = body("spool-full")
assert s["phase"] == "parser" and "ENOSPC" in s["errors"], s
assert not s["part_exists_before_probe_cleanup"]
for p in Path("spool-results").glob("*.json"):
    if p.stem.endswith("-status"):
        assert json.loads(p.read_text())["body"]["parser_spool_max_size"] == 1024 * 1024
        continue
    b = json.loads(p.read_text())["response"]["body"]
    print(p.stem, {k:b.get(k) for k in ("source_sha","phase","production_status","errors","release_calls",
          "production_open_spools_before_probe_cleanup","sampled")})
PY
if grep -rqF "$PROBE_TOKEN" spool-results; then echo "secret leak"; exit 1; fi
