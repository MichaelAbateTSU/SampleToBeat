"""Sample transformation engine + effects. Everything is non-destructive: inputs are read from the
source/stem files, results are cached by a hash of source identity, region and settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pedalboard as pb
from scipy.signal import resample as _sresample

from . import audio_io
from .core import SR, cache_key, data_path

DEFAULT_TRANSFORM = {
    "pitch_semitones": 0.0, "pitch_cents": 0.0, "stretch_mode": "stretch", "preserve_formants": False,
    "reverse": False, "gain_db": 0.0, "fade_in_ms": 5.0, "fade_out_ms": 8.0,
}
DEFAULT_FX = {
    "highpass_hz": 0.0, "lowpass_hz": 0.0, "saturation": 0.0, "bitcrush_bits": 0, "reverb": 0.0,
    "delay": 0.0, "delay_beats": 0.75, "width": 1.0, "compress": False, "wobble": 0.0, "vinyl": 0.0,
}


@lru_cache(maxsize=16)
def _load(path: str) -> np.ndarray:
    return audio_io.load(Path(path))


def source_audio(paths: tuple[str, ...]) -> np.ndarray:
    """Sum one or more aligned stems (or just the mix)."""
    ys = [_load(p) for p in paths]
    n = min(len(y) for y in ys)
    return ys[0][:n] if len(ys) == 1 else np.sum([y[:n] for y in ys], axis=0).astype(np.float32)


def region(paths: tuple[str, ...], start: float, end: float) -> np.ndarray:
    y = source_audio(paths)
    a, b = int(round(start * SR)), int(round(end * SR))
    if not (0 <= a < b <= len(y) + 1):
        raise ValueError(f"Invalid sample bounds {start:.3f}-{end:.3f}s for audio of {len(y) / SR:.3f}s")
    return y[a:b].copy()


def fade(y: np.ndarray, in_ms: float, out_ms: float) -> np.ndarray:
    y = y.copy()
    for ms, ramp in ((in_ms, 1), (out_ms, -1)):
        n = min(len(y), int(SR * ms / 1000))
        if n > 1:
            env = np.sin(np.linspace(0, np.pi / 2, n)) ** 2  # smooth equal-power-ish ramp
            if ramp == 1:
                y[:n] *= env[:, None]
            else:
                y[-n:] *= env[::-1, None]
    return y


def zero_cross(y: np.ndarray, max_ms: float = 2.0) -> int:
    """Offset (samples) to the nearest zero crossing within max_ms; keeps transients intact."""
    m = y[: int(SR * max_ms / 1000), 0]
    z = np.where(np.diff(np.signbit(m)))[0]
    return int(z[0]) if len(z) else 0


def _pb(y: np.ndarray, board: pb.Pedalboard) -> np.ndarray:
    return board(np.ascontiguousarray(y.T, dtype=np.float32), SR).T.astype(np.float32)


def pitch_stretch(y: np.ndarray, semitones: float, speed: float, mode: str = "stretch",
                  preserve_formants: bool = False) -> np.ndarray:
    """speed >1 makes it shorter. 'tape' links pitch and speed like varispeed; 'stretch' keeps them independent."""
    if len(y) < 256:
        return y
    if mode == "tape" and abs(semitones) > 1e-3:
        rate = 2 ** (semitones / 12)
        y = _sresample(y, max(2, int(round(len(y) / rate))), axis=0).astype(np.float32)
        semitones, speed = 0.0, speed / rate
    if abs(speed - 1) < 1e-4 and abs(semitones) < 1e-3:
        return y
    out = pb.time_stretch(np.ascontiguousarray(y.T), SR, stretch_factor=float(speed),
                          pitch_shift_in_semitones=float(semitones), high_quality=True,
                          preserve_formants=preserve_formants)
    return out.T.astype(np.float32)


def pan(y: np.ndarray, p: float) -> np.ndarray:
    th = (np.clip(p, -1, 1) + 1) * np.pi / 4
    return y * (np.array([np.cos(th), np.sin(th)]) * np.sqrt(2)).astype(np.float32)


def effects(y: np.ndarray, fx: dict, bpm: float = 90.0, seed: int = 0) -> np.ndarray:
    fx = {**DEFAULT_FX, **(fx or {})}
    chain = []
    if fx["highpass_hz"] > 0:
        chain.append(pb.HighpassFilter(cutoff_frequency_hz=float(fx["highpass_hz"])))
    if fx["lowpass_hz"] > 0:
        chain.append(pb.LowpassFilter(cutoff_frequency_hz=float(fx["lowpass_hz"])))
    if fx["bitcrush_bits"]:
        chain.append(pb.Bitcrush(bit_depth=float(fx["bitcrush_bits"])))
    if fx["compress"]:
        chain.append(pb.Compressor(threshold_db=-18, ratio=3, attack_ms=10, release_ms=120))
    if chain:
        y = _pb(y, pb.Pedalboard(chain))
    if fx["saturation"] > 0:
        d = 1 + 8 * fx["saturation"]
        y = (np.tanh(d * y) / np.tanh(d)).astype(np.float32)
    if fx["wobble"] > 0:  # tape wow: slow modulated fractional delay
        n = np.arange(len(y))
        dly = (1 + np.sin(2 * np.pi * 0.6 * n / SR)) * fx["wobble"] * 0.004 * SR
        idx = np.clip(n - dly, 0, len(y) - 1)
        y = np.stack([np.interp(idx, n, y[:, c]) for c in range(2)], axis=1).astype(np.float32)
    if fx["vinyl"] > 0:
        rng = np.random.default_rng(seed)
        noise = rng.normal(0, 1, (len(y), 1)).astype(np.float32)
        noise = _pb(np.repeat(noise, 2, 1), pb.Pedalboard([pb.LowpassFilter(3000), pb.HighpassFilter(300)]))
        crackle = (rng.random((len(y), 1)) < 3e-4) * rng.normal(0, 1, (len(y), 1))
        y = y + fx["vinyl"] * (0.006 * noise + 0.08 * crackle).astype(np.float32)
    if fx["width"] != 1.0:
        mid, side = (y[:, 0] + y[:, 1]) / 2, (y[:, 0] - y[:, 1]) / 2 * fx["width"]
        y = np.stack([mid + side, mid - side], axis=1).astype(np.float32)
    tail = []
    if fx["delay"] > 0:
        tail.append(pb.Delay(delay_seconds=60 / bpm * fx["delay_beats"], feedback=0.35, mix=float(fx["delay"]) * 0.5))
    if fx["reverb"] > 0:
        tail.append(pb.Reverb(room_size=0.6, damping=0.5, wet_level=float(fx["reverb"]) * 0.4, dry_level=1.0, width=1.0))
    if tail:
        y = _pb(y, pb.Pedalboard(tail))
    return y


def transform(y: np.ndarray, t: dict, speed: float = 1.0) -> np.ndarray:
    t = {**DEFAULT_TRANSFORM, **(t or {})}
    y = pitch_stretch(y, t["pitch_semitones"] + t["pitch_cents"] / 100.0, speed, t["stretch_mode"],
                      t["preserve_formants"])
    if t["reverse"]:
        y = y[::-1].copy()
    y = fade(y, t["fade_in_ms"], t["fade_out_ms"])
    return (y * 10 ** (t["gain_db"] / 20)).astype(np.float32)


_mem: dict[str, np.ndarray] = {}


def cached_transform(paths: tuple[str, ...], hashes: tuple[str, ...], start: float, end: float, t: dict,
                     speed: float = 1.0) -> np.ndarray:
    key = cache_key("tf", hashes, round(start, 5), round(end, 5), t, round(speed, 6))
    if key in _mem:
        return _mem[key]
    f = data_path("cache", "transforms", f"{key}.wav")
    if f.exists():
        y = audio_io.load(f)
    else:
        y = transform(region(paths, start, end), t, speed)
        audio_io.write_wav(f, y)
    if len(_mem) > 256:
        _mem.clear()
    _mem[key] = y
    return y
