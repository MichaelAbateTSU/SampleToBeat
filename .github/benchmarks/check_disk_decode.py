"""Synthetic fixture creation or blockwise parity check inside the test image."""
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from choplab import demo, disk_decode

root = Path("/evidence")
source = root / "input.wav"
if sys.argv[1] == "fixture":
    y = demo.make(bars=6)
    remaining = 1200 * 48000
    with sf.SoundFile(source, mode="w", samplerate=48000, channels=2, subtype="PCM_16") as out:
        while remaining:
            block = y[:min(remaining, len(y))]
            out.write(block)
            remaining -= len(block)
else:
    (root / "phase").write_text("decode")
    start = time.monotonic()
    output = disk_decode.decode_to_wav(source, root / "decoded.wav")
    decoded_seconds = time.monotonic() - start
    (root / "phase").write_text("parity")
    info = sf.info(output)
    assert info.frames == 1200 * 48000 and info.samplerate == 48000
    assert info.channels == 2 and info.subtype == "FLOAT"
    frames = 0
    with source.open("rb") as original_file, output.open("rb") as actual_file, \
            sf.SoundFile(original_file) as original, sf.SoundFile(actual_file) as actual:
        while len(y := original.read(65536, dtype="float32", always_2d=True)):
            result = actual.read(len(y), dtype="float32", always_2d=True)
            np.testing.assert_array_equal(result, y)
            frames += len(y)
            disk_decode._discard_cache(original_file.fileno())
            disk_decode._discard_cache(actual_file.fileno())
        assert len(actual.read(1)) == 0
    assert frames == 1200 * 48000
    assert not list(root.glob(".decode-*"))
    (root / "parity.json").write_text(json.dumps({
        "decode_seconds": decoded_seconds, "total_seconds": time.monotonic() - start,
        "frames": frames, "duration_seconds": frames / 48000, "sample_rate": info.samplerate,
        "channels": info.channels, "subtype": info.subtype, "float32_sample_bytes": frames * 2 * 4,
        "wav_bytes": output.stat().st_size, "all_samples_exact": True,
        "partial_files": [str(p) for p in root.glob(".decode-*")],
        "output_permissions": oct(output.stat().st_mode & 0o777),
    }, indent=2))
    print((root / "parity.json").read_text(), flush=True)
    (root / "phase").write_text("complete")
