# Opt-in exact GiB valid-audio acceptance fixture

Test-only preparation, not an application change or a deployment. Default commands
make no network requests. The full application/provider test requires the final
integrated source SHA, an explicitly approved **fresh measured free-quota budget**,
and private owner authentication. Do not use it against the earlier disposable
upload-discard or spool-only services.

## Exact input and offline verification

The deterministic RIFF/WAVE is exactly **1,073,741,824 bytes**, including its
64-byte header and a legal 12-byte JUNK padding chunk. Audio is non-silent PCM24,
96 kHz, four channels, **89,478,480 frames / 932.0675 seconds**, below 1,200 seconds.
The 100-Hz triangle signal is generated mathematically, not from real audio.
Maximum yielded chunk: 737,280 bytes. No GiB allocation or sparse-file shortcut.

File SHA-256:
`2f16011c15bf4c0ff6313c154d119030741cf9a6efbd88695fa20da1c7044b46`.
Content-Length multipart body is exactly **1,073,742,002 bytes** (178-byte envelope).
The Linux full test independently decodes every frame with libsndfile, validates
FFprobe PCM metadata/frame timestamps, decodes through FFmpeg to null, invokes
the copied production `audio_io.probe`, and verifies full loopback body/file
hashes against the independently read file. It does not call production decode,
analysis, durable storage, authentication or jobs.

From this directory (Python 3.11+):

```powershell
python client.py
python fixture.py --describe
python -m unittest -v test_fixture
python fixture.py --hash --allow-gib-fixture
python fixture.py --write synthetic.wav --allow-gib-fixture
```

Generation uses exclusive `.part` and final paths, cleans only its own partial
file on failure, validates declared byte count, and checks available disk space.
Only explicitly generated `synthetic.wav` needs manual removal after inspection.

Linux offline container, from repository root:

```text
docker build -f tools/valid-audio-preflight/Dockerfile -t valid-audio .
docker run --rm --memory=512m --memory-swap=512m valid-audio python -m unittest -v test_fixture
docker run --name valid-audio-full --memory=512m --memory-swap=512m valid-audio python verify_linux.py --allow-gib-fixture --out /tmp/offline-result.json
docker cp valid-audio-full:/tmp/offline-result.json offline-result.json
docker rm valid-audio-full
```

The container requires at least 3 GiB free initially. Temporary fixture is removed
even on decoder/network failure. CI runs only offline synthetic tests, with no
provider credentials or requests. Its decoder source fingerprint is recorded;
compare to final application code before executing the real test.

## Actual application: blocked until approved

First confirm final source endpoint contracts, authentication, supported decoder,
upload limit 1024 MiB, duration cap at least 932.0675 seconds, disk/cache guard and
bounded decoding/analysis are integrated. Hosted default duration can be lower:
the client rejects incompatible capabilities. This fixture exercises existing
four-channel-to-stereo behavior; it does not change application audio quality.

`budget.json` is operator-prepared evidence, **not a discovered quota or a claim
that free plans suffice**. Use the following schema, replacing placeholders with
measured values. Origin is the final HTTPS origin; SHA is the full deployed
40-character commit, confirmed separately. Measurement age must be <=15 minutes;
expiration must cover the bounded test/polling. Never approve guessed quota.

```json
{
  "approved": true,
  "origin": "https://FINAL-APP-ORIGIN",
  "final_source_sha": "FULL_FINAL_40_HEX_SHA",
  "measured_at": "MEASURED_RFC3339_TIMESTAMP",
  "expires_at": "FUTURE_RFC3339_TIMESTAMP",
  "input_ingress_bytes": 1073742002,
  "available_local_bytes": 5368709120,
  "tmp_available_bytes": 1082130432,
  "data_available_bytes": 3646242752,
  "object_storage_available_bytes": 1431659840,
  "app_egress_available_bytes": 1431659840,
  "max_http_requests": 260,
  "max_upload_mb": 1024,
  "max_audio_seconds": 1200,
  "measurement_evidence": "Exact free provider quota and mount/capacity snapshots; do not include secrets",
  "analysis_extra_budget_acknowledged": true
}
```

