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
from . import memory

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


def _release_file_cache(path: Path) -> None:
    if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
        with open(path, "rb") as f:
            os.fsync(f.fileno())
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    _release_file_cache(path)
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
    return resample_poly(y, sr_out // g, sr_in // g, axis=0).astype(np.float32, copy=False)


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
    from .disk_decode import decode_to_wav

    meta = probe(path)
    if meta["duration"] > MAX_SECONDS:
        raise AudioError(f"Recording is longer than {MAX_SECONDS} seconds; trim it before importing.")
    temporary = memory.private_file(".wav")
    try:
        duration = meta["duration"] or MAX_SECONDS
        memory.require_space(temporary.parent, int(np.ceil(duration * sr)) * 8 + 128)
        decode_to_wav(path, temporary, sr)
        return _load(temporary, sr)
    finally:
        temporary.unlink(missing_ok=True)


def load(path: Path) -> np.ndarray:
    return _load(path, SR)


def _load(path: Path, sr: int) -> np.ndarray:
    with sf.SoundFile(str(path)) as file:
        if file.samplerate != sr:
            return decode(path, sr)
        output = memory.empty((len(file), 2), np.float32)
        block = max(1, min(65536, (1 << 20) // (4 * file.channels)))
        for start in range(0, len(file), block):
            end = min(start + block, len(file))
            chunk = _to_stereo(file.read(end - start, dtype="float32", always_2d=True))
            if len(chunk) != end - start:
                raise AudioError("Decoding failed: incomplete audio frames.")
            output[start:end] = chunk
            memory.release_pages(output)
            _release_file_cache(path)
        return output


def load_region(path: Path, start: int, end: int) -> np.ndarray:
    """Read just an internal 48 kHz region; preserve the existing resampler for other rates."""
    with sf.SoundFile(str(path)) as f:
        if f.samplerate != SR:
            y = load(path)
            if not 0 <= start < end <= len(y) + 1:
                raise ValueError("Invalid sample bounds for audio.")
            region = y[start:end]
            result = memory.empty(region.shape, region.dtype)
            for offset in range(0, len(region), 65536):
                result[offset:offset + 65536] = region[offset:offset + 65536]
                memory.release_pages(y)
                memory.release_pages(result)
            return result
        if not 0 <= start < end <= len(f) + 1:
            raise ValueError("Invalid sample bounds for audio.")
        f.seek(start)
        count = min(end, len(f)) - start
        result = memory.empty((count, 2), np.float32)
        block = max(1, min(65536, (1 << 20) // (4 * f.channels)))
        for offset in range(0, count, block):
            stop = min(offset + block, count)
            chunk = _to_stereo(f.read(stop - offset, dtype="float32", always_2d=True))
            if len(chunk) != stop - offset:
                raise AudioError("Decoding failed: incomplete audio frames.")
            result[offset:stop] = chunk
            memory.release_pages(result)
            _release_file_cache(path)
    _release_file_cache(path)
    return result


def write_wav(path: Path, y: np.ndarray, sr: int = SR, subtype: str = "FLOAT") -> Path:
    """Atomic write: never leaves a half-written file under the final name."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".wav", dir=path.parent)
    os.close(fd)
    try:
        channels = 1 if y.ndim == 1 else y.shape[1]
        with sf.SoundFile(tmp, mode="w", samplerate=sr, channels=channels, subtype=subtype) as out:
            bytes_per_sample = {"PCM_U8": 1, "PCM_S8": 1, "PCM_16": 2, "PCM_24": 3,
                                "PCM_32": 4, "FLOAT": 4, "DOUBLE": 8}.get(out.subtype, 8)
            memory.require_space(path.parent, y.size * bytes_per_sample + 128)
            for start in range(0, len(y), 65536):
                out.write(y[start:start + 65536])
                out.flush()
                _release_file_cache(Path(tmp))
                memory.release_pages(y)
        _release_file_cache(Path(tmp))
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
    # Stream exact float32 PCM; a temporary PCM file would also count against cgroup page-cache RAM.
    try:
        with tempfile.TemporaryFile() as errors:
            with subprocess.Popen([exe, "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2",
                                   "-i", "-", "-codec:a", "libmp3lame", "-b:a", bitrate, str(tmp)],
                                  stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors) as encoder:
                broken = None
                try:
                    for start in range(0, len(y), 65536):
                        encoder.stdin.write(np.clip(y[start:start + 65536], -1, 1).astype(np.float32).tobytes())
                        memory.release_pages(y)
                except BrokenPipeError as exc:
                    broken = exc
                finally:
                    try:
                        encoder.stdin.close()
                    except BrokenPipeError as exc:
                        broken = exc
                code = encoder.wait()
            if code != 0 or broken is not None:
                errors.seek(0, os.SEEK_END)
                errors.seek(max(0, errors.tell() - 300))
                detail = errors.read().decode(errors="replace")
                raise AudioError(f"MP3 encoding failed: {detail or broken}") from broken
        _release_file_cache(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def sample_peak(y: np.ndarray) -> float:
    peak = 0.0
    for start in range(0, len(y), 65536):
        peak = float(np.maximum(peak, np.abs(y[start:start + 65536]).max()))
        memory.release_pages(y)
    return peak


def peaks(y: np.ndarray, n: int = 2000) -> list[float]:
    if len(y) == 0:
        return []
    n = min(n, len(y))
    edges = np.linspace(0, len(y), n + 1).astype(int)
    return [round(sample_peak(y[a:b]), 4) if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])]
