# Disposable real multipart/spool preflight

This is **not** the earlier network-discard probe. It exercises real FastAPI
`File(...)` parsing into Starlette's `UploadFile`, then imports and calls
ChopLab's **unchanged** `api._save_upload`. It never decodes synthetic bytes,
starts jobs, connects to a database, accesses Neon/S3, or exposes product routes.
The original 1,024 MiB file cap and hosted 2,048 MiB reserve remain enforced.

## Pinning and fidelity

Docker clones this public repo at exactly
`fd53e2af147f69d6ab44b66be479067164267d02`, checks the SHA, and installs its
backend with the production `postgres,storage` extras. No source is rewritten.
Parser versions are explicitly fixed to those in that revision's requirements:
FastAPI 0.157.0, Starlette 1.9.0, python-multipart 0.0.32, uvicorn 0.54.0.
All installed versions are retained in `/installed.txt`.

Compared with hosting revision `345b44235d3343b73ee55b266e1b1b6ddd2c81ae`:
`_save_upload`, `_release_file_cache`, the `File(...)` route signature, cap/reserve
declarations, dependency manifest and parser lock versions are unchanged.
Run the comparison again against the **final** hosting tree and deployed
dependency versions before applying this result to production.

Runtime responses include these source hashes (SHA-256 of `inspect.getsource`):

| Component | SHA-256 |
|---|---|
| `_save_upload` | `1ea44e373c6b0b7f24206d56f2e413b8d1a609292c03922ee343933c3c98aa32` |
| `_release_file_cache` | `11a0dd176eed743a39ffaaba2c945ba312abfb96f71e1c108f400e4b61458387` |
| `MultiPartParser` | `e33a3e235ef1f2cb3ee59b4f191ea24d0f661d873fdf5d1684fc349f4ef9f34b` |

The wrapper uses hosted mode with a locally generated synthetic password-hash
file, but does not serve `api.app` or run its lifespan. `durable._settings` is
set to a test-only sentinel, making the **real** `durable.enabled()` return true
without calling `durable.configure()` or supplying storage credentials.
This exercises the unchanged production `_release_file_cache` at 8 MiB intervals
and completion. `PROBE_DURABLE_BRANCH=false` selects a clearly labelled SQLite
control (no cache-release calls); it is **not** a production fallback.
No SQLite database is needed for this focused function control.

Instrumentation delegates to the original spool constructor/cache-release
function and observes their calls; it never changes parser limits or upload guards.
The wrapper monitors cgroup memory, `/tmp` and `/data` free bytes at 50 ms
intervals and records mount identity, device ID, filesystem size/free inodes,
RSS high-water mark and kernel cgroup counters. Sampling may miss brief peaks;
`memory.peak` is the kernel high-water mark when available.

## Endpoints and safety

- `GET /healthz`: anonymous, only data-free readiness and source SHA.
- `GET /status`: **bearer authenticated**, source hashes, parser versions,
  spool threshold and initial disk/cgroup snapshot.
- `POST /probe`: **bearer authenticated before parsing**, Content-Length only.
  Hard request-body ceiling: 1,024 MiB + 1 MiB framing allowance. File cap:
  1,024 MiB via the real production guard. Only one concurrent upload.

The client creates one deterministic synthetic file part. It sends and hashes
the multipart envelope incrementally, separately computes the expected file
digest incrementally, and verifies both the complete received request digest
and the completed saved-file digest/length. Default file size is 1,024 MiB,
total request size **1,073,742,002 B** (178 B overhead). Client file ceiling:
1,024 MiB + 1 byte, allowing one exact 413 negative test.

The server saves only a synthetic `/data/probe-upload.bin`, hashes it in bounded
chunks, then deletes it. It records whether the production parser/function had
already closed spools and removed `.part` before test-only cleanup. Failure
cleanup does not turn a production failure into success: the original HTTP
status/response and phase remain visible. Parser ENOSPC caught and generalized
by FastAPI is captured by a temporary exception observer (no credentials logged).
Access logging is disabled. Tokens come only from env/private files, never argv.

## CI evidence and interpretation

