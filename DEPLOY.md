# Render hosting readiness and Neon

**No live deployment is configured.** The user selected Render for application compute and Neon for data,
instead of the initially investigated VM host.
The protected container now supports Neon-compatible PostgreSQL metadata, but Neon does not host the Python
app or frontend. Private production Neon configuration and adequate authorized Python/container compute
are still required; a temporary synthetic test database is not a deployed studio or permanent production database.

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
remain on a durable `/data` volume. Object-storage migration is not included.

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
  validation blockers, not a production-ready deployment.
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
Neon Free PostgreSQL project and an authorized adequate free Python compute host with durable disk, followed
by remote studio validation. No Neon Object Storage bucket is needed for the current volume-backed path.
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

* Free web services lose uploaded/generated files on every restart, redeploy and 15-minute idle spin-down.
  They cannot attach persistent disks, run one-off jobs or use dashboard/SSH shells. The full app currently
  needs `/data` files and has no Neon Object Storage integration; an empty bucket is not automatic persistence.
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

When the compute and durable-audio requirements have actually been validated, sign in to Render and connect
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

## Portable container preparation

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
