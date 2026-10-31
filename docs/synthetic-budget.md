# Isolated synthetic storage and transfer budget

This opt-in measurement uses **only disposable authenticated TLS PostgreSQL and
TLS MinIO**, never Render/Neon credentials or provider resources. It runs the
existing generated short HTTP journey, kills the application, destroys its exact
synthetic cache, starts a replacement, and verifies once. It does not repeat full
downloads unnecessarily. An optional exact document-linked original-object read
checks actual uploaded bytes after replacement, rather than inferring original
durability from `source.content_hash`.

On the measurement branch, run the existing isolated service workflow:

```powershell
gh workflow run durable-integration.yml --repo MichaelAbateTSU/SampleToBeat `
  --ref michaelabatetsu-interrupted-job-recovery -f budget_only=true
```

The `durable-integration-evidence` artifact contains `budget-report.json`,
`budget-container.json`, and sanitized test output. Each phase has cumulative
traffic counters and a catalog/physical-object snapshot, so subtract consecutive
phase counters for phase costs. Object keys, auth headers, URLs, private server
logs, credential files and document bodies are not published.

## Run against a supplied integrated image

Reuse the workflow's isolated `durable-acceptance` network and certificates. Build
the acceptance image from the exact integrated image/revision, adding only pytest
and httpx as the existing workflow does. Mount these measurement scripts and the
**matching integrated** `scripts\verify_durable_http.py`, not older application
source on `PYTHONPATH`. The image provides the actual installed application.
The services are synthetic, guarded to host `postgres`, database
`choplab_recovery_test`, user `choplab_test`, and TLS object host `s3`. The fixture
uses a random private object prefix; it never wipes a shared bucket.

The following Linux Docker invocation is for an already prepared isolated
environment; paths inside the container use Linux separators:

```sh
docker create --name synthetic-budget-check --network durable-acceptance \
  --entrypoint python --read-only --tmpfs /tmp:uid=10001,gid=10001 \
  -e CHOPLAB_DISPOSABLE_POSTGRES_TEST=yes -e CHOPLAB_SYNTHETIC_BUDGET=yes \
  -e CHOPLAB_BUDGET_ORIGINAL_READ=yes \
  -e CHOPLAB_TEST_POSTGRES_URL='postgresql://choplab_test:synthetic-ci-password@postgres:5432/choplab_recovery_test?sslmode=verify-full' \
  -e PGSSLROOTCERT=/pg/server.crt -e CHOPLAB_TEST_S3_CA=/s3/public.crt \
  -e CHOPLAB_BUDGET_REVISION=EXACT_INTEGRATED_SHA \
  -e CHOPLAB_BUDGET_REPORT=/working/budget-report.json \
  -v "$PG_CERT:/pg/server.crt:ro" -v "$S3_CERT:/s3/public.crt:ro" \
  -v "$MEASUREMENT_BACKEND:/workspace/backend:ro" \
  -v "$INTEGRATED_SCRIPTS:/workspace/scripts:ro" \
  -v synthetic-budget-working:/working INTEGRATED_ACCEPTANCE_IMAGE \
  -m pytest -q -s -p no:cacheprovider --basetemp=/working/budget \
  /workspace/backend/storage_tests/test_budget_measurement.py
docker start -a synthetic-budget-check
docker cp synthetic-budget-check:/working/budget-report.json ./budget-report.json
docker inspect synthetic-budget-check \
  --format '{"OOMKilled":{{.State.OOMKilled}},"ExitCode":{{.State.ExitCode}},"MemoryLimit":{{.HostConfig.Memory}}}'
