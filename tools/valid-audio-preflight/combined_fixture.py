"""Exact GiB / exact 1,200-second PCM32 fixture; ancillary padding is not audio."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

import fixture

FILE_BYTES = 1 << 30
RATE = 96000
CHANNELS = 2
FRAMES = 115200000
PADDING = 152141772
BLOCK_BYTES = 737280


def header(frames=FRAMES, padding=PADDING):
    total = 52 + padding + frames * 8
    if not 0 < frames <= FRAMES or not 0 <= padding <= PADDING or padding % 2 or total > FILE_BYTES:
        raise ValueError("invalid capped frames/padding")
    return (b"RIFF" + struct.pack("<I", total - 8) + b"WAVEfmt "
            + struct.pack("<IHHIIHH",16,1,2,RATE,RATE*8,8,32)
            + b"JUNK" + struct.pack("<I",padding))


def chunks(frames=FRAMES, padding=PADDING):
    yield header(frames,padding)
    block = bytes(BLOCK_BYTES)
    remaining = padding
    while remaining:
        piece = block[:min(remaining,BLOCK_BYTES)]
        yield piece
        remaining -= len(piece)
    yield b"data" + struct.pack("<I",frames*8)
    period = b"".join(struct.pack("<ii",fixture.sample(f,0)<<8,fixture.sample(f,1)<<8)
                      for f in range(960))
    block = period * 96
    remaining = frames * 8
    while remaining:
        piece = block[:min(remaining,len(block))]
        yield piece
        remaining -= len(piece)


def describe(frames=FRAMES, padding=PADDING):
    header(frames,padding)
    return {"format":"RIFF/WAVE PCM32","file_bytes":52+padding+frames*8,
            "frames":frames,"sample_rate":RATE,"channels":CHANNELS,
            "bits_per_sample":32,"block_align":8,"duration_seconds":frames/RATE,
            "pcm_bytes":frames*8,"padding_bytes":padding,"structural_bytes":52,
            "header_bytes":52+padding,"max_generator_chunk_bytes":BLOCK_BYTES}


def write(path, frames=FRAMES):
    return fixture.write(path,frames,iterator=chunks,description=describe(frames))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--hash",action="store_true")
    operation.add_argument("--write",type=Path)
    parser.add_argument("--allow-gib-fixture",action="store_true")
    args = parser.parse_args()
    if (args.hash or args.write) and not args.allow_gib_fixture:
        parser.error("full combined generation requires --allow-gib-fixture")
    result = describe()
    if args.write:
        result = write(args.write)
    elif args.hash:
        h = hashlib.sha256()
        for piece in chunks():
            h.update(piece)
        result["sha256"] = h.hexdigest()
    print(json.dumps(result,indent=2))


if __name__ == "__main__":
    main()
