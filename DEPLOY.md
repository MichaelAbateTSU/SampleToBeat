# Hosting readiness and Neon

**No live deployment is configured.** The user selected Neon instead of the initially investigated VM host.
The protected container below is provider-neutral preparation, not a claim that Neon hosts this app.
No cloud account, Neon project, durable bucket or deployment URL has been provisioned.

## What Neon can host

Current official docs checked on 2026-10-31:

| Neon service | Free allowance | Fit for ChopLab |
| --- | --- | --- |
| [Lakebase Postgres](https://neon.com/pricing) | 1 GB/project, 100 CU-hours/project, 5 GB shared egress | Could replace SQLite metadata; not yet integrated |
| [Object Storage](https://neon.com/docs/storage/overview) | 5 GB/project, S3-style private buckets | Could hold audio and exports; not a filesystem mount |
| [Functions](https://neon.com/docs/compute/functions/overview) | 10 active / 400 waiting Capacity-Hours and 1M invocations | **JS/TS on Node.js 24 only, not Python** |

Neon Functions explicitly do not host websites or own durable background job queues. This app uses Python
FastAPI, NumPy, SciPy, librosa, pedalboard and ffmpeg, plus background audio jobs and absolute disk paths.
Moving the metadata to Postgres alone would **not** make audio, exports, kits or jobs durable on an
ephemeral app host. No speculative SQLite-to-Postgres or file-to-object-storage migration is included.

The Free plan is permanent and requires no card, but database compute scales to zero after five idle minutes
and quotas can suspend compute/block writes. Free object storage is available in Ohio, N. Virginia, Frankfurt
and Singapore. Storage/egress quotas apply across the project's branches. These are usage limits, not an
unlimited always-on production SLA. Do not upgrade to a paid plan or use a trial as a permanent free solution.

The [Claimable Neon](https://neon.com/docs/reference/claimable-neon) flow creates a temporary Postgres project
without signup; it expires after 72 hours unless claimed, and object storage requires claiming. It is not a
permanent deployment and is unnecessary until an integration can actually be tested.

## Complementary hosting constraints

The unchanged audio engine needs a separate **Python/container runtime** with enough CPU/RAM and either
durable disk or a complete Neon object-storage integration. The React frontend can be served by this same
container, avoiding cross-origin auth, SSE, download and audio-range complications.

* [Render free](https://render.com/docs/free) sleeps, provides only 512 MB RAM, and has ephemeral disk.
  It is not validated for this app's representative audio workflow and is not a safe production recommendation.
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
guaranteed $0 ongoing compute** with the available authorized accounts. Required next steps are a claimed
Neon Free project/bucket and an authorized adequate free Python compute host, followed by an end-to-end
storage integration and remote validation. Signing up for Neon alone does not resolve the compute blocker.
Credentials must be configured privately in host/repository secrets, never in frontend variables, git or chat.

## Portable container preparation

`Dockerfile` builds Vite assets and installs the Python backend without optional Demucs/model dependencies.
The final container runs as UID/GID 10001, serves the UI and API together, includes native ffmpeg for both
x86-64 and ARM64, and exposes a data-free readiness check at `/api/health`.

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
cp deploy/{compose.yaml,Caddyfile,release.sh,backup.sh} "/opt/choplab/releases/$release/"
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
the backend's private network trusts only this proxy for forwarded headers.

The Docker build resolves versions within existing dependency constraints and records the installed package
versions in `/opt/requirements-installed.txt`. Retain a built image tag for repeatable release/rollback;
rebuilding later can resolve newer dependencies. The workflow `Hosted container checks` builds and smoke-tests
the container, authentication, SPA serving, persistence across restart, and Caddy/Compose configuration.
It does **not** deploy to Neon or create cloud resources. Standard GitHub-hosted CI is free for this public
repository; review billing before changing visibility or runner type.

## Hosted behavior and limits

The app container is limited to 9 GB RAM and 1.75 CPUs; the proxy is limited to 256 MB RAM and 0.25 CPU.
One audio computation runs at a time across background jobs, HPSS and HQ previews. Up to eight queued/running
jobs are accepted, then a retryable 429 is returned. Hosted imports are limited to 100 MiB and five minutes;
Caddy caps request bodies before multipart parsing. Local defaults remain 1 GiB, 20 minutes and two workers.
Free space below a 2 GiB reserve rejects uploads with 507. This is not a total storage quota: generated
renders/stems can still fill disk. Monitor `df -h`, clear render cache, and delete old projects.

Memory-limit kills interrupt jobs; reduce recording/beat length before retrying. HPSS is included; Demucs
integration remains optional and unvalidated. Playback, downloads, range requests and job SSE use the same
origin. "Show folder" is hidden in hosted mode because the folder is on the server; use Download instead.

Data lives in `choplab_audio`, including SQLite, audio, kits and exports. Certificate state has separate
volumes. Containers restart after reboot; image replacement preserves data volumes.
**Never run `docker compose down -v` or assume data survives host/disk deletion.** Interrupted jobs are marked
interrupted on startup, not silently replayed. Only one backend process is supported. Neon is not involved
in this existing filesystem path until a separately validated integration exists.

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
# Briefly stops the app for a consistent SQLite WAL + audio snapshot:
bash "$(cat /opt/choplab/current-release)/backup.sh"
```

Copy backups **off the host** to storage you control. A host-local backup does not protect against disk/host
deletion. Retain backups within the chosen free storage allowance; save `.env` and the owner hash privately.
Backups contain source audio and project metadata and must not be published.

For recovery, configure a compatible host, deploy the retained image, stop the app, and restore an off-host
archive into `choplab_audio` while no backend process is running. Keep UID/GID 10001 and the `/data` mount path
unchanged because persisted file paths are absolute. Then start the app and verify projects/audio/exports.
To roll back, rerun a retained release's `release.sh` with its retained image tag. Automatic container-health
rollback cannot undo incompatible data changes or bad DNS/TLS. Remove only explicitly identified old image
IDs/release directories after confirming a release; never delete volumes. No paid monitoring or backup
service has been enabled.
