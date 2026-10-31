#!/usr/bin/env bash
# CI-only validation of the temporary upload probe (Linux + Docker).
# Uses a throwaway synthetic token that is masked and never echoed.
set -euo pipefail
cd "$(dirname "$0")"
PROBE_TOKEN="$(openssl rand -hex 32)"
echo "::add-mask::${PROBE_TOKEN}"
export PROBE_TOKEN
WORK="$(mktemp -d)"

docker build -q -t upload-probe:ci . >/dev/null
docker network create probe-net >/dev/null
# 512 MiB mirrors Render Free; the probe must stay far below it.
docker run -d --name probe --network probe-net --memory 512m --memory-swap 512m \
  -e PROBE_TOKEN -e PORT=10000 -p 10000:10000 upload-probe:ci >/dev/null
for _ in $(seq 30); do curl -fsS http://127.0.0.1:10000/healthz >/dev/null && break; sleep 1; done

echo "== direct container (Content-Length + chunked, up to cap + over-max) =="
python3 run_suite.py --url http://127.0.0.1:10000 --allow-http --include-stream-over-max \
  --out "$WORK/direct.jsonl"
echo "container memory.peak bytes: $(docker exec probe cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo n/a)"
docker inspect probe --format 'OOMKilled={{.State.OOMKilled}}'

echo "== TLS proxy controls (classifier validation) =="
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost" -keyout "$WORK/key.pem" -out "$WORK/cert.pem" 2>/dev/null
cat >"$WORK/nginx.conf" <<'EOF'
events {}
http {
  ssl_certificate /certs/cert.pem; ssl_certificate_key /certs/key.pem;
  proxy_http_version 1.1;
  # 8443: buffers whole request before contacting upstream (expect "inconclusive")
  server { listen 8443 ssl; client_max_body_size 2g; proxy_request_buffering on;
           location / { proxy_pass http://probe:10000; } }
  # 9443: streams request body to upstream (expect "proven")
  server { listen 9443 ssl; client_max_body_size 2g; proxy_request_buffering off;
           location / { proxy_pass http://probe:10000; } }
  # 7443: default 1m body limit (expect platform_rejected, no X-Upload-Probe)
  server { listen 7443 ssl;
           location / { proxy_pass http://probe:10000; } }
}
EOF
docker run -d --name edge --network probe-net -v "$WORK/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$WORK:/certs:ro" -p 7443:7443 -p 8443:8443 -p 9443:9443 nginx:1.27-alpine >/dev/null
sleep 3
C="python3 client.py --cafile $WORK/cert.pem --pause-s 8"

$C --url https://localhost:9443 --size 100mib --two-phase >"$WORK/stream.json"
jq -e '.outcome=="ok" and .two_phase.streaming=="proven"' "$WORK/stream.json"
$C --url https://localhost:9443 --size cap --multipart --framing chunked --two-phase >"$WORK/stream-cap.json"
jq -e '.outcome=="ok" and .two_phase.streaming=="proven" and .body_bytes==1073742002' "$WORK/stream-cap.json"

$C --url https://localhost:8443 --size 100mib --two-phase >"$WORK/buffer.json"
jq -e '.outcome=="ok" and .two_phase.streaming=="inconclusive" and .two_phase.max_server_received_during_pause==0' "$WORK/buffer.json"
$C --url https://localhost:8443 --size 100mib --framing chunked >"$WORK/reframe.json"
jq -e '.outcome=="ok" and .edge_reframed==true' "$WORK/reframe.json"

set +e
$C --url https://localhost:7443 --size 100mib >"$WORK/platform.json"; rc=$?
set -e
jq -e '.outcome=="platform_rejected" and .response.status==413 and .response.from_app==false' "$WORK/platform.json"
test "$rc" -ne 0

echo "== secret hygiene =="
docker logs probe >"$WORK/probe.log" 2>&1; docker logs edge >"$WORK/edge.log" 2>&1
if grep -rqF "$PROBE_TOKEN" "$WORK"/*.json "$WORK"/*.jsonl "$WORK"/*.log; then
  echo "token leaked"; exit 1; fi
echo "no token in logs/results"
for f in stream stream-cap buffer reframe platform; do
  jq -c '{label,outcome,status:.response.status,from_app:.response.from_app,framing_seen:.response.body.framing_seen,edge_reframed,streaming:.two_phase.streaming,seen:.two_phase.max_server_received_during_pause}' "$WORK/$f.json"
done
docker rm -f probe edge >/dev/null; docker network rm probe-net >/dev/null
