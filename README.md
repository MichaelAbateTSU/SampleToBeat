# ChopLab AI (SampleToBeat)

ChopLab AI turns a piece of music into sample-based hip-hop beats, entirely on your machine. Import a song,
let ChopLab analyze it and find loopable phrases and chops, generate several complete beats (chopped samples,
drums and 808s), then edit one in the browser studio and export a mastered WAV or MP3.

* **Local and private.** A Python audio engine and a web studio on `127.0.0.1`. No accounts, no uploads.
* **Explainable.** Tempo, key and sample choices come with confidences and reasons, and you can correct them.
* **Editable.** Arrangement timeline, drum step sequencer, 808 piano roll, chop editor, mixer and FX.
* **One render path.** Previews and exports use the same offline renderer (-14 LUFS, -1 dBTP).

## 1. Prerequisites

* Windows 10/11 or Linux (x86-64)
* [Node.js 20+](https://nodejs.org)
* [uv](https://docs.astral.sh/uv/). The setup script installs it if missing, and uv installs Python 3.12 for you.
* About 2 GB of free disk space

ffmpeg comes bundled (via `imageio-ffmpeg`), so you don't need to install it. See
[docs/installation.md](docs/installation.md) for details.

## 2. Installation

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

```bash
# Linux
chmod +x scripts/*.sh && scripts/setup_unix.sh
```

This runs `uv sync` for the backend and `npm ci` + `npm run build` for the frontend, then checks the
environment (`scripts/check_environment.py`).

## 3. Model downloads (optional)

No model download is needed. Stem separation works out of the box with the built-in **HPSS**
harmonic/percussive splitter.

For 4-stem **Demucs v4** separation (vocals / drums / bass / other), install the optional extra:

```bash
cd backend && uv sync --extra separation      # or run the setup script with -Separation / --separation
```

The weights (`htdemucs`, `htdemucs_ft`) download to `data/models/` on first use. CPU inference takes minutes per
song.

## 4. Startup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1     # Windows
```

```bash
scripts/start_unix.sh                                                  # Linux
```

The app opens at **<http://127.0.0.1:8765>**. The single underlying command is `cd backend && uv run choplab`.
Add `-Dev` / `--dev` for the hot-reloading Vite dev server on port 5173.

## 5. First audio import

1. Click **New project** and name it.
2. In **Source**, drop an audio file (WAV, FLAC, MP3, M4A, …) or click **Use demo sample** to generate a
   copyright-free synthetic song.
3. Analysis runs automatically: tempo, key, beat grid, sections. Correct anything that's off under
   **Correct the analysis**.
4. Optionally choose a separation model and click **Separate**.

Only use audio you have the right to sample.

## 6. Generating a beat

1. Open **Samples** to review the discovered loops and chops. Each has a score with reasons. ★ the ones you
   like, or select a region yourself.
2. In **Generate**, pick a genre (Boom Bap, Trap, Drill, Lo-fi) and set length, energy, chop intensity, 808
   intensity, drum complexity and the number of variations, then click **Generate**.
3. In **Variations**, play each result and check its automatic checks. Regenerate any you don't like.

## 7. Editing a beat

Click **Open in studio** on a variation. In the studio:

* **Timeline**: drag, resize, split (S), duplicate (Ctrl+D) and delete clips. Mute/solo tracks, set the loop
  (shift-click the ruler), edit sections.
* **Pattern editor**: drum step grid with velocities, 808 piano roll with slides and sound settings, sample chop
  pads and sequencer.
* **Mixer**: volume, pan, meters, EQ/filters/compression/saturation/reverb/delay per track.
* **Transport**: play (Space), BPM, swing, key, kit, undo/redo (Ctrl+Z / Ctrl+Y), and **Render HQ** to hear the
  exact export.

Changes autosave.

## 8. Exporting a beat

In **Export**, choose WAV 24/16-bit, WAV 32-bit float or MP3 320 kbps, either the full beat or a loop region,
and optionally per-track stems and the project JSON. Click **Export**, then download the file. Every export is
read back and verified: duration, peak, not silent.

Full walkthrough: [docs/user-guide.md](docs/user-guide.md).

## 9. Troubleshooting

| Problem | Fix |
| --- | --- |
| `uv` / `node` not found | Run the setup script, or open a new terminal so PATH updates take effect |
| Port 8765 already in use | Another ChopLab is running, or set `CHOPLAB_PORT` / use `-Port` |
| PowerShell refuses to run the script | Use `powershell -ExecutionPolicy Bypass -File …` as shown |
| "Cannot decode file" on import | The file is corrupt or not audio. Convert it to WAV and retry |
| Tempo is half/double what you expect | Type the correct BPM in **Correct the analysis** |
| Demucs isn't listed | Install the optional extra (`uv sync --extra separation`). HPSS is always available |
| Separation is very slow | Demucs on CPU takes minutes per song. "Fast" is about 4× quicker than "Quality" |
| No sound in the studio | Click inside the page first (browsers block audio until you interact), and check mute/solo |
| Studio sounds different from the export | The live engine approximates some processing (tape wobble, vinyl, sampled 808, mastering). Use **Render HQ** |
| Running out of disk space | **Settings → Clear render cache**. Project data lives in `data/` (or `CHOPLAB_DATA`) |
| Something else | `cd backend && uv run --no-sync python ../scripts/check_environment.py` |

## Development

```bash
cd backend  && uv run pytest -q                       # backend tests
cd frontend && npm test && npm run typecheck && npm run lint && npm run build
cd frontend && npx playwright test                    # end-to-end (see docs/testing.md)
```

| Doc | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | System overview, modules, data model, playback paths |
| [docs/installation.md](docs/installation.md) | Prerequisites, setup, Demucs, environment variables |
| [docs/user-guide.md](docs/user-guide.md) | Every screen, step by step |
| [docs/audio-engine.md](docs/audio-engine.md) | Analysis, separation, sampling, drums, 808, composition, rendering |
| [docs/testing.md](docs/testing.md) | Test suites and what is (and isn't) verified |

## Known limitations

* Demucs separation is implemented but not exercised by the automated tests (they use HPSS).
* The live studio engine approximates the final render. Render HQ and exports are authoritative.
* Only 4/4 time is supported.
* Automatic checks confirm that the audio is technically valid (levels, key, presence of drums and 808), not
  that the beat sounds good.
