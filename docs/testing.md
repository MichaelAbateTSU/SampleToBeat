# Testing

Hosted access regression tests cover owner authentication across API/docs/audio/download/SSE surfaces,
HTTPS enforcement, cross-site and Host validation, failed-login throttling, upload/duration limits,
partial-upload cleanup, shared compute slots and bounded background-job admission. The HTTP workflow also
checks audio/download byte ranges and completed-job SSE. `.github/workflows/container.yml` adds Docker,
SPA, restart-persistence and TLS/Compose configuration checks. These checks do not provision a live Neon
deployment; see [DEPLOY.md](../DEPLOY.md) for the remaining provider/account constraints.

PostgreSQL container CI uses a disposable TLS database to verify metadata CRUD, timestamp precision,
parameterized values, threaded job updates, restart recovery, explicit SQLite migration, safe store switching,
snapshot restore, and the full studio audio workflow with PostgreSQL metadata. The same job runs natively
on x86-64 and ARM64. Optional manual Neon checks require a dedicated disposable secret and synthetic data;
they must never target an existing production database. Liveness probes do not query the database.

All automated tests use synthetic audio (the generated demo song and in-test signals). No copyrighted audio
is needed.

## Backend (pytest)

```bash
cd backend
uv run pytest -q
```

| File | Covers |
| --- | --- |
| `test_units.py` | Tick math, swing, cache keys, key parsing, safe filenames, limiter ceiling, loudness and true-peak mastering |
| `test_dsp.py` | Pitch shift, time-stretch, tape mode, reverse, fades, effects, EQ, 808 tuning and slides, drum patterns, kit sounds |
| `test_analysis.py` | Tempo, key and downbeat on the demo song; fast tempo not halved; user overrides; silence rejected |
| `test_schemas.py` | Arrangement round trip, invalid documents rejected, schema migration, bounds on request input |
| `test_pipeline.py` | Sample discovery, manual samples, every generation strategy producing a valid playable arrangement, variation diversity, seed reproducibility, seamless loop render, mute/solo/missing samples, verified export files |
| `test_api.py` | The full REST workflow (demo → analysis → samples → generation → studio save → export → download), the studio helper endpoints, and a real HPSS separation run (stems exist, match the source length and are actually split) |

## Frontend unit tests (Vitest + Testing Library)

```bash
cd frontend
npm test            # vitest run
npm run typecheck
npm run lint
npm run build
```

Covers music math, arrangement operations (moving/splitting/duplicating clips, making patterns unique, bars and
sections), offline scheduling, the studio store (undo/redo, baseline) and the pattern editor component.

## End-to-end (Playwright)

```bash
cd frontend
npm run build
npx playwright install chromium     # or set PW_CHANNEL=msedge / chrome to use an installed browser
npx playwright test
```

The config starts the real backend (`uv run choplab`) on port 8799 (`E2E_PORT`) with a temporary `CHOPLAB_DATA`
and serves the built frontend. The journey test does the following:

1. Create a project and load the demo song. Analysis runs and samples are discovered automatically.
2. Generate two 8-bar boom-bap variations, preview one and open it in the studio.
3. Mute a track, change the BPM, toggle a drum step, play (the playhead must move), then undo/redo.
4. Wait for "saved", reload, and confirm the mute and BPM persisted.
5. Export a 24-bit WAV, download it and check its size. The test also fails on any uncaught page error.

A second test checks that settings persist. A full run takes several minutes on CPU, mostly analysis and
generation.

## What is and isn't verified

* **Verified automatically:** import, analysis, HPSS separation, sample discovery, generation of complete beats
  with real drums and 808, rendering, mastering and export (files are read back: duration, peak, not silent),
  project save and reopen, and the studio editing journey in a real browser.
* **Not verified automatically:**
  * Demucs separation. The adapter is implemented but needs the optional extra and model downloads, and the
    tests don't run it.
  * Musical quality. Producing a valid audio file is not proof that it sounds good, so judge results by ear.