```

Supply only the known throwaway service credentials above. Do not substitute a
provider DSN; the fixture rejects it. The working volume must be owned by UID/GID
10001. Use unique container/volume names for independent runs. Remove only those
exact synthetic containers/network/volume after copying evidence. The existing
workflow does this in its `always()` cleanup. An OOM-killed container may not
produce a report; preserve container state instead of claiming successful data.

For the eventual **1,200-second complete** run, additionally mount the previously
prepared session companion directory read-only and set:

```sh
-v "$SESSION_FILES:/companions:ro" \
-e CHOPLAB_BUDGET_SECONDS=1200 \
-e CHOPLAB_BUDGET_LONG_COMPANION=/companions/verify_long_durable_http.py
```

Keep its sibling `verify_import_interruption.py`. Long mode uses the companion's
explicit 512-MiB decoded-source byte guard and 1,800-second checksum/job bounds;
advertised duration/upload caps must permit the fixture before any mutation.
The 76,800,044-byte input and approximately 460,800,000-byte decoded payload can
require substantial service memory, disk, storage and transfers. Do not execute
this mode until the integrated workload and isolated quota budget are approved.
A future capped run may add `--memory=512m --memory-swap=512m --cpus=1`, but a
successful uncapped run is not 512-MiB feasibility evidence.

## Accounting and measurement limits

| Direction/category | Observation |
|---|---|
| Render workspace to Neon objects | S3 PUT/UploadPart body bytes read/buffered; logical call and every HTTP attempt/status, including retries |
| Neon objects to Render workspace | GetObject body bytes actually consumed, separately from HEAD/LIST/control operations |
| Render workspace to acceptance client | HTTP response body bytes consumed, including JSON/ranges/audio/export |
| Acceptance client to Render | Multipart request body length; not Render outbound |
| Storage | Unique catalog live/published/uploading/retired/deleted declared bytes; physical MinIO LIST totals at each checkpoint |
| Artifacts | Document-linked original/decoded/features/stems/variation/preview/export/etc. counts and sizes |
| Disk | Cache plus dedicated TMPDIR logical/allocated bytes sampled every 100 ms, de-duplicated by inode |
| Resource/health | Shared-container cgroup current/peak/max/swap/events where available; observed health responses including intentional replacement downtime |

`render_outbound_observed_payload_floor` is object upload consumption/buffering
plus application response consumption. The two directions of object payload are
also reported as a **directional sum, not a Neon billing claim**. Uploaded
original + decoded objects and two served decoded downloads belong to distinct
directions; do not multiply object GET bytes into Render outbound a second time.
The optional original read adds Neon-to-workspace payload, not a client response.

SDK body reads/buffers and declared request lengths are not proof of bytes
acknowledged by MinIO. Interrupted sends and retries can have unknown partial
wire transfer; each attempt is retained rather than collapsed into success.
Failed GET error bodies, PostgreSQL protocol traffic, TLS/HTTP headers, socket
retransmissions, SDK fingerprint reads, provider probes/static frontend traffic,
other branch/project consumers and actual provider metering are unobserved.
Health sampling and inventory observer LIST/SQL traffic are excluded from the
application totals. Object controls/deletions are counted, but bytes deleted are
storage effects, not reclaimed transfer allowance.

MinIO physical deletion **does not model Neon soft retention**. Retired catalog
bytes and before/after physical inventory are exposed, without asserting that
Neon immediately releases storage quota. No Neon retention duration or quota
reclamation can be inferred. Cached files may remain after metadata retirement;
sampled occupancy explicitly includes them.

`provider_conservative_recorded_bytes_without_reclamation` adds every recorded
object size across live, retired and deleted tombstones rather than subtracting
deleted objects. This is not an upper bound if there are unknown-size in-flight
uploads, multipart parts, historical objects absent from the catalog, other
branches or outside consumers. The unknown-size catalog count is reported.
An empty provider S3 listing cannot establish reclaimed capacity, and delayed
Overview storage/network counters cannot establish exact transfer usage. Budget
from observed payloads and retained-recorded bytes conservatively until provider
metering/retention settles; do not prescribe redundant full downloads merely
because a dashboard has not caught up.

Disk peaks are lower bounds: between-sample transients, open-unlinked files and
files outside cache/TMPDIR are unobserved. Cgroup memory includes the Python
measurement process and application child; it excludes the separate DB/MinIO
services. The probe adds overhead and is a budget observation, not an unmodified
production benchmark. HTTP GET verification covers decoded and representative
studio/export objects, not every possible library asset.

There is no original-download HTTP endpoint. A real-provider original-byte check
would require separately authorized, private read-only metadata and exact object
credentials scoped to the disposable project. This guarded MinIO test must not
be repointed at a live provider to obtain that evidence.
