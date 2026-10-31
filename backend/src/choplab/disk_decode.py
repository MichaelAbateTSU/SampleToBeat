"""Bounded decode prerequisite; callers must separately bound downstream analysis.

decode_to_wav publishes a private stereo FLOAT WAV, not an in-memory array.
It does not change audio_io.decode or wire the import/analysis pipeline.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio_io
from .core import SR

BLOCK_FRAMES = 65536


class _ReadError(Exception):
    pass


def _probe(path: Path, timeout: float) -> dict:
    try:
        info = sf.info(path)
        return {"duration": info.duration}
    except sf.LibsndfileError:
        exe = audio_io.ffmpeg_exe()
        if not exe:
            raise audio_io.AudioError("Unsupported format and FFmpeg is not available to decode it.")
        with tempfile.TemporaryFile() as errors:
            try:
                subprocess.run([exe, "-hide_banner", "-i", str(path)], stdout=subprocess.DEVNULL,
                               stderr=errors, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                raise audio_io.AudioError("Audio probing timed out; trim or convert this recording and retry.") from error
            errors.seek(0)
            text = errors.read(65536).decode(errors="replace")
        if not re.search(r"Audio: ([^,]+), (\d+) Hz, ([^,]+)", text):
            raise audio_io.AudioError("File does not contain a decodable audio stream (corrupt or unsupported).")
        duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
        seconds = (int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) if duration else 0
        return {"duration": seconds}


def _discard_cache(fd: int) -> None:
    if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def _write(out: sf.SoundFile, y: np.ndarray, fd: int, max_frames: int) -> None:
    if out.tell() + len(y) > max_frames:
        raise audio_io.AudioError(
            f"Recording is longer than {audio_io.MAX_SECONDS} seconds; trim it before importing."
        )
    if not np.isfinite(y).all():
        y = np.nan_to_num(y)
    out.write(y)
    out.flush()
    os.fsync(fd)
    _discard_cache(fd)


def _soundfile(path: Path, out: sf.SoundFile, fd: int, sr: int) -> None:
    with path.open("rb") as source:
        try:
            inp = sf.SoundFile(source)
        except sf.LibsndfileError as error:
            raise _ReadError() from error
        with inp:
            _read_blocks(source, inp, out, fd, sr)


def _read_blocks(source, inp: sf.SoundFile, out: sf.SoundFile, fd: int, sr: int) -> None:
        if inp.frames / inp.samplerate > audio_io.MAX_SECONDS:
            raise audio_io.AudioError(
                f"Recording is longer than {audio_io.MAX_SECONDS} seconds; trim it before importing."
            )
        g = math.gcd(inp.samplerate, sr)
        up, down = sr // g, inp.samplerate // g
        block = max(down, BLOCK_FRAMES // down * down)
        # Match scipy's default symmetric FIR and global downsampling phase.
        halo = math.ceil((10 * max(up, down) / up + 2) / down) * down
        for start in range(0, inp.frames, block):
            end = min(start + block, inp.frames)
            left = max(0, start - halo)
            right = min(inp.frames, end + halo)
            inp.seek(left)
            try:
                y = audio_io._to_stereo(inp.read(right - left, dtype="float32", always_2d=True))
            except sf.LibsndfileError as error:
                raise _ReadError() from error
            if len(y) != right - left:
                raise audio_io.AudioError("Decoding failed: incomplete audio frames.")
            if up != down:
                y = audio_io.resample(y, inp.samplerate, sr)
            offset = (start - left) * up // down
            count = math.ceil(end * up / down) - start * up // down
            _write(out, y[offset:offset + count], fd, audio_io.MAX_SECONDS * sr)
            _discard_cache(source.fileno())


def _ffmpeg(path: Path, out: sf.SoundFile, fd: int, sr: int, timeout: float) -> None:
    exe = audio_io.ffmpeg_exe()
    if not exe:
        raise audio_io.AudioError("Cannot decode file: unsupported format and FFmpeg unavailable.")
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(
            [exe, "-v", "error", "-i", str(path), "-t", str(audio_io.MAX_SECONDS + 1),
             "-f", "f32le", "-ac", "2", "-ar", str(sr), "-"],
            stdout=subprocess.PIPE, stderr=errors,
        ) as decoder:
            failures: list[Exception] = []

            def consume() -> None:
                assert decoder.stdout is not None
                pending = b""
                try:
                    while chunk := decoder.stdout.read(BLOCK_FRAMES * 8):
                        pending += chunk
                        complete = len(pending) // 8 * 8
                        if complete:
                            y = np.frombuffer(pending[:complete], dtype="<f4").reshape(-1, 2)
                            _write(out, y, fd, audio_io.MAX_SECONDS * sr)
                            pending = pending[complete:]
                    if pending:
                        raise audio_io.AudioError("Decoding failed: incomplete stereo float32 frame.")
                except Exception as error:
                    failures.append(error)
                    decoder.kill()
                finally:
                    decoder.stdout.close()

            reader = threading.Thread(target=consume, name="choplab-pcm-reader")
            reader.start()
            try:
                code = decoder.wait(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                decoder.kill()
                decoder.wait()
                raise audio_io.AudioError(
                    "Audio decoding timed out; trim or convert this recording and retry."
                ) from error
            finally:
                reader.join()
            if failures:
                raise failures[0]
            if code != 0:
                errors.seek(0, os.SEEK_END)
                errors.seek(max(0, errors.tell() - 300))
                detail = errors.read().decode(errors="replace")
                raise audio_io.AudioError(f"Decoding failed: {detail or 'no audio'}")


def decode_to_wav(path: Path, destination: Path, sr: int = SR, *, timeout: float = 180) -> Path:
    """Atomically decode to a mode-0600 stereo float32 WAV at sr (48 kHz by default).

    libsndfile inputs preserve audio_io.decode's first-two-channel/mono-duplication
    and scipy polyphase resampling, using phase-aligned overlapping blocks.
    FFmpeg fallback preserves its existing stereo mixing/resampler and streams PCM.
    No partial destination is published; failures remove temporary files and leave
    an existing destination unchanged. The caller owns the completed file and must
    read bounded regions rather than loading it wholesale for downstream analysis.
    timeout applies to FFmpeg decoding; libsndfile reads are synchronous local I/O.
    """
    path, destination = Path(path), Path(destination)
    if not isinstance(sr, int) or sr <= 0 or timeout <= 0:
        raise ValueError("Sample rate and decoding timeout must be positive.")
    if path.resolve() == destination.resolve():
        raise ValueError("Decoded destination must differ from its source.")
    meta = _probe(path, timeout)
    if meta["duration"] > audio_io.MAX_SECONDS:
        raise audio_io.AudioError(
            f"Recording is longer than {audio_io.MAX_SECONDS} seconds; trim it before importing."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".decode-", suffix=".wav", dir=destination.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w+b") as file:
            with sf.SoundFile(file, mode="w", samplerate=sr, channels=2,
                              format="WAV", subtype="FLOAT", closefd=False) as out:
                try:
                    _soundfile(path, out, file.fileno(), sr)
                except _ReadError:
                    out.seek(0)
                    out.truncate(0)
                    _ffmpeg(path, out, file.fileno(), sr, timeout)
                if out.tell() == 0:
                    raise audio_io.AudioError("Audio file is empty (zero length).")
                out.flush()
            file.flush()
            os.fsync(file.fileno())
            _discard_cache(file.fileno())
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return destination
