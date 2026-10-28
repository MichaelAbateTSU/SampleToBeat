"""Shared constants, paths and musical-time math. All musical positions are integer ticks."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

SR = 48000  # internal working sample rate (stereo float32)
PPQ = 480  # ticks per quarter note
BEATS_PER_BAR = 4
TICKS_PER_BAR = PPQ * BEATS_PER_BAR
STEP_TICKS = PPQ // 4  # one 16th note
SCHEMA_VERSION = 1
ENGINE_VERSION = "2"  # bump to invalidate render/transform caches

DATA_DIR = Path(os.environ.get("CHOPLAB_DATA", Path(__file__).resolve().parents[3] / "data"))

log = logging.getLogger("choplab")


def data_path(*parts: str) -> Path:
    p = DATA_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def seconds_per_beat(bpm: float) -> float:
    return 60.0 / bpm


def seconds_per_bar(bpm: float) -> float:
    return BEATS_PER_BAR * seconds_per_beat(bpm)


def ticks_to_seconds(ticks: float, bpm: float) -> float:
    return ticks / PPQ * seconds_per_beat(bpm)


def ticks_to_samples(ticks: float, bpm: float, sr: int = SR) -> int:
    return int(round(ticks_to_seconds(ticks, bpm) * sr))


def midi_to_hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def hz_to_midi(hz: float) -> float:
    import math

    return 69 + 12 * math.log2(hz / 440.0)


def db_to_gain(db: float) -> float:
    return 10.0 ** (db / 20.0)


def gain_to_db(g: float) -> float:
    import math

    return 20.0 * math.log10(max(g, 1e-12))


def semitones_to_ratio(semis: float) -> float:
    return 2.0 ** (semis / 12.0)


def swing_offset_ticks(tick: int, swing_pct: float) -> int:
    """MPC-style swing: 50 = straight, ~66 = triplet feel. Delays off-beat 16ths only."""
    step = int(round(tick / STEP_TICKS))  # nearest 16th, so humanized hits still swing
    if step % 2 == 0 or abs(tick - step * STEP_TICKS) > STEP_TICKS // 4:
        return 0
    return int(round((2 * swing_pct / 100.0 - 1.0) * STEP_TICKS))


def cache_key(*parts) -> str:
    blob = json.dumps([ENGINE_VERSION, *parts], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def parse_key(key: str | None) -> tuple[int, str] | None:
    """'A minor' -> (9, 'minor')."""
    if not key:
        return None
    name, _, mode = key.partition(" ")
    if name not in NOTE_NAMES:
        return None
    return NOTE_NAMES.index(name), (mode or "major")
