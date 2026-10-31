"""Uncapped independent references, capped decode and blockwise full parity."""
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import scipy
from scipy.signal import resample_poly
import soundfile as sf

from choplab import disk_decode

root = Path("/evidence")
mode, rate, channels, seconds = sys.argv[1], *map(int, sys.argv[2:])
source = root / "input.wav"
reference = root / "reference.wav"
if mode == "fixture":
    count = seconds * rate
    with sf.SoundFile(source, mode="w", samplerate=rate, channels=channels,
                      subtype="PCM_16" if rate > 96000 else "FLOAT") as out:
        for start in range(0, count, 4096):
            t = np.arange(start, min(start + 4096, count), dtype=np.float64) / rate
            y = np.empty((len(t), channels), np.float32)
            for channel in range(channels):
                y[:, channel] = .2 * np.sin(2 * np.pi * (.13 + channel * .03) * t)
            out.write(y)
    # Frozen original sf.read -> first two/mono duplicate -> SciPy default
    # resample_poly -> float32/sanitize. Never invoke production audio_io.decode.
    y, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if channels == 1:
        y = np.repeat(y, 2, axis=1)
    y = np.ascontiguousarray(y[:, :2], dtype=np.float32)
    if rate != 48000:
        g = math.gcd(rate, 48000)
        y = resample_poly(y, 48000 // g, rate // g, axis=0).astype(np.float32)
    if not np.isfinite(y).all():
        y = np.nan_to_num(y)
    sf.write(reference, y, 48000, subtype="FLOAT")
    (root / "reference.json").write_text(json.dumps({
        "rate": rate, "channels": channels, "seconds": seconds,
        "input_frames": count, "input_bytes": source.stat().st_size,
        "sample_sha256": hashlib.sha256(y.tobytes()).hexdigest(),
        "numpy": np.__version__, "scipy": scipy.__version__, "soundfile": sf.__version__,
        "reference": "independent original libsndfile + SciPy resample_poly default",
    }, indent=2))
else:
    (root / "phase").write_text("decode")
    start = time.monotonic()
    output = disk_decode.decode_to_wav(source, root / "decoded.wav")
    elapsed = time.monotonic() - start
    (root / "phase").write_text("parity")
    digest = hashlib.sha256()
    frames = 0
    with reference.open("rb") as golden_fd, output.open("rb") as actual_fd, \
            sf.SoundFile(golden_fd) as golden, sf.SoundFile(actual_fd) as actual:
        while len(expected := golden.read(65536, dtype="float32", always_2d=True)):
            received = actual.read(len(expected), dtype="float32", always_2d=True)
            np.testing.assert_array_equal(received, expected)
            digest.update(received.tobytes())
            frames += len(received)
            disk_decode._discard_cache(golden_fd.fileno())
            disk_decode._discard_cache(actual_fd.fileno())
        assert len(actual.read(1)) == 0
    info = sf.info(output)
    assert frames == seconds * 48000
    assert info.channels == 2 and info.samplerate == 48000 and info.subtype == "FLOAT"
    assert digest.hexdigest() == json.loads((root / "reference.json").read_text())["sample_sha256"]
    assert not list(root.glob(".decode-*"))
    (root / "parity.json").write_text(json.dumps({
        "decode_seconds": elapsed, "total_seconds": time.monotonic() - start,
        "frames": frames, "seconds": frames / 48000, "sample_bytes": frames * 8,
        "output_bytes": output.stat().st_size, "all_samples_exact": True,
        "sample_sha256": digest.hexdigest(), "partial_files": [],
        "output_mode": oct(output.stat().st_mode & 0o777),
    }, indent=2))
    print((root / "parity.json").read_text(), flush=True)
    (root / "phase").write_text("complete")
