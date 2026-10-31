# Combined-limit synthetic budget runner

Test tooling only. No Render/Neon endpoints, accounts, credentials or quotas are
used. Application source is built from the exact supplied SHA; it is never
overlaid with older application modules. The companion's matching
`scripts/verify_durable_http.py` comes from that app SHA.

The 12-second control uses the same deterministic 96-kHz stereo PCM32 samples,
with no ancillary padding. It is **not** the exact-limit case. Combined mode
regenerates one exclusive local file from pinned
`f6d7a9d6e8cab041415b538c5d81111a5712eb08`: exactly 1,073,741,824 bytes,
1,200 seconds, SHA-256
`2057063a6515a4111624b9e89678f8c4853867b7399086a0138dd52817f0c03b`.
Its independently verified eight-key manifest is bound to both HTTP phases.
Generation, hashing, and upload are streamed; the same verified descriptor is
retained for upload. There is no GiB CI artifact and no automatic mutation retry.
An incomplete manifest/unknown commit is a failure, never a guessed resume.

## Execution

After this workflow has been registered by its branch-only control trigger:

```powershell
gh workflow run combined-budget.yml --repo MichaelAbateTSU/SampleToBeat `
  --ref michaelabatetsu-render-free-feasibility `
  -f app_sha=092df4b821a24a33b686eb942dcb0852137503eb -f mode=control
```

**Do not run combined mode before the coordinator supplies the corrected final
hosting SHA.** Dispatch is explicitly gated, rather than inferred from passing
control or advancing branch state:

```powershell
gh workflow run combined-budget.yml --repo MichaelAbateTSU/SampleToBeat `
  --ref michaelabatetsu-render-free-feasibility `
  -f app_sha=EXACT_FINAL_SHA -f mode=combined `
  -f confirm_final_combined_sha=EXACT_FINAL_SHA
```

The workflow archives that exact app and pinned generator, builds the installed
production image, and creates authenticated TLS PostgreSQL and TLS MinIO on an
isolated Docker network. Only known synthetic endpoints are accepted by admin
setup. Configuration files use mode 0600 and are never uploaded.

The host command is:

```sh
sudo --preserve-env=RUNNER_TEMP timeout --signal=TERM --kill-after=30s 6600s \
  python3 .github/benchmarks/full_budget/run.py \
  --root "$RUNNER_TEMP/combined-budget" --app-sha "$APP_SHA" \
  --mode "$BUDGET_MODE" --confirm-final-combined-sha "$CONFIRM_FINAL_SHA"
```

The app alone uses `--memory=512m --memory-swap=512m --cpus=0.1`,
`--no-healthcheck`, read-only root, disk-backed cache and TMPDIR. Docker limits
and cgroup v2 `memory.max=536870912`, `memory.swap.max=0`,
`cpu.max=10000 100000` are asserted for both instances. No production limit
overrides are supplied: mismatched advertised upload/duration caps abort before
mutation.

The real HTTP journey imports and analyzes, generates four bars, opens the
variation in studio, renders a one-bar preview, exports one-bar WAV16 plus
project JSON, and retrieves selected complete assets and a source range.
Decoded source downloads check the entire exact stereo FLOAT/48-kHz RIFF/frame
boundary and checksum, not just HTTP success. After prepare, the old process is
killed, its exact cache and temporary directories destroyed, and its synthetic
lease expired. A cold replacement must reproduce completed job history,
metadata, and all selected asset hashes. A separate admin verifies the exact
document-linked original against its upload hash/size. No job resubmission is
used. HPSS, MP3, eight-bar generations and every possible asset are not included
in this journey; they are not implied to pass.

## Evidence and bounds

Only `evidence/` is uploaded, seven-day retention: sanitized HTTP/S3 records,
stage/phase timing, per-app cgroup limits/current/peak/anon/file/events and CPU,
disk samples, inventory snapshots, health observations and selected Docker
exit/OOM fields. Private config, service/server logs, URLs, headers, documents,
keys, waveform payloads, fixture and auth/acceptance files are excluded.

The app cgroup includes the app and its in-process S3 logging wrapper, **not**
the client, sampler, admin/original verifier, PostgreSQL or MinIO. This wrapper
adds CPU/log work, so it is instrumented app-only evidence, not an unmodified
production benchmark. Health polling is a separate test observer every second;
its own response bytes are excluded from client/object payload totals. It adds
real synthetic inbound requests and cannot prove idle spin-down behavior.

Object logical calls and each retry/HTTP attempt remain distinct, with consumed
GET bytes and upload body reads/buffers. Declared length/read bytes do not prove
acknowledged wire transfer, particularly for unknown commits. Client consumed
response bytes are a separate direction. DB protocol traffic, failed response
bodies, TLS/HTTP/socket overhead, SDK fingerprint reads, retransmissions and
provider accounting are not captured.

Inventory includes live/uploading/retired/deleted recorded bytes, artifact
classes, unknown-size records and physical MinIO objects at phase boundaries.
No manual retirement is injected. A recorded-byte sum without reclamation is
not an upper bound on missing history/multipart parts. MinIO physical deletion
is not Neon soft retention or released-quota evidence.

100-ms disk/anonymous/file samples are lower bounds on transient peaks;
open-unlinked/outside-cache files are missed. Kernel `memory.peak` is lifetime
per container, not reset per stage. OOM/exit observations are independent of the
client/workflow status. Intentional final SIGKILL/exit 137 is labeled by the
before-stop running state and `OOMKilled` rather than called a workload OOM.
Disk is shared cache/TMPDIR occupancy, not exclusively application writes:
inventory initialization and the optional separate original verifier can write
to the mounted cache. Separate phase timestamps and the original-read traffic
identify that overhead; only cgroup/CPU figures are app-container-only. An
original verification may reuse an already hydrated exact cache file; observed
admin GET payload, not the verification marker alone, establishes a new read.

Each job/download/upload remains bounded at 1,800 seconds, HTTP at 120 seconds,
startup at 180 seconds, client phase at 5,400 seconds, outer run at 6,600 seconds
and CI at 120 minutes. Timeout prevents later workflow claims. All exact named
containers and the synthetic network are removed in `always()` cleanup; the
runner disk is disposable. A green artifact upload never establishes successful
workload, provider feasibility, uptime, or persistence on Render.

## Residual provider limits (official docs checked 2026-10-31)

[Render Free](https://render.com/docs/free) explicitly warns against production
use and may suspend a Free service for an uncommonly high volume of traffic it
initiates over the public internet, including external databases and object
storage. No numeric threshold is published. Therefore, even measured traffic
below an included monthly allowance cannot prove provider acceptance or
uninterrupted operation.

[Neon pricing](https://neon.com/pricing) lists 5 GB/project included Object
Storage and 5 GB/project/month public egress on Free. The public-egress allowance
is shared by Postgres, Object Storage and Functions, not a separate 5 GB for each.
The runner does not meter PostgreSQL wire traffic or outside project consumers.
Consumed S3 payload and synthetic recorded inventory must not be called complete
provider usage or quota headroom; retention and unknown transfers remain limits.
