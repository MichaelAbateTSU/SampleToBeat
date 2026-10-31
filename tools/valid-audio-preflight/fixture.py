"""Deterministic, non-silent PCM24 WAV; never allocates the full fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct

FILE_BYTES = 1 << 30
RATE = 96_000
CHANNELS = 4
SAMPLE_BYTES = 3
ALIGN = CHANNELS * SAMPLE_BYTES
FRAMES = 89_478_480
PERIOD = 960
HEADER_BYTES = 64
BLOCK_FRAMES = PERIOD * 64


def sample(frame: int, channel: int) -> int:
    phase = (frame + channel * (PERIOD // CHANNELS)) % PERIOD
    position = phase if phase < PERIOD // 2 else PERIOD - phase
    return -1_048_576 + (2_097_152 * position // (PERIOD // 2))


def pcm_frame(frame: int) -> bytes:
    return b"".join((sample(frame, channel) & 0xFFFFFF).to_bytes(3, "little")
                    for channel in range(CHANNELS))


def header(frames: int) -> bytes:
    if not 0 < frames <= FRAMES:
        raise ValueError("frames must be positive and no larger than the exact GiB fixture")
    data_size = frames * ALIGN
    total = HEADER_BYTES + data_size
    return (
        b"RIFF" + struct.pack("<I", total - 8) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE,
                               RATE * ALIGN, ALIGN, SAMPLE_BYTES * 8)
        + b"JUNK" + struct.pack("<I", 12) + b"choplab-test"
        + b"data" + struct.pack("<I", data_size)
    )


def chunks(frames: int = FRAMES):
    yield header(frames)
    period = b"".join(pcm_frame(frame) for frame in range(PERIOD))
    block = period * (BLOCK_FRAMES // PERIOD)
    remaining = frames * ALIGN
    while remaining:
        piece = block[:min(len(block), remaining)]
        yield piece
        remaining -= len(piece)


def describe(frames: int = FRAMES) -> dict:
    return {"format": "RIFF/WAVE PCM24", "file_bytes": HEADER_BYTES + frames * ALIGN,
            "frames": frames, "sample_rate": RATE, "channels": CHANNELS,
            "bits_per_sample": 24, "block_align": ALIGN,
            "duration_seconds": frames / RATE, "header_bytes": HEADER_BYTES,
            "pcm_bytes": frames * ALIGN, "max_generator_chunk_bytes": BLOCK_FRAMES * ALIGN}


def write(path: Path, frames: int):
    """Exclusive output; only our partial file is removed on failed generation."""
    partial = path.with_name(path.name + ".part")
    if path.exists():
        raise FileExistsError(path)
    expected = HEADER_BYTES + frames * ALIGN
    header(frames)
    if shutil.disk_usage(path.parent).free < expected + (64 << 20):
        raise OSError("insufficient measured free space for offline fixture plus 64 MiB margin")
    digest, count = hashlib.sha256(), 0
    created = False
    since_flush = 0
    try:
        with partial.open("xb") as stream:
            created = True
            for piece in chunks(frames):
                stream.write(piece)
                digest.update(piece)
                count += len(piece)
                since_flush += len(piece)
                if since_flush >= (8 << 20) and hasattr(os, "posix_fadvise"):
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.posix_fadvise(stream.fileno(), 0, count, os.POSIX_FADV_DONTNEED)
                    since_flush = 0
            stream.flush()
            os.fsync(stream.fileno())
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(stream.fileno(), 0, count, os.POSIX_FADV_DONTNEED)
        if count != expected:
            raise ValueError("fixture size differs from declared header; refusing publication")
        # Hard link provides exclusive destination publication without overwriting.
        os.link(partial, path)
        return {**describe(frames), "actual_bytes": count, "sha256": digest.hexdigest()}
    finally:
        if created:
            partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--describe", action="store_true")
    operation.add_argument("--hash", action="store_true", help="stream offline exact fixture hash")
    operation.add_argument("--write", type=Path)
    parser.add_argument("--frames", type=int, default=FRAMES)
    parser.add_argument("--allow-gib-fixture", action="store_true")
    args = parser.parse_args()
    if not 0 < args.frames <= FRAMES:
        parser.error("frame cap exceeded")
    if args.frames == FRAMES and (args.hash or args.write) and not args.allow_gib_fixture:
        parser.error("exact GiB generation requires --allow-gib-fixture")
    if args.write:
        result = write(args.write, args.frames)
    elif args.hash:
        digest = hashlib.sha256()
        for piece in chunks(args.frames):
            digest.update(piece)
        result = {**describe(args.frames), "sha256": digest.hexdigest()}
    else:
        result = describe(args.frames)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
