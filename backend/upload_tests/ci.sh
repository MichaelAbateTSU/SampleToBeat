#!/usr/bin/env bash
set -euo pipefail
mkdir -p upload-results
export TEST_TOKEN="$(openssl rand -hex 32)"
echo "::add-mask::$TEST_TOKEN"
docker build -q -t spool-cache -f backend/upload_tests/Dockerfile . >/dev/null
docker run --rm --memory=512m --memory-swap=512m -e TEST_TOKEN spool-cache \
  python -m pytest -q upload_tests/test_uploads.py
cleanup() { docker rm -f cache >/dev/null 2>&1 || true; }
trap cleanup EXIT
run_server() {
  mode="$1"; shift
  docker run -d --name cache --memory=512m --memory-swap=512m --cpus=0.1 \
    -p 10000:10000 -e TEST_TOKEN -e CACHE_MODE="$mode" "$@" spool-cache >/dev/null
  docker inspect cache --format \
    '{"memory":{{.HostConfig.Memory}},"memory_swap":{{.HostConfig.MemorySwap}},"nano_cpus":{{.HostConfig.NanoCpus}}}' \
    >"upload-results/$mode-limits.json"
  for i in $(seq 180); do
    if curl -fsS --max-time 5 http://127.0.0.1:10000/api/health >/dev/null 2>&1; then return; fi
    if ! docker inspect cache --format '{{.State.Running}}' | grep -q true; then
      docker logs cache; exit 1
    fi
    sleep 1
  done
  docker logs cache; exit 1
}
stop_server() {
  docker logs cache >"upload-results/$1.log" 2>&1
  docker inspect cache --format '{{.State.OOMKilled}}' | grep -q false
  docker rm -f cache >/dev/null
}
for mode in baseline bounded; do
  run_server "$mode"
  python3 backend/upload_tests/measure.py --no-auth --expect 401 \
    --out "upload-results/$mode-unauth.json"
  python3 backend/upload_tests/measure.py --out "upload-results/$mode-full.json"
  stop_server "$mode"
done
run_server bounded --tmpfs /data:rw,size=64m,uid=10001,gid=10001
python3 backend/upload_tests/measure.py --size 16777216 --expect 507 \
  --out upload-results/reserve-full.json
stop_server reserve
run_server bounded --tmpfs /tmp:rw,size=8m,uid=10001,gid=10001
python3 backend/upload_tests/measure.py --size 16777216 --expect 507 \
  --out upload-results/spool-full.json
stop_server spool-full
if grep -rqF "$TEST_TOKEN" upload-results; then echo "token leak"; exit 1; fi
python3 - <<'PY'
import json
from pathlib import Path
for mode in ("baseline","bounded"):
    r=json.loads(Path(f"upload-results/{mode}-full.json").read_text())
    peak=r["after_request"]["memory.peak"]
    samples=r["samples"]
    print(mode,"kernel_peak",peak,"headroom",536870912-peak,
          "file_max",max(int(s["memory.stat"]["file"]) for s in samples),
          "anon_max",max(int(s["memory.stat"]["anon"]) for s in samples),
          "health_timeouts",sum("error" in h for h in r["health"]),
          "max_health",max(h["latency_s"] for h in r["health"]),
          "events",r["after_request"]["memory.events"])
    auth=json.loads(Path(f"upload-results/{mode}-unauth.json").read_text())
    assert auth["received_bytes"]==0
    limits=json.loads(Path(f"upload-results/{mode}-limits.json").read_text())
    assert limits=={"memory":536870912,"memory_swap":536870912,"nano_cpus":100000000}
    assert r["expected_file_bytes"]==1073741824 and r["expected_body_bytes"]==1073742002
    assert all(h.get("status")==200 and h["latency_s"]<5 for h in r["health"])
    if mode=="bounded":
        assert peak<=536870912-64*1024*1024, peak
        assert int(r["after_request"]["memory.events"]["max"])==0
        assert int(r["after_request"]["memory.events"]["oom_kill"])==0
PY
