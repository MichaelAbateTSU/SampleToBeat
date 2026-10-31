#!/usr/bin/env python3
"""ChopLab environment check (stdlib only, so it runs before anything is installed).

Usage:  python scripts/check_environment.py [--port 8765]
Exit code is non-zero if a required component is missing.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OK, WARN, FAIL = "ok", "warn", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, status: str, detail: str) -> None:
    results.append((status, name, detail))


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str]:
    exe = shutil.which(cmd[0])
    if not exe:
        return 127, f"{cmd[0]} not found on PATH"
    try:
        p = subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def version_tuple(s: str) -> tuple[int, ...]:
    digits = "".join(c if c.isdigit() or c == "." else " " for c in s).split()
    return tuple(int(x) for x in digits[0].split(".") if x) if digits else ()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("CHOPLAB_PORT", 8765)))
    args = ap.parse_args()

    # uv manages the backend's Python 3.12 environment
    code, out = run(["uv", "--version"])
    check("uv", OK if code == 0 else FAIL, out if code == 0 else "install from https://docs.astral.sh/uv/ (setup scripts do this)")

    venv = ROOT / "backend" / ".venv"
    if code == 0 and venv.exists():
        code, out = run(["uv", "run", "--no-sync", "python", "-c",
                         "import sys, choplab, numpy, librosa, pedalboard, soundfile; print(sys.version.split()[0])"], cwd=ROOT / "backend")
        check("backend environment", OK if code == 0 else FAIL,
              f"Python {out}" if code == 0 else (out.splitlines()[-1] if out else "import failed"))
        code, out = run(["uv", "run", "--no-sync", "python", "-c", "import audio_separator; print('installed')"], cwd=ROOT / "backend")
        check("Demucs / audio-separator (optional)", OK if code == 0 else WARN,
              "installed" if code == 0 else "not installed - built-in HPSS separation is used (uv sync --extra separation to add it)")
        code, out = run(["uv", "run", "--no-sync", "python", "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"], cwd=ROOT / "backend")
        ff = shutil.which("ffmpeg") or (out.splitlines()[-1] if code == 0 and out else None)
        check("ffmpeg (MP3/AAC/M4A import, MP3 export)", OK if ff else WARN, ff or "not found - WAV/FLAC still work")
    else:
        check("backend environment", FAIL, "backend/.venv missing - run the setup script (uv sync)")

    code, out = run(["node", "--version"])
    if code == 0:
        v = version_tuple(out)
        check("Node.js", OK if v >= (20,) else FAIL, out + ("" if v >= (20,) else " (need 20+)"))
    else:
        check("Node.js", FAIL, "install Node.js 20+ from https://nodejs.org")
    nm = (ROOT / "frontend" / "node_modules").exists()
    check("frontend dependencies", OK if nm else FAIL, "installed" if nm else "run npm ci in frontend/")
    dist = (ROOT / "frontend" / "dist" / "index.html").exists()
    check("frontend build", OK if dist else WARN, "frontend/dist present" if dist else "not built - start scripts build it")

    data = Path(os.environ.get("CHOPLAB_DATA", ROOT / "data"))
    probe = data if data.exists() else data.parent
    try:
        free = shutil.disk_usage(probe).free / 1e9
        check("disk space", OK if free > 2 else WARN, f"{free:.1f} GB free at {probe}")
    except OSError as e:
        check("disk space", WARN, str(e))

    with socket.socket() as s:
        s.settimeout(1)
        busy = s.connect_ex(("127.0.0.1", args.port)) == 0
    check(f"port {args.port}", WARN if busy else OK, "in use (ChopLab already running? set CHOPLAB_PORT)" if busy else "free")

    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"[{status:>4}] {name.ljust(width)}  {detail}")
    failed = [n for s, n, _ in results if s == FAIL]
    print("\nAll required components present." if not failed else f"\nMissing: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
