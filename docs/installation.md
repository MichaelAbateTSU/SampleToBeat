# Installation

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Windows 10/11 or Linux x86-64 | The backend lockfile targets `win32` and `linux`. macOS is untested |
| [uv](https://docs.astral.sh/uv/) | Installed automatically by the setup scripts. It also installs Python 3.12 for the backend |
| Node.js 20+ | <https://nodejs.org> (Windows: `winget install OpenJS.NodeJS.LTS`) |
| ~2 GB free disk | More if you install Demucs models (~300 MB each) |
| A modern browser | Chrome, Edge or Firefox (Web Audio) |

ffmpeg does **not** need to be installed. `imageio-ffmpeg` ships a binary, which handles MP3/AAC/M4A import and
MP3 export.

## Setup

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
```

Linux:

```bash
chmod +x scripts/*.sh
scripts/setup_unix.sh
```

The scripts:

1. install uv if it is missing
2. run `uv sync` in `backend/` to create `.venv` with Python 3.12 and the pinned dependencies
3. run `npm ci` and `npm run build` in `frontend/`
4. run `scripts/check_environment.py`

### Manual setup

```bash
cd backend && uv sync
cd ../frontend && npm ci && npm run build
```

## Model downloads (optional Demucs separation)

ChopLab works without any model weights. The built-in **HPSS** separator (harmonic/percussive, 2 stems) is always
available and is the default.

For 4-stem separation (vocals, drums, bass, other) with Demucs v4, install the optional extra:

```bash
cd backend
uv sync --extra separation            # or: scripts\setup_windows.ps1 -Separation / scripts/setup_unix.sh --separation
```

This installs [`audio-separator`](https://github.com/nomadkaraoke/python-audio-separator) (CPU build). The model
weights (`htdemucs`, `htdemucs_ft`) download into `data/models/` the first time you run a separation with that model
(**Source** tab → **Model** → **Separate**). After that they work offline. CPU inference takes
several minutes per song. "Quality" (`htdemucs_ft`) is about 4× slower than "Fast".

> Demucs integration is implemented behind the same adapter interface as HPSS, but the automated test suite only
> exercises HPSS. See [testing.md](testing.md).

## Start

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_windows.ps1     # Windows
```

```bash
scripts/start_unix.sh                                                  # Linux
```

This serves the built app at <http://127.0.0.1:8765> and opens a browser. Options:

| Option | Effect |
| --- | --- |
| `-Dev` / `--dev` | Also run the Vite dev server (hot reload) at <http://localhost:5173>, which proxies `/api` to the backend |
| `-Port 9000` / `CHOPLAB_PORT=9000` | Change the backend port |
| `-NoBrowser` | Windows only: don't open a browser |

Equivalent single command: `cd backend && uv run choplab`.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHOPLAB_PORT` | `8765` | Backend port |
| `CHOPLAB_DATA` | `<repo>/data` | Where projects, stems, renders, exports, kits and models live |
| `CHOPLAB_FRONTEND` | `<repo>/frontend/dist` | Built SPA to serve |
| `CHOPLAB_LOG` | `info` | Log level |

## Check your environment

```bash
cd backend && uv run --no-sync python ../scripts/check_environment.py
```

This reports uv, the backend venv, optional Demucs, ffmpeg, Node, frontend install/build, disk space and port
availability.
