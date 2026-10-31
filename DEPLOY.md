# Render hosting readiness and Neon

**No live deployment is configured.** The user selected Render for application compute and Neon for data,
instead of the initially investigated VM host.
The protected container supports Neon-compatible PostgreSQL metadata and optional private S3-compatible
audio with database-fenced runtime ownership. Combined-state acceptance is still pending; Neon does not
host the Python app or frontend. The dedicated Free Neon project and private audio bucket have been provisioned, but their production
credentials are not wired to an app host. Final Render configuration, suitable compute and integrated
durability validation remain unresolved.
A temporary synthetic test database is not a deployed studio or permanent production database.

## What Neon can host

Current official docs checked on 2026-10-31:

| Neon service | Free allowance | Fit for ChopLab |
| --- | --- | --- |
| [Lakebase Postgres](https://neon.com/pricing) | 1 GB/project, 100 CU-hours/project, 5 GB shared egress | Optional metadata backend implemented, including projects and job state |
| [Object Storage](https://neon.com/docs/storage/overview) | 5 GB/project, S3-style private buckets | Could hold audio and exports; not a filesystem mount |
| [Functions](https://neon.com/docs/compute/functions/overview) | 10 active / 400 waiting Capacity-Hours and 1M invocations | **JS/TS on Node.js 24 only, not Python** |

Neon Functions explicitly do not host websites or own durable background job queues. This app uses Python
FastAPI, NumPy, SciPy, librosa, pedalboard and ffmpeg, plus background audio jobs and absolute disk paths.
Moving the metadata to Postgres alone would **not** make audio, exports, kits or jobs durable on an
ephemeral app host. The explicit SQLite migration below copies only metadata; source audio, exports and kits
remain on a durable `/data` volume unless the separate private object-storage configuration is enabled and
its explicit migration/recovery acceptance passes. A metadata migration alone does not publish audio.

The Free plan is permanent and requires no card, but database compute scales to zero after five idle minutes
and quotas can suspend compute/block writes. Free object storage is available in Ohio, N. Virginia, Frankfurt
and Singapore. Storage/egress quotas apply across the project's branches. These are usage limits, not an
unlimited always-on production SLA. Do not upgrade to a paid plan or use a trial as a permanent free solution.

The [Claimable Neon](https://neon.com/docs/reference/claimable-neon) flow creates a temporary Postgres project
without signup; it expires after 72 hours unless claimed, and object storage requires claiming. It is not a
permanent deployment. Optional CI can validate the metadata integration against an explicitly provided
disposable Claimable Neon database using only synthetic records; no production credentials are required.

## Complementary hosting constraints

The unchanged audio engine needs a separate **Python/container runtime** with enough CPU/RAM and either
durable disk or a complete Neon object-storage integration. The React frontend can be served by this same
container, avoiding cross-origin auth, SSE, download and audio-range complications.

* [Render free](https://render.com/docs/free) is the selected application host, but its
  [0.1 CPU / 512 MB](https://render.com/docs/compute-plans) limit, sleep and ephemeral disk remain
  hard blockers: the baseline OOM-killed short generation and five-minute import. Subsequent bounded DSP
  and lifetime changes pass measured five-minute workflows, but their lifetime peaks still reach 512 MiB.
  Repeated workflows must remain at or below 448 MiB, including the configured durable runtime, before
  treating the compute gate as passed. It is not a production-ready deployment for the full supported studio.
* [Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-overview) requires a paid plan to create a
  Docker Space; CPU Basic's zero hourly price does not make a new Docker Space $0. Its default storage is
  ephemeral and unused compute sleeps.
* [Google Cloud Run](https://cloud.google.com/run/pricing) supports Python containers and usage-based free
  allowances, but requires a billing account and charges above those allowances. Its filesystem is ephemeral;
  request-based CPU allocation also does not safely support the existing detached background job model.
  It is **not guaranteed $0**, and no billable resources have been created.
* Static frontend hosts (GitHub Pages, Cloudflare Pages, Vercel/Netlify static hosting) cannot execute this
  Python audio engine. Hosting only the frontend would leave the app dependent on a separate backend.
* [Northflank Sandbox](https://northflank.com/docs/v1/application/billing/pricing-on-northflank) advertises
  always-on free resources, but requires a payment method and explicitly says its free tier should not be
  used for production applications. Its [pricing](https://northflank.com/pricing) lists a $20 resource allowance
  and a 1 GB / 0.5 CPU plan. Full audio-workflow and storage/overage suitability are unverified; no account,
  upgrade or service has been created.

No verified provider currently satisfies **Neon + unchanged full audio functionality + durable data +
guaranteed $0 ongoing compute** with the available authorized accounts. A dedicated permanent Free
Neon project and private bucket are provisioned; the remaining decision is suitable compute that can run
the full audio engine. Do not switch providers, select a paid Render plan or remove features without user
approval. Remote studio validation is still required. No Neon Object Storage bucket is needed for the current volume-backed path.
An ephemeral host would require a separate complete audio-storage integration. Signing up for Neon alone
does not resolve the compute blocker.
Credentials must be configured privately in host/repository secrets, never in frontend variables, git or chat.

## Selected Render Free path and remaining gates

Render can build this repository's Dockerfile and serve the bundled frontend and Python API together at its
managed HTTPS `onrender.com` hostname. It supplies `PORT` (normally `10000`) and forwards to `0.0.0.0:$PORT`;
hosted ChopLab now honors that port unless `CHOPLAB_PORT` explicitly overrides it, and its container
liveness probe uses the same port. Local port/bind defaults are unchanged. Do not deploy the separate
Caddy/Compose stack on Render: Render supplies TLS, and Compose's persistent volume is unavailable there.

This is a **conditional deployment contract, not a deploy-ready Blueprint**. Do not create a full studio on
an ephemeral disk or call it durable because the metadata uses Neon:

* The independent **Render Free feasibility measurements** benchmark applied Render Free's documented
  512 MiB RAM and 0.1 CPU limits. The measured jobs produced these results despite a later report-artifact
  packaging failure:

  | Synthetic input | Import peak | HPSS peak | Eight-bar generation |
  | --- | --- | --- | --- |
  | 4 seconds | Passed, 294.81 MiB | Passed, 344.02 MiB | OOM-killed, 511.99 MiB |
  | 16 seconds | Passed, 381.43 MiB | Passed, 460.40 MiB | OOM-killed, 512 MiB |
  | 300 seconds | OOM-killed, 511.98 MiB | Not tested | Not tested |

  HQ preview and export were not tested at these limits. These are supported studio workflows, not optional
  Demucs tests. Port handling,
  successful startup or a healthy endpoint does not establish Free-tier suitability. **Do not deploy the
  unchanged full studio to Render Free.** Neon object storage cannot fix an audio computation's memory
  requirement; an unvalidated storage rewrite is not a solution to these measured failures.
* Behavior-preserving allocation reductions were subsequently measured at code `f3dc8be` in
  [the capped memory workflow](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41227718272).
  All tested 4s and 16s import, HPSS, eight-bar generation, HQ two-bar preview, WAV24 and MP3 export/download
  stages passed. Short generation peaks fell to 450.01/469.84 MiB, respectively. However, 16s MP3 reached
  512 MiB, and 300s import was OOM-killed (exit 137) at 511.98 MiB after 209.129 seconds; its downstream
  stages were skipped. There is no reliable headroom or five-minute support established at the cap.
* A narrower follow-up retained sample-identical default harmonic separation using overlapping median
  workspaces, reused the centered mel batching for MFCC, reused channelwise loudness, and streamed exact
  clipped float32 PCM into the MP3 encoder. At `c9148a2`,
  [300s import still OOM-killed in the key-analysis stage](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41229576106)
  after 236.607 seconds at 512 MiB (exit 137). At final code `9290f7b`,
  [the affected 16s workflow passed](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41230824337),
  but WAV reached 507.07 MiB and MP3 still reached 512 MiB; this does not establish reliable headroom.
  The original 4s case was not repeated, and the unchanged 300s import was not repeated after the
  encoder-only correction. 141 backend tests and
  [native x64/ARM container regressions](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41230824317)
  passed. Benchmark CI success means evidence collection succeeded, not that all measured operations did.
  Hosted defaults remain 300 seconds / 100 MiB, versus local 1,200 seconds / 1,024 MiB; 1,200-second
  inputs were not tested. No full input parity, live deployment, or provider persistence is established.
  Default longer/multiple-variation generation, alternate effects and concurrent requests remain untested
  at these limits. A green measurement workflow records failures as evidence, not production readiness.
* Expanded bounded DSP work subsequently passed two complete 300s-source workflows at `1725fa0`, but
  HPSS/export reached 512 MiB and final Docker health was unhealthy with repeated five-second subprocess
  timeouts. Those results did not establish either headroom or satisfactory container liveness.
  At `b758ed2`, [repeated capped workflows](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41239040577)
  passed import, HPSS, eight-bar generation, HQ two-bar preview, WAV24 and MP3, including downloads.
  Two 16s workflows had lifetime peaks of 360.83/354.73 MiB; two 300s imports reached 457.46/457.48 MiB,
  and their later workflows still reached 512 MiB. The 300s headroom gate therefore remained failed.
  Startup was about 21-23 seconds, 16s import about 21 seconds instead of the earlier 287 seconds,
  300s import about 109 seconds, and 300s HPSS about 131-136 seconds at 0.1 CPU.
  Native curl health checks remained healthy, and all direct external HTTP probes completed within five
  seconds (maximum observed 300s import response 1.20164 seconds). These probes reached an exposed local
  Docker endpoint, **not Render's edge**; replacing a heavy Python health subprocess does not alone prove
  HTTP responsiveness. [Render HTTP health checks](https://render.com/docs/health-checks) also require
  a 2xx/3xx response within five seconds.
  Later code releases unused allocator arenas, bounds CQT projection, aligns the original native FFT batch
  boundaries/layout, and flushes/releases audio page cache during streaming HPSS rather than only at its end.
  At `df589bd`, [native x64/ARM compatibility](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41240688462)
  passed unchanged exact-array assertions; earlier native one-ULP discrepancies were not hidden by looser
  tolerances. [Repeated conservative cold-code-cache measurements](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41240688346)
  completed all four workflows, but lifetime peaks remained 511.97-512 MiB and all cases recorded memory
  pressure events, without an OOM. Cold mode releases clean scientific code cache before each measured
  container because build/input-maker activity can otherwise charge shared mapped pages outside its cgroup.
  Charged file cache was substantial: about 185-191 MiB during short exports; their sampled anonymous
  maxima were about 311-317 MiB. The 300s imports' sampled anonymous maxima were 394.5/441.6 MiB;
  total-memory peak samples included 155.0/134.9 MiB file cache. File charge can be reclaimable and is not
  the same as anonymous memory or unique RSS, but reaching 512 MiB still fails the 448 MiB headroom gate.
  A lifetime peak carries earlier operation peaks forward; a later row's high-water is not necessarily
  that operation's own peak. All sampled direct HTTP checks passed, with maximum 1.295 seconds; Docker
  health was healthy during audio work. The 300s imports took 102.6/104.8 seconds and HPSS 138.7/139.6 seconds.
  Observed import/HPSS CPU work was approximately 10.2-10.4/13.8-13.9 CPU seconds respectively, explaining
  much of the remaining wall time at 0.1 CPU rather than cold compilation alone.
  At `0541653`, further changes remove redundant float32 resampling copies, calculate exact peaks without
  full absolute-value buffers, retain owned native output views, and release unused per-track arenas.
  The local suite passed 185 tests with one platform skip, and
  [native x64/ARM parity and container checks passed](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41242404181).
  [Repeated cold capped measurements](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41242403566)
  completed all four full workflows, but lifetime peaks still reached 512 MiB in the first 16s case and
  both 300s cases; the second 16s case peaked at 503.13 MiB. The 448 MiB headroom target remains failed.
  All sampled HTTP probes passed within five seconds (maximum 1.201 seconds) and audio-stage Docker
  health remained healthy. The 300s imports took 128.2/129.0 seconds and HPSS 177.4/177.8 seconds on
  that runner; anonymous maxima during import were 358.0/363.5 MiB, while file-cache maxima reached
  approximately 244.9 MiB. Reduced anonymous allocations did not establish safe total-memory headroom.
  All these measurements use local metadata/audio, **not configured PostgreSQL/S3**, and exclude the
  durable SDK, lease activity and storage transfers. They cannot establish combined-runtime readiness.
* Original input compatibility remains unverified. Hosted defaults are still 300 seconds / 100 MiB;
  local defaults remain 1,200 seconds / 1,024 MiB. Independent test-only overrides accepted a valid
  1,200s stereo 48 kHz PCM16 input at `05eda9d`, but
  [import OOM-killed after 14.217 seconds](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41240082067/artifacts/13056863220)
  at 512 MiB (exit 137). A [newer diagnostic at `ece9072`](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41240643182/artifacts/13057531302)
  OOM-killed after 8.215 seconds in the decoding label, which includes probe/decode/preprocessing.
  Upload file cache peaked at 378 MiB but fell to 0.05 MiB while anonymous memory climbed to 508.90 MiB:
  unreclaimed upload cache alone was not the terminal pressure. This does not pinpoint a particular
  allocation or measure newer code. Downstream stages did not run in either diagnostic, and neither
  establishes 1,024 MiB upload coverage. Do not raise hosted limits, weaken exact-parity assertions or
  claim full local input parity; the complete original-limit durable workflow is still a separate gate.
  A [pinned `0541653` retest](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41242656402/artifacts/13057618500)
  likewise OOM-killed after 8.514 seconds, with terminal sampled anonymous memory 509.19 MiB and file cache
  0.04 MiB; later stages were skipped. Static sizing identifies a representation blocker independent of
  stack attribution: the whole stereo float32 decode alone occupies 439.45 MiB, and the subsequent
  full 48 kHz mono mean adds 219.73 MiB before resampling/runtime overhead. Compressed decode also retains
  full captured PCM plus a copy. Original-limit support requires a safely bounded representation through
  decode, analysis and other decode/reanalysis callers, not only chunked peak calculation.
* Free web services lose uploaded/generated files on every restart, redeploy and 15-minute idle spin-down.
  They cannot attach persistent disks, run one-off jobs or use dashboard/SSH shells. The full app currently
  needs `/data` working/cache files. Optional object-storage integration must be explicitly configured and
  verified together with PostgreSQL fencing; an empty bucket is not automatic persistence.
* Render explicitly says not to use Free instances for production and can restart or suspend them.
  A sleeping service takes about a minute to resume. Background audio jobs cannot be guaranteed to finish
  during spin-down/restarts; preserve explicit interrupted-job reporting rather than claiming durable compute.
* Hobby includes [5 GB/month outbound bandwidth](https://render.com/pricing), then $0.15/GB.
  With a linked payment method, bandwidth overages can be billed; without one, services suspend until the
  next month. Public-internet transfers to Neon, including audio uploads, count. Render can also suspend
  unusually high external database/storage traffic. Keep payment methods/paid upgrades disabled for this
  $0 request and monitor build/bandwidth allowances; do not promise unlimited availability.
* Neon has a separate shared 5 GB/project/month egress allowance and 5 GB object-storage allowance.
  Signed private URLs can reduce Render traffic, but cannot bypass Neon quotas. Bucket versioning/lifecycle
  settings are not enforced; they are not backups or retention guarantees.

Only after the user approves a viable compute approach and the compute/durable-audio requirements have
actually been validated, sign in to Render and connect
the public repository with the reviewed hosting branch (not older `main`), select Docker and **Free**, and
configure `CHOPLAB_MODE=hosted`, the real `CHOPLAB_PUBLIC_URL=https://...onrender.com`, owner-hash secret file,
private Neon connection settings and `/api/health` liveness. The password hash file path must match the
Render secret file, for example `/etc/secrets/owner_password_hash`; never put credentials in build arguments
or frontend variables. Do not use Render's expiring Free database instead of Neon.

The managed edge's forwarded-header sanitization/trust must be checked against the **real deployed edge**
before owner login. Do not blindly set `CHOPLAB_PROXY_IPS=*` or trust client-supplied scheme/host headers.
Without Render account access, that boundary and a live studio cannot be verified. Object-storage writes,
authorized/ranged downloads, cache loss, job recovery and realistic audio within 512 MB must also pass
before claiming end-to-end Render Free support. Paid resources or silent feature reductions are not a
solution to these gates.

### Independent platform diagnostics, not final studio validation

Disposable, data-free reference probes confirmed that Render redirected HTTP to HTTPS, presented peer
`127.0.0.1`, and overwrote `X-Forwarded-Proto` to `https` even for spoofed external values. It preserved
client-supplied `X-Forwarded-Host` and `Forwarded`, and spoofed `X-Forwarded-For` entries remained on the
left of a platform-extended chain. Do not trust forwarded host or the leftmost forwarded IP. This was not
ChopLab/Uvicorn or its owner-auth flow; the final edge-to-application trust boundary still needs verification.

A real Render reference secret mount had owner/group `root:1000` and mode `0640`. UID/GID 10001 was denied
without supplementary groups and read a synthetic secret with supplementary GID 1000. The independently
integrated secret-access fix `308a0dc` keeps UID/GID 10001 and adds that group; native image identity and
permission checks passed. This does not replace final ChopLab startup validation against real mounted files.
Render [stages secret files in the Docker build-context root](https://render.com/docs/docker-secrets);
allowlisted `COPY` paths must remain, and final integration must exclude `owner_password_hash`,
`database_url` and `storage_config.json` from the context.

A disposable Render-to-Neon metadata-only test at `0ca3079` initially failed with `InsufficientPrivilege`:
the non-owner role had `USAGE`, but needed `CREATE` on the app's `public` schema. Granting it only on the
disposable branch allowed startup and synthetic writes/reads. Same-commit redeployment to a different
Render instance preserved those metadata records, verified independently in Neon and then deleted.
No audio assets, integrated fencing or object-storage recovery were tested; production grants were unchanged.
The disposable services, database branch/roles and probe files were deleted.

A separate synthetic Render HTTPS upload probe at `f55b963` received byte-exact 230,400,044-byte raw and
multipart bodies and 1,073,742,002-byte multipart bodies using both chunked and explicit Content-Length
requests. On this one Cloudflare IAD client path, uploads appeared buffered until the body finished;
5-10 second pauses resumed, but a 20-second pause disconnected before the app observed the request.
An app-level 413 arrived only after the sender transmitted a declared 1,258,291,201-byte over-cap body.
This is provider-path evidence, not ChopLab upload, decode, quality or persistence validation, nor a
guarantee for every client/edge. Preserve the original cap and account for buffering and upload timeouts.
The probe hashed/discarded bodies; it did not exercise FastAPI multipart spooling, ephemeral-disk reserves
or Neon quotas. The real app parses `UploadFile` before copying it to a `.part` file and checking the
hosted 2,048 MiB free-space reserve, so acceptance must account for briefly overlapping local copies.

Configure appropriate schema/object privileges for the actual database role rather than assuming every
Neon role owns `public`. Console password reset also does not create missing application privileges or clear
`NOLOGIN`: a passwordless SQL-created probe role returned HTTP 422 on reset, while a privately initialized
password allowed reset and retained `NOLOGIN` and its least-privilege flags. Do not document reset of a
passwordless SQL role as a sufficient provisioning step.

## Portable container preparation

### Integrated durable-runtime gates

An independent Render parser/`_save_upload` preflight compared against `d5f4115` saved
1,073,741,824 synthetic bytes in 93 seconds with matching digests, 129 cache releases and a maintained
2 GiB reserve. It nevertheless peaked at **537,137,152 bytes against a 536,870,912-byte limit**,
with 6,806 `memory.events:max` events and no OOM/kill: heavy reclaim, not safe headroom.
It used a disposable probe with a test-only storage predicate and no database, S3, app lifespan or jobs;
function/parser equality is not production image/runtime equivalence. A bounded multipart follow-up is
being investigated separately. This does not establish full-app 1 GiB upload readiness.

The next audio pipeline uses the independent `e42630a` disk decoder for import and bounded reads for
analysis/reanalysis and other decode/load callers. Large mono, harmonic, mel and CQT buffers use private
disk-backed arrays with last-view ownership, cache release and the existing hosted free-space reserve.
Global resampling, dB floors, centered frames and reductions retain independent numerical references.
Local backend regressions passed (379 tests / nine platform/network skips), but final native and capped
runtime checks are pending. The prerequisite's 1,200s **48 kHz stereo PCM16 decode-only** test took
11.004 seconds and peaked at 70.92 MiB under 512 MiB/no swap/0.1 CPU; it is not full app/DSP evidence.
Its expansion/channel/coprime-rate edge correction is still pending; no all-rate readiness or limit
increase is claimed. Temporary arrays add working disk and I/O; after a forced process kill, stop all
workers before inspecting/removing orphaned private `.scratch` files. They are not durable assets.

The complete recovery/storage final tree `240f23f` was integrated atomically at `fd53e2a`, not deployed
as a recovery-only intermediate. Its combined hosted x64/ARM, secret permissions, isolated recovery,
full PostgreSQL/S3 integration and live TLS object-storage boundary checks passed. Durable mode requires PostgreSQL and a private
`CHOPLAB_STORAGE_CONFIG_FILE`; it bypasses permanent volume binding, fences metadata/asset publication,
and reports interrupted jobs or lost ownership explicitly. Do not overlap legacy unfenced workers with
durable production. The final image includes the PostgreSQL/storage extras and excludes all three Render
secret filenames from its build context. Local volume-backed mode remains supported.

The entire selective-hydration follow-up (`c140252`, `5b088367`, `100cbc4`) is integrated together,
retaining the hosting audio response's range support and cache release. Metadata/API/snapshot/startup
reads no longer eagerly download audio; processing and playback materialize only the required,
authoritatively referenced asset with the existing checksum and fencing guards. The follow-up's
standalone five-asset instrumentation recorded zero GETs for cold metadata, then one whole 3 MiB
source download for a selected 64-byte HTTP range; repeat playback used the verified local cache.
A selected harmonic required its own whole 4 MiB download. This is not range-only object transfer.
The final combined hydration runtime `345b442` passed hosted x64/ARM, secret permissions, recovery/SSE,
[full durable integration and protected HTTP](https://github.com/MichaelAbateTSU/SampleToBeat/actions/runs/41245969662),
and the TLS S3 boundary checks. Its small-fixture durable acceptance recorded 23 passes and one
intentional full-DSP skip at 512 MiB/no swap/**1 CPU**, with a 309,714,944-byte lifetime peak and zero
max/OOM events; separate uncapped full studio/cache destruction and 38 HTTP harness tests passed.
This is combined-state functional proof, not Render's 0.1 CPU full-audio acceptance or a long-library
quota test. Exact capped full-runtime acceptance remains blocked.
A 1,200s stereo 48 kHz PCM16 original/internal/two-stem set still occupies roughly 1.6 GB, even though
opening its metadata no longer transfers that set. Repeated small-fixture
replacement success is not evidence of long-library cold-start, disk or 5 GB egress headroom.

The fenced runtime renews its lease through PostgreSQL every ten seconds while active. This prevents
Neon's five-minute database scale-to-zero. The dedicated branch's observed compute range is 0.25-2 CU;
at a constant 0.25 CU, the Free project's 100 CU-hour allowance permits at most 400 active hours/month,
less with autoscaling or other usage. Exhaustion suspends compute. Foreground browser health polling every
ten seconds can also keep Render active while the page is open. Do not weaken fencing or add unattended
keepalive traffic to work around limits. No idle-ownership strategy has been validated.

The portable volume backup scripts below are not by themselves a complete object-storage backup.
A durable metadata snapshot carries asset references/catalogs, not independent copies of every immutable
object. Off-provider object copies and coordinated snapshot/restore acceptance remain required before
claiming recovery after bucket deletion.

`Dockerfile` builds Vite assets and installs the Python backend with the PostgreSQL client, without optional
Demucs/model dependencies. Local installs still use SQLite unless explicitly configured.
The final container runs as UID/GID 10001, serves the UI and API together, includes native ffmpeg for both
x86-64 and ARM64, and exposes data-free **process liveness** at `/api/health`.

`deploy/compose.yaml` is for an **already authorized Linux Docker host with persistent disk**, not Neon
Functions or an ephemeral serverless host. It includes a TLS Caddy proxy and a durable named audio volume.
It does not provision a provider or configure billing. Its defaults need roughly 12 GB host memory and two CPUs;
do not deploy unchanged onto a 512 MB free service.

Publish the complete build and hosting commits to the existing GitHub repository before attempting a remote
checkout: the transferred studio build initially existed only locally. The CI workflow runs on reviewed
source in GitHub, not on an older remote `main`.

On a chosen authorized Linux host with Docker Engine and Compose v2 installed:

```bash
# From the reviewed repository checkout containing the full studio and these changes:
sudo install -d -o "$USER" -g "$(id -gn)" -m 700 /opt/choplab /opt/choplab/secrets \
  /opt/choplab/releases /opt/choplab/backups
cp deploy/.env.example /opt/choplab/.env
chmod 600 /opt/choplab/.env
nano /opt/choplab/.env
python3 scripts/hosting_password.py /opt/choplab/secrets/owner_password_hash
sudo chown 10001:10001 /opt/choplab/secrets/owner_password_hash
sudo chmod 400 /opt/choplab/secrets/owner_password_hash
release=$(git rev-parse HEAD)
docker build -t "choplab:$release" .
mkdir -p "/opt/choplab/releases/$release"
cp deploy/{compose.yaml,compose.neon.yaml,Caddyfile,release.sh,backup.sh} "/opt/choplab/releases/$release/"
bash "/opt/choplab/releases/$release/release.sh" "choplab:$release"
```

Set `CHOPLAB_DOMAIN`, `ACME_EMAIL` and optionally `CHOPLAB_USERNAME` in `.env`. Use a DNS hostname pointing
at this host, not a bare IP. Free DNS can avoid a domain purchase, but its eligibility/availability must be
checked with the chosen provider. Open TCP 80/443 in provider and host firewalls, restrict administrator SSH,
and never expose backend port 8765. Do not flush existing firewall rules.

The password tool requests a unique 16+ character password and writes only a salted PBKDF2 hash. The browser's
native Basic authentication prompt protects all studio pages, APIs, source audio, downloads and API docs.
Only `/api/health` is anonymous. Cross-site access is rejected. Caddy provisions and renews TLS and redirects
HTTP to HTTPS. **Never enter the owner password at an HTTP URL**. Caddy is the only published service;
the backend has no published port and trusts forwarded headers only within its dedicated Docker network.
That network permits outbound NAT so the container can reach Neon over encrypted PostgreSQL; it does not
make the backend an internet-facing port.

## Configure Neon metadata

1. Sign in at [Neon](https://console.neon.tech), select the permanent **Free** plan, and create a dedicated
   project/database. Do not reuse another app's database or upgrade to paid. Copy the pooled PostgreSQL
   connection URL privately from the console. Standard Neon `sslmode=require` URLs are upgraded by the
   backend to `verify-full` with system CA trust, authenticating both certificate chain and hostname.
2. On the chosen host, put that URL on one line in `/opt/choplab/secrets/database_url` with no quotes.
   Do not paste it into chat, git, frontend settings or command-line arguments:

   ```bash
   # The editor input stays private; nothing prints the connection URL.
   nano /opt/choplab/secrets/database_url
   sudo chown 10001:10001 /opt/choplab/secrets/database_url
   sudo chmod 400 /opt/choplab/secrets/database_url
   ```

3. `release.sh` detects this explicit secret file and adds `compose.neon.yaml`. It never silently falls back
   to SQLite on a PostgreSQL error. Remove neither the secret nor the migration marker to "fix" a failed DB:
   once the volume uses Postgres, missing configuration is a startup error instead of an empty/old library.
4. For a **new empty library**, run the release normally. For existing SQLite data, stop the app, make an
   off-host backup of the original audio volume first, and explicitly migrate into an **empty** Neon store:

   ```bash
   export CHOPLAB_IMAGE="choplab:REVIEWED_RELEASE"
   export CADDY_CONFIG="/opt/choplab/releases/REVIEWED_RELEASE/Caddyfile"
   compose=(docker compose --project-directory /opt/choplab --env-file /opt/choplab/.env \
     -f /opt/choplab/releases/REVIEWED_RELEASE/compose.yaml \
     -f /opt/choplab/releases/REVIEWED_RELEASE/compose.neon.yaml)
   "${compose[@]}" stop app
   "${compose[@]}" run -T --rm --no-deps app python -m choplab.metadata migrate-sqlite
   "${compose[@]}" up -d --wait
   ```

   The migration is one transaction and preserves document IDs, parents, timestamps and stored audio paths.
   It keeps the original SQLite file and writes a database-identity/baseline marker on the audio volume.
   A nonempty conflicting destination is rejected without overwriting it; rerunning an already completed
   identical import is safe. Do not run a local SQLite process against the same volume after migration.
   Startup rejects a changed SQLite baseline or a different configured database rather than hiding data.

   This is a store migration, not a Windows-to-Linux path relocation. Migrate a library whose existing
   audio paths still match its persistent volume (normally `/data` in Docker). Copying an old Windows
   `data` directory into Linux does not rewrite `C:\...` paths; preserve its original backup and reimport
   sources or perform a separately reviewed path-relocation migration before treating it as cloud-ready.

For non-container local testing, install `cd backend && uv sync --extra postgres` and configure a private
`CHOPLAB_DATABASE_URL_FILE`, `CHOPLAB_DATABASE_URL`, or `DATABASE_URL` (file and inline configuration are
mutually exclusive). Without any database configuration, existing SQLite behavior is unchanged. Connections
always enforce `sslmode=verify-full` and disable automatic prepared statements for Neon PgBouncer transaction
pooling. A URL using `require` or `verify-ca` is upgraded, never used as an insecure fallback; `disable`,
`allow` and `prefer` are rejected. The container includes system CA certificates and passes Python's actual
system trust-bundle path to libpq, whose bundled OpenSSL may otherwise look in a different directory.
If no system bundle is available (for example in some local Windows installs), configure an explicit trusted
CA file; the backend refuses to connect without one. For an isolated/private PostgreSQL server, configure a trusted certificate
file with URL `sslrootcert` or `PGSSLROOTCERT`, readable inside the container. Certificate-chain or hostname
mismatches fail the connection; do not bypass these checks or use an untrusted CA to fix connectivity.
Only one
ChopLab backend process is supported; this is not a multi-user/multi-writer SaaS migration.

The authenticated `/api/system/database` endpoint performs an **on-demand** connection check. Do not poll it
as a keepalive. `/api/health` and Docker health probes never query Neon, allowing the Free database to scale
to zero. Real owner actions wake it; quotas/outages return an explicit 503, not success or SQLite fallback.
Cold database startup can add latency. Monitor Neon CU-hours, storage and egress in its console.

The Docker build resolves versions within existing dependency constraints and records the installed package
versions in `/opt/requirements-installed.txt`. Retain a built image tag for repeatable release/rollback;
rebuilding later can resolve newer dependencies. The workflow `Hosted container checks` builds and smoke-tests
the container, authentication, SPA serving, native demo/HPSS/generation/HQ/WAV+MP3/range/SSE workflow,
persistence across restart, Caddy/Compose configuration, and real TLS PostgreSQL metadata CRUD/job updates/
migration/restore checks on x86-64 and ARM64, including real trusted/hostname-matched TLS success and untrusted
CA/hostname-mismatch rejection. It executes the real deployment backup script for both SQLite
and PostgreSQL, restores exact metadata and synthetic audio into a fresh volume/database, and exercises
export/invalid-JSON/archive/recovery failures, overlapping backups and intentionally stopped service state.
Ordinary CI uses a disposable local PostgreSQL container.
The optional manual `neon_smoke` input uses a dedicated `NEON_SMOKE_DATABASE_URL` secret supplied for a
synthetic, disposable Neon database; it does not provision or claim a project. Never point that test at
production data. Standard GitHub-hosted CI is free for this public
repository; review billing before changing visibility or runner type.

## Hosted behavior and limits

The app container is limited to 9 GB RAM and 1.75 CPUs; the proxy is limited to 256 MB RAM and 0.25 CPU.
One audio computation runs at a time across background jobs, HPSS and HQ previews. Up to eight queued/running
jobs are accepted, then a retryable 429 is returned. Hosted imports are limited to 100 MiB and five minutes;
Caddy caps request bodies at 101 MiB before multipart parsing, allowing overhead above the 100 MiB file limit.
Local defaults remain 1 GiB, 20 minutes and two workers.
Free space below a 2 GiB reserve rejects uploads with 507. This is not a total storage quota: generated
renders/stems can still fill disk. Monitor `df -h`, clear render cache, and delete old projects.

Memory-limit kills interrupt jobs; reduce recording/beat length before retrying. HPSS is included; Demucs
integration remains optional and unvalidated. Playback, downloads, range requests and job SSE use the same
origin. "Show folder" is hidden in hosted mode because the folder is on the server; use Download instead.

Audio, kits and exports live in `choplab_audio`; metadata is either local SQLite or configured Neon/Postgres.
Certificate state has separate
volumes. Containers restart after reboot; image replacement preserves data volumes.
**Never run `docker compose down -v` or assume data survives host/disk deletion.** Interrupted jobs are marked
interrupted on startup, not silently replayed. Neon metadata survives app-container replacement, but it
cannot recreate deleted source audio. Keep the same `/data` mount path and its matching metadata snapshot.

## Verification, backup and recovery

Remote readiness requires more than a health response:

1. Confirm `https://YOUR_HOST/api/health` succeeds and anonymous studio/audio/API requests return 401.
2. Login, create a project, import the demo or authorized short recording, generate a variation, open the
   studio, play/seek, render HQ, and export/download WAV and MP3.
3. Refresh a nested project URL and verify job progress. Restart the host and confirm the project, audio
   and exports remain. Validate TLS without disabling certificate verification.

```bash
docker ps
df -h
# Briefly stops the app for coordinated metadata + audio snapshots:
bash "$(cat /opt/choplab/current-release)/backup.sh"
```

Copy backups **off the host** to storage you control. A host-local backup does not protect against disk/host
deletion. Retain backups within the chosen free storage allowance; save `.env` and the owner hash privately.
Backups contain source audio and project metadata and must not be published. `backup.sh` creates **both**
`audio.tar.gz` and `metadata.json` in a unique `backup-TIMESTAMP-SUFFIX` directory while the single backend
is stopped. Copy that entire directory off-host. Install `flock` (normally included in Linux `util-linux`);
overlapping backups are rejected before changing the service. Do not run deployments, manual writers or
other maintenance against this volume/database during a backup.
The script preserves an intentionally stopped app. For a running app, it waits for recovery before
publishing the completed pair. Failed export, archive or recovery commands return nonzero and do not publish
a complete-looking backup; temporary files are removed on handled failures. A forcibly killed host/process
can leave `.pending-*` directories; these are **not complete backups**. Inspect the service state and
remove only an identified pending directory's two files before retrying. A recovery failure needs operator
attention and is never reported as backup success.
The JSON is an application-document snapshot, not a backup of PostgreSQL roles or unrelated databases.
If metadata export fails, the backup command fails and does not claim a complete audio-only backup.

For recovery, configure a compatible host, deploy the retained image, stop the app, and restore an off-host
archive into `choplab_audio` while no backend process is running. Keep UID/GID 10001 and the `/data` mount path
unchanged because persisted file paths are absolute. For Neon, configure a **new empty dedicated database**,
restore the paired audio archive first while the app is stopped, then import its metadata snapshot:

```bash
# Use the same compose array as above; the JSON goes over stdin, not command-line arguments.
"${compose[@]}" run -T --rm --no-deps app python -m choplab.metadata restore < backup-TIMESTAMP-SUFFIX/metadata.json
"${compose[@]}" up -d --wait
```

The importer refuses to overwrite conflicting existing records and updates the volume's database identity
for the restored destination. Verify projects, source seeking, job state and exports before deleting old
backups. For SQLite-only recovery the audio archive also contains the matching SQLite database.
To roll back, rerun a retained release's `release.sh` with a PostgreSQL-compatible retained image when Neon
is enabled; rollback to an older SQLite-only release is refused. Automatic container-health
rollback cannot undo incompatible data changes or bad DNS/TLS. Remove only explicitly identified old image
IDs/release directories after confirming a release; never delete volumes. No paid monitoring or backup
service has been enabled.
