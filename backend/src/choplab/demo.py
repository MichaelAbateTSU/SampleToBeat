"""Deterministic synthetic test song: 92 BPM, A minor, Am-F-C-G with keys, pad, bass and drums.
Section A (8 bars) is harmonic only (a clean sample source); section B (8 bars) adds drums."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import audio_io, drums
from .core import SR, midi_to_hz

BPM, BARS = 92.0, 16
CHORDS = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]  # Am F C G


def make(bpm: float = BPM, bars: int = BARS) -> np.ndarray:
    spb = 60 / bpm
    n = int(bars * 4 * spb * SR) + SR
    y = np.zeros(n, np.float32)

    def add(sig, at):
        a = int(at * SR)
        sig = sig[: max(0, n - a)]
        y[a:a + len(sig)] += sig

    for bar in range(bars):
        chord = CHORDS[bar % 4]
        t0 = bar * 4 * spb
        t = np.arange(int(4 * spb * SR)) / SR
        pad = sum(np.sin(2 * np.pi * midi_to_hz(m) * t) + 0.3 * np.sin(4 * np.pi * midi_to_hz(m) * t) for m in chord)
        pad *= np.minimum(1, t / 0.05) * np.minimum(1, (4 * spb - t) / 0.05) * 0.06
        add(pad.astype(np.float32), t0)
        for i in range(8):  # keys arpeggio in 8ths
            m = chord[i % 3] + 12 * (i // 3 % 2)
            tt = np.arange(int(spb / 2 * SR)) / SR
            note = np.sin(2 * np.pi * midi_to_hz(m) * tt) * np.exp(-tt / 0.18) * 0.18
            add(note.astype(np.float32), t0 + i * spb / 2)
        tb = np.arange(int(2 * spb * SR)) / SR
        for half in (0, 1):
            b = np.sin(2 * np.pi * midi_to_hz(chord[0] - 24) * tb) * np.exp(-tb / 0.6) * 0.25
            add(b.astype(np.float32), t0 + half * 2 * spb)
        if bar >= bars // 2:
            for beat in range(4):
                add(drums.synth("boombap", "kick" if beat % 2 == 0 else "snare") * 0.5, t0 + beat * spb)
                for e in (0, 0.5):
                    add(drums.synth("boombap", "hat") * 0.15, t0 + (beat + e) * spb)
    y = 0.8 * y / np.abs(y).max()
    return np.stack([y, y * 0.98], axis=1).astype(np.float32)


def write(path: Path) -> Path:
    return audio_io.write_wav(path, make(), subtype="PCM_16")