The workflow builds Docker and runs real HTTP multipart requests under a
512 MiB container memory limit. It creates **bounded tmpfs mounts**, never fills
the CI host disk.

| Case (16 MiB synthetic file) | Actual result |
|---|---|
| Durable-enabled branch, real `/tmp` and `/data` | 200, both digests and lengths match; 3 real cache-release calls |
| SQLite control | 200; no cache-release calls |
| `/data` mounted as 64 MiB tmpfs, real 2,048 MiB reserve | Production 507: `Storage reserve reached; delete old projects or clear the cache.` |
| `/tmp` mounted as 8 MiB tmpfs | Real parser ENOSPC, FastAPI **400**: `There was an error parsing the body`; `_save_upload` was never reached |

In all four cases, the production parser closed the spool before probe cleanup.
The reserve rejection also removed `.part`. The kernel reported no OOM.
The successful durable control's cgroup peak was about 155 MB for this small
case; this is **not proof of bounded memory at 1 GiB**. Python RSS high-water
includes import/startup peaks and is not equal to cgroup charged memory.

Spooling happens **before** `_save_upload` runs; its reserve check cannot protect
the parser's `/tmp` write. For a file of size F, the parser spool and destination
coexist during copying. If `/tmp` and `/data` share a filesystem, an empty
1 GiB upload requires roughly **4 GiB initially free**: 1 GiB spool + 1 GiB
destination + 2 GiB reserve, plus metadata/allocation headroom. If different
filesystems, `/tmp` needs >1 GiB and `/data` needs >3 GiB. Actual Render capacity
and full-size parser/cgroup behavior remain **unverified until the live test**.

If the process is OOM-killed, no wrapper report is possible; client disconnect
alone cannot diagnose OOM. Collect provider logs/metrics and post-restart health.
An edge rejection lacks the `X-Upload-Probe` marker; an app 413/507/400 includes
`production_status`, `phase`, original response and filesystem snapshots.
The wrapper does not test durable publication, jobs, audio decode, warm model
caches or existing library occupancy.
The disposable image includes a source checkout and omits the production frontend
build/Numba warm-up. Its filesystem free bytes measure **that temporary service**;
image layers and occupancy can differ in the final service. Confirm final `/tmp`
and `/data` identity/free capacity as well as parser versions before extrapolating
a capacity result. No provider-specific ephemeral capacity is assumed.

## Temporary Render deployment (coordinator only)

- Remote branch: `michaelabatetsu-render-spool-preflight`.
- Runtime **Docker**, root directory `tools/render-spool-probe`, Dockerfile
  `./Dockerfile`, instance **Free**, health check `/healthz`, auto-deploy off.
- Set one private synthetic `PROBE_TOKEN` (32+ characters) in the dashboard.
- Defaults already set in Docker: `CHOPLAB_MODE=hosted`,
  `CHOPLAB_MAX_UPLOAD_MB=1024`, `CHOPLAB_MIN_FREE_MB=2048`,
  `CHOPLAB_DATA=/data`, default tempfile directory `/tmp`.
- Leave `PROBE_DURABLE_BRANCH=true`. No actual password/database/storage secrets,
  no databases/disks, no paid resources or payment method.

From the repository root, Python 3.11+:

```powershell
python tools\render-spool-probe\client.py --url https://<service>.onrender.com --token-dpapi-file <private.dpapi> --out <session-files\spool-render.json>
# small control:
python tools\render-spool-probe\client.py --url https://<service>.onrender.com --size 16MiB --token-dpapi-file <private.dpapi> --out <session-files\small.json>
```

Add `--status` to fetch authenticated `/status` without uploading a body or
printing auth headers; save this initial filesystem/source inspection first.
The new client reuses that checked incremental generator/HTTPS transport and
failure classification; no arbitrary-size client was introduced.

Run CI locally on Linux/Docker: `bash tools/render-spool-probe/ci.sh`.

After evidence is retained, the coordinator deletes the temporary service/token
and authorizes a SHA-guarded delete of **only** the remote preflight branch.
Keep prior published reports and measurement artifacts; nothing is merged.
