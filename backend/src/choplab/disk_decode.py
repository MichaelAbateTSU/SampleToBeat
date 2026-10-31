"""Bounded decode prerequisite; callers must separately bound downstream analysis.

decode_to_wav publishes a private stereo FLOAT WAV, not an in-memory array.
It does not change audio_io.decode or wire the import/analysis pipeline.
"""
from __future__ import annotations

import math
import mmap
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import firwin, resample_poly
from scipy.special import i0

from . import audio_io
from .core import SR

BLOCK_FRAMES = 65536
READ_BYTES = 1 << 20
FILTER_WORK_BYTES = 128 << 20


class _ReadError(Exception):
    pass


def _flush_span(values: np.memmap, start: int, end: int) -> None:
    offset = start * values.dtype.itemsize
    size = (end - start) * values.dtype.itemsize
    values._mmap.flush(offset, size)
    if hasattr(values._mmap, "madvise"):
        values._mmap.madvise(mmap.MADV_DONTNEED, offset, size)


def _disk_fir(directory: Path, taps: int, cutoff: float) -> tuple[np.memmap, Path]:
    """Build the same normalized Kaiser-5 lowpass without full float64 temporaries."""
    fd, name = tempfile.mkstemp(prefix=".decode-fir-", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(fd, "w+b") as file:
            file.truncate(taps * 8)
            values = np.memmap(file, dtype=np.float64, mode="r+", shape=taps)
            alpha = .5 * (taps - 1)
            try:
                for start in range(0, taps, BLOCK_FRAMES):
                    end = min(start + BLOCK_FRAMES, taps)
                    position = np.arange(start, end, dtype=np.float64) - alpha
                    window = i0(5.0 * np.sqrt(1 - ((position / alpha) ** 2))) / i0(5.0)
                    values[start:end] = cutoff * np.sinc(cutoff * position) * window
                    _flush_span(values, start, end)
                values.flush()
                # One contiguous reduction preserves NumPy's original pairwise
                # sum tree; summing independent chunks changes coefficients.
                scale = np.sum(values)
                for start in range(0, taps, BLOCK_FRAMES):
                    end = min(start + BLOCK_FRAMES, taps)
                    values[start:end] /= scale
                    _flush_span(values, start, end)
                values.flush()
                coefficients = np.memmap(file, dtype=np.float32, mode="r+", shape=taps)
                try:
                    # Forward conversion never overwrites unread float64 values.
                    for start in range(0, taps, BLOCK_FRAMES):
                        end = min(start + BLOCK_FRAMES, taps)
                        coefficients[start:end] = values[start:end].astype(np.float32)
                        _flush_span(coefficients, start, end)
                    coefficients.flush()
                finally:
                    coefficients._mmap.close()
            finally:
                values._mmap.close()
            file.truncate(taps * 4)
            file.flush()
            os.fsync(file.fileno())
            _discard_cache(file.fileno())
        return np.memmap(path, dtype=np.float32, mode="r", shape=taps), path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


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


def _soundfile(path: Path, out: sf.SoundFile, fd: int, sr: int, directory: Path) -> None:
    with path.open("rb") as source:
        try:
            inp = sf.SoundFile(source)
        except sf.LibsndfileError as error:
            raise _ReadError() from error
        with inp:
            _read_blocks(source, inp, out, fd, sr, directory)


def _read_blocks(source, inp: sf.SoundFile, out: sf.SoundFile, fd: int, sr: int, directory: Path) -> None:
    filters = []
    try:
        _read_resampled(source, inp, out, fd, sr, directory, filters)
    finally:
        for coefficients, filter_path in filters:
            coefficients._mmap.close()
            filter_path.unlink(missing_ok=True)


def _read_resampled(source, inp: sf.SoundFile, out: sf.SoundFile, fd: int, sr: int,
                    directory: Path, filters: list) -> None:
    if inp.frames / inp.samplerate > audio_io.MAX_SECONDS:
        raise audio_io.AudioError(
            f"Recording is longer than {audio_io.MAX_SECONDS} seconds; trim it before importing."
        )
    g = math.gcd(inp.samplerate, sr)
    up, down = sr // g, inp.samplerate // g
    # Budget the expanded signal AND FIR construction, not just input frames.
    target = (1 << 20) if up > 1024 else BLOCK_FRAMES
    block = max(1, target // up) * down
    # Match scipy's default symmetric FIR and global downsampling phase.
    halo = (10 * max(up, down) + 2 * up + up * down - 1) // (up * down) * down
    frames = min(inp.frames, block + 2 * halo)
    expanded = (frames * up + down - 1) // down
    taps = 20 * max(up, down) + 1
    # upfirdn also allocates the convolution tail before resample_poly slices
    # it away. Include filter padding, row copies and the trimmed float32 copy.
    filtered = ((max(frames, 1) - 1) * up + taps + 2 * down - 1) // down + 1
    phase_only = up != down and (
        taps * 16 > 192 << 20
        or taps * 16 + 32 * (frames + max(expanded, filtered)) + READ_BYTES > 384 << 20
    )
    if inp.channels * 4 > READ_BYTES:
        raise audio_io.AudioError("One audio frame exceeds the bounded decoder's channel-read budget.")
    coefficients = None
    filter_path = None
    if up != down:
        # Cache the unscaled float32 FIR once. resample_poly applies the same
        # up scaling, zero-padding and phase as its default filter each block.
        if taps * 64 > FILTER_WORK_BYTES:
            coefficients, filter_path = _disk_fir(directory, taps, 1.0 / max(up, down))
            filters.append((coefficients, filter_path))
        else:
            coefficients = firwin(taps, 1.0 / max(up, down), window=("kaiser", 5.0)).astype(np.float32)
    read_frames = max(1, min(BLOCK_FRAMES, READ_BYTES // (inp.channels * 4)))
    if phase_only:
        _phase_blocks(source, inp, out, fd, sr, up, down, coefficients, read_frames)
        return
    buffered = np.empty((0, 2), np.float32)
    buffer_start = 0
    for start in range(0, inp.frames, block):
        end = min(start + block, inp.frames)
        left = max(0, start - halo)
        right = min(inp.frames, end + halo)
        buffered = buffered[left - buffer_start:]
        buffer_start = left
        retained = len(buffered)
        next_buffer = np.empty((right - left, 2), np.float32)
        next_buffer[:retained] = buffered
        while retained < len(next_buffer):
            needed = min(read_frames, len(next_buffer) - retained)
            try:
                incoming = audio_io._to_stereo(inp.read(needed, dtype="float32", always_2d=True))
            except sf.LibsndfileError as error:
                raise _ReadError() from error
            if len(incoming) != needed:
                raise audio_io.AudioError("Decoding failed: incomplete audio frames.")
            next_buffer[retained:retained + needed] = incoming
            retained += needed
        buffered = next_buffer
        y = resample_poly(buffered, up, down, axis=0, window=coefficients).astype(np.float32) \
            if up != down else buffered
        offset = (start - left) * up // down
        count = math.ceil(end * up / down) - start * up // down
        for position in range(offset, offset + count, BLOCK_FRAMES):
            _write(out, y[position:min(position + BLOCK_FRAMES, offset + count)],
                   fd, audio_io.MAX_SECONDS * sr)
        _discard_cache(source.fileno())


def _phase_blocks(source, inp: sf.SoundFile, out: sf.SoundFile, fd: int, sr: int,
                  up: int, down: int, coefficients: np.ndarray, read_frames: int) -> None:
    """Evaluate the original float32 polyphase sum in order, with bounded gathers."""
    half = (len(coefficients) - 1) // 2
    pre_pad = down - half % down
    remove = (half + pre_pad) // down
    count = (inp.frames * up + down - 1) // down
    length = len(coefficients) + pre_pad
    while ((inp.frames - 1) * up + length - 1) // down + 1 < count + remove:
        length += 1
    per_phase = (length + up - 1) // up
    size = max(1, min(BLOCK_FRAMES, (1 << 20) * up // down))
    buffered = np.empty((0, 2), np.float32)
    buffer_start = 0
    for start in range(0, count, size):
        end = min(start + size, count)
        time_index = (np.arange(start, end, dtype=np.int64) + remove) * down
        newest, phase = time_index // up, time_index % up
        left = max(0, int(newest[0]) - per_phase + 1)
        right = min(inp.frames, int(newest[-1]) + 1)
        buffered = buffered[left - buffer_start:]
        buffer_start = left
        retained = len(buffered)
        next_buffer = np.empty((right - left, 2), np.float32)
        next_buffer[:retained] = buffered
        while retained < len(next_buffer):
            needed = min(read_frames, len(next_buffer) - retained)
            try:
                incoming = audio_io._to_stereo(inp.read(needed, dtype="float32", always_2d=True))
            except sf.LibsndfileError as error:
                raise _ReadError() from error
            if len(incoming) != needed:
                raise audio_io.AudioError("Decoding failed: incomplete audio frames.")
            next_buffer[retained:retained + needed] = incoming
            retained += needed
        buffered = next_buffer
        result = np.zeros((end - start, 2), np.float32)
        for tap in range(per_phase):
            indices = newest - per_phase + 1 + tap
            filter_indices = phase + (per_phase - 1 - tap) * up - pre_pad
            weights = np.zeros(end - start, np.float32)
            valid_filter = (filter_indices >= 0) & (filter_indices < len(coefficients))
            weights[valid_filter] = coefficients[filter_indices[valid_filter]]
            weights *= up
            if indices[0] >= 0 and indices[-1] < inp.frames:
                result += buffered[indices - left] * weights[:, None]
            else:
                valid_input = (indices >= 0) & (indices < inp.frames)
                positions = indices[valid_input] - left
                result[valid_input] += buffered[positions] * weights[valid_input, None]
        _write(out, result, fd, audio_io.MAX_SECONDS * sr)
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
    Channel reads, expanded workspace and FIR design have separate budgets;
    large ratios use disk-backed FIR construction and ordered phase evaluation
    rather than substituting a different resampling algorithm. This can be slow
    and require significant temporary disk; filesystem failures remain explicit.
    FIR coefficients are cached per decode; sanitization/writes use small blocks.
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
            def fill(fallback: bool) -> None:
                with sf.SoundFile(file, mode="w", samplerate=sr, channels=2,
                                  format="WAV", subtype="FLOAT", closefd=False) as out:
                    if fallback:
                        _ffmpeg(path, out, file.fileno(), sr, timeout)
                    else:
                        _soundfile(path, out, file.fileno(), sr, destination.parent)
                    if out.tell() == 0:
                        raise audio_io.AudioError("Audio file is empty (zero length).")
                    out.flush()

            try:
                fill(False)
            except _ReadError:
                file.seek(0)
                file.truncate(0)
                fill(True)
            file.flush()
            os.fsync(file.fileno())
            _discard_cache(file.fileno())
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return destination