Lower bounds, **not sufficient budgets for final processing**:

| Resource | Minimum bytes / qualification |
|---|---:|
| Upload ingress | 1,073,742,002 plus small API requests |
| Original durable object | 1,073,741,824 |
| Internal 48-kHz stereo float32 PCM | 357,913,920 (44,739,240 frames), excluding WAV headers |
| Original + internal durable storage / initial app publication egress | 1,431,655,744 each |
| Concurrent spool + saved input + real 2-GiB reserve | 4,294,967,296 before internal WAV/features/other objects |
| Client policy local availability | >=5 GiB aggregate plus explicit `/tmp` and `/data` mount minima |

Features, peaks, proxies, revisions, outbox, retries, storage rehydration and
analysis costs are additional and unknown here. Independently measure backing
mount identity: aggregate and per-path numbers must not double-count separate
or shared storage. Metadata cleanup does not refund monthly egress.
This format is deliberately larger than the earlier 1,200-second stereo fixture.

Supply password through inherited `CHOPLAB_TEST_PASSWORD`, owner-only private
UTF-8 `--password-file`, or Windows CurrentUser `--password-dpapi-file`. Never
place it in arguments, receipts, Git or logs. Private files are operator-managed;
the client never writes plaintext credentials. Do not run commands below until
approval; uppercase values are placeholders.

```powershell
python client.py --execute --budget budget.json --origin https://FINAL-APP-ORIGIN --final-source-sha FULL_FINAL_40_HEX_SHA --password-dpapi-file PRIVATE_DPAPI_PATH --out new-receipt.json
```

The client creates only a unique `valid-gib-preflight-<random>` project and uploads
to `/api/projects/{id}/sources` with Basic authentication over verified HTTPS.
It generates/hash-checks the file before sending, hashes all streamed file/body
bytes separately, refuses redirects and caps responses at 1 MiB. `202` is only
acceptance, not byte preservation or completion. A verified import requires a
completed job, matching server original SHA-256, and source sample-rate/channel/
duration metadata. Max 120 polls at 15 seconds; socket timeout 180 seconds;
request budget and approval expiration apply to each new request.

## Failure / unknown outcomes and cleanup

Never automatically retry create/upload. Receipt is exclusively created before
requests and identities are journaled before upload. Preserve it on timeout,
disconnect, cancellation, unknown creation or active jobs; inspect the reported
outcome rather than infer success from transmitted bytes. Capped polling may end
before a slow job completes. No audio download is needed to compare the server
original hash.

Automatic cleanup is restricted to exact own project ID **and** unique name, with
all project jobs terminal. Unknown upload outcomes are retained even if no job
exists yet, because an in-flight request may still create one. No cancellations,
existing-project reuse or arbitrary deletion. HTTP 404 after DELETE establishes
metadata absence only: verify durable outbox completion and actual object
inventory separately. Local files outside this fixture/client are never deleted.

To retry **only metadata cleanup**, obtain a new explicit measured approval (same
origin/SHA, fresh timestamps/evidence, >=10 HTTP requests; spent ingress/storage/
egress need not meet upload minima), then:

```powershell
python client.py --execute --cleanup-receipt new-receipt.json --budget cleanup-budget.json --origin https://FINAL-APP-ORIGIN --final-source-sha FULL_FINAL_40_HEX_SHA --password-dpapi-file PRIVATE_DPAPI_PATH
```

For an unknown upload, only after independently confirming no in-flight request
can create a job, add `--confirm-unknown-upload-ended`. The ID/name and terminal
job checks still apply. Unknown creation is never searched/guessed by the client;
an operator must inspect the unique name and receipt. This preparation alone
does **not** establish final app, warm memory, health, storage or provider success.
