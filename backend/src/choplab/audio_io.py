"""Import service: format validation, decoding to the internal 48 kHz stereo float32 representation,
atomic writes, waveform peaks and playback proxies."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .core import SR

MAX_SECONDS = int(os.environ.get("CHOPLAB_MAX_AUDIO_SECONDS", "300" if os.environ.get("CHOPLAB_MODE") == "hosted" else "1200"))
if not 1 <= MAX_SECONDS <= 1200:
    raise ValueError("CHOPLAB_MAX_AUDIO_SECONDS must be 1-1200")


class AudioError(ValueError):
    """User-actionable audio problem (bad format, empty, too long...)."""


def ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_stereo(y: np.ndarray) -> np.ndarray:
    if y.ndim == 1:
        y = y[:, None]
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    return np.ascontiguousarray(y[:, :2], dtype=np.float32)


def resample(y: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    if sr_in == sr_out:
        return y
    g = np.gcd(int(sr_in), int(sr_out))
    return resample_poly(y, sr_out // g, sr_in // g, axis=0).astype(np.float32)


def probe(path: Path) -> dict:
    """Inspect the real media content (not the extension)."""
    try:
        info = sf.info(str(path))
        return {"format": info.format, "sample_rate": info.samplerate, "channels": info.channels,
                "duration": info.duration, "decoder": "libsndfile"}
    except Exception:
        pass
    exe = ffmpeg_exe()
    if not exe:
        raise AudioError("Unsupported format and FFmpeg is not available to decode it.")
    # ffmpeg -i prints stream info to stderr; argument list (never a shell string) keeps user names safe
    r = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace")
    import re

    m = re.search(r"Audio: ([^,]+), (\d+) Hz, ([^,]+)", r.stderr)
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise AudioError("File does not contain a decodable audio stream (corrupt or unsupported).")
    ch = m.group(3)
    channels = 1 if "mono" in ch else 2
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0
    return {"format": m.group(1).strip(), "sample_rate": int(m.group(2)), "channels": channels,
            "duration": dur, "decoder": "ffmpeg"}


def decode(path: Path, sr: int = SR) -> np.ndarray:
    """Decode any supported file to (n, 2) float32 at `sr`."""
    meta = probe(path)
    if meta["duration"] > MAX_SECONDS:
        raise AudioError(f"Recording is longer than {MAX_SECONDS} seconds; trim it before importing.")
    try:
        y, file_sr = sf.read(str(path), dtype="float32", always_2d=True,
                             frames=(MAX_SECONDS + 1) * meta["sample_rate"])
        y = resample(_to_stereo(y), file_sr, sr)
    except Exception:
        exe = ffmpeg_exe()
        if not exe:
            raise AudioError("Cannot decode file: unsupported format and FFmpeg unavailable.")
        try:
            r = subprocess.run([exe, "-v", "error", "-i", str(path), "-t", str(MAX_SECONDS + 1),
                                "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
                               capture_output=True, timeout=180)
        except subprocess.TimeoutExpired as e:
            raise AudioError("Audio decoding timed out; trim or convert this recording and retry.") from e
        if r.returncode != 0 or not r.stdout:
            raise AudioError(f"Decoding failed: {r.stderr.decode(errors='replace')[-300:] or 'no audio'}")
        y = np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).copy()
    if len(y) == 0:
        raise AudioError("Audio file is empty (zero length).")
    if len(y) / sr > MAX_SECONDS:
        raise AudioError(f"Recording is longer than {MAX_SECONDS} seconds; trim it before importing.")
    if not np.isfinite(y).all():
        y = np.nan_to_num(y)
    return y


def load(path: Path) -> np.ndarray:
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return resample(_to_stereo(y), sr, SR)


def load_region(path: Path, start: int, end: int) -> np.ndarray:
    """Read just an internal 48 kHz region; preserve the existing resampler for other rates."""
    with sf.SoundFile(str(path)) as f:
        if f.samplerate != SR:
            y = load(path)
            if not 0 <= start < end <= len(y) + 1:
                raise ValueError("Invalid sample bounds for audio.")
            return y[start:end].copy()
        if not 0 <= start < end <= len(f) + 1:
            raise ValueError("Invalid sample bounds for audio.")
        f.seek(start)
        return _to_stereo(f.read(end - start, dtype="float32", always_2d=True))


def write_wav(path: Path, y: np.ndarray, sr: int = SR, subtype: str = "FLOAT") -> Path:
    """Atomic write: never leaves a half-written file under the final name."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".wav", dir=path.parent)
    os.close(fd)
    try:
        sf.write(tmp, y, sr, subtype=subtype)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def write_mp3(path: Path, y: np.ndarray, sr: int = SR, bitrate: str = "320k") -> Path:
    exe = ffmpeg_exe()
    if not exe:
        raise AudioError("MP3 export requires FFmpeg.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.mp3")
    pcm = np.clip(y, -1, 1).astype(np.float32).tobytes()
    r = subprocess.run([exe, "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", "-",
                        "-codec:a", "libmp3lame", "-b:a", bitrate, str(tmp)], input=pcm, capture_output=True)
    if r.returncode != 0:
        raise AudioError(f"MP3 encoding failed: {r.stderr.decode(errors='replace')[-300:]}")
    os.replace(tmp, path)
    return path


def peaks(y: np.ndarray, n: int = 2000) -> list[float]:
    mono = np.abs(y).max(axis=1) if y.ndim == 2 else np.abs(y)
    if len(mono) == 0:
        return []
    n = min(n, len(mono))
    edges = np.linspace(0, len(mono), n + 1).astype(int)
    return [round(float(mono[a:b].max()), 4) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
