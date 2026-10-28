"""808 engine: a mono synth with pitch envelope, glide, harmonics and saturation, or a sample transposed
from its root note. Also includes a key- and harmony-aware bassline generator that follows the kick."""
from __future__ import annotations

import numpy as np

from .core import NOTE_NAMES, SR, STEP_TICKS, TICKS_PER_BAR, midi_to_hz, ticks_to_samples

DEFAULT_808 = {
    "mode": "synth", "decay": 0.9, "sustain": 0.0, "release_ms": 25.0, "attack_ms": 2.0, "pitch_env_semis": 7.0,
    "pitch_env_ms": 30.0, "glide_ms": 70.0, "harmonics": 0.25, "drive": 0.35, "gain_db": 0.0,
    "sample_path": None, "root_midi": 36,
}
SCALES = {"major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10]}
LOW, HIGH = 28, 40  # E1..E2: the classic 808 register


def _env(n: int, attack: int, release: int) -> np.ndarray:
    e = np.ones(n, np.float32)
    a, r = min(attack, n // 2), min(release, n // 2)
    if a:
        e[:a] = np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if r:
        e[-r:] *= np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return e


def _chains(notes: list[dict], bpm: float, total: int) -> list[list[tuple[dict, int, int]]]:
    """Mono voice: a later note cuts an earlier one; a slide note that starts while the previous one is
    still sounding is legato (same chain, no retrigger)."""
    ev = sorted(notes, key=lambda n: n["tick"])
    chains: list[list[tuple[dict, int, int]]] = []
    for i, n in enumerate(ev):
        s = ticks_to_samples(n["tick"], bpm)
        e = min(total, s + max(1, ticks_to_samples(n["dur"], bpm)))
        if s >= total:
            break
        if chains:
            pn, ps, pe = chains[-1][-1]
            if s < pe:  # overlap
                chains[-1][-1] = (pn, ps, s)
                if n.get("slide"):
                    chains[-1].append((n, s, e))
                    continue
        chains.append([(n, s, e)])
    return [c for c in chains if c[-1][2] > c[0][1]]


def render(notes: list[dict], bpm: float, total: int, p: dict | None = None) -> np.ndarray:
    p = {**DEFAULT_808, **(p or {})}
    out = np.zeros(total, np.float32)
    if p["mode"] == "sample" and p.get("sample_path"):
        return _render_sample(notes, bpm, total, p)
    for chain in _chains(notes, bpm, total):
        s0, e0 = chain[0][1], chain[-1][2]
        n = e0 - s0
        f = np.empty(n, np.float64)
        prev_hz = None
        for note, s, e in chain:
            hz = midi_to_hz(note["midi"])
            seg = np.arange(e - s) / SR
            if prev_hz is None:  # attack: pitch drops into the note
                f[s - s0:e - s0] = hz * 2 ** (p["pitch_env_semis"] * np.exp(-seg / (p["pitch_env_ms"] / 1000)) / 12)
            else:  # glide in log-frequency
                g = np.clip(seg / max(1e-4, p["glide_ms"] / 1000), 0, 1)
                f[s - s0:e - s0] = prev_hz * (hz / prev_hz) ** g
            prev_hz = hz
        phase = 2 * np.pi * np.cumsum(f) / SR
        h = p["harmonics"]
        y = np.sin(phase) + h * 0.5 * np.sin(2 * phase) + h * 0.3 * np.sin(3 * phase)
        t = np.arange(n) / SR
        amp = np.maximum(np.exp(-t / max(0.02, p["decay"])), p["sustain"]) * chain[0][0].get("vel", 1.0)
        y = y * amp * _env(n, int(p["attack_ms"] * SR / 1000), int(p["release_ms"] * SR / 1000))
        if p["drive"] > 0:
            d = 1 + 6 * p["drive"]
            y = np.tanh(d * y) / np.tanh(d)
        out[s0:e0] += y.astype(np.float32)
    return out * np.float32(10 ** (p["gain_db"] / 20) * 0.8)


def _render_sample(notes, bpm, total, p):
    from pathlib import Path

    from . import audio_io

    src = audio_io.load(Path(p["sample_path"])).mean(axis=1)
    out = np.zeros(total, np.float32)
    # shortcut: sample mode retriggers every note (no glide); use synth mode for slides.
    for chain in _chains(notes, bpm, total):
        for note, s, e in chain:
            ratio = 2 ** ((note["midi"] - p["root_midi"]) / 12)
            idx = np.arange(e - s) * ratio
            idx = idx[idx < len(src) - 1]
            y = np.interp(idx, np.arange(len(src)), src) * note.get("vel", 1.0)
            y *= _env(len(y), int(0.001 * SR), int(p["release_ms"] * SR / 1000))
            out[s:s + len(y)] += y.astype(np.float32)
    return out * np.float32(10 ** (p["gain_db"] / 20))


def duck(bass: np.ndarray, kick_ticks: list[int], bpm: float, amount: float = 0.5, attack_ms: float = 4,
         release_ms: float = 140) -> np.ndarray:
    """Sidechain-style gain dip on every kick so kick and 808 don't stack into clipping."""
    if amount <= 0 or not kick_ticks:
        return bass
    g = np.ones(len(bass), np.float32)
    a, r = max(1, int(attack_ms * SR / 1000)), int(release_ms * SR / 1000)
    shape = np.concatenate([np.linspace(1, 1 - amount, a), 1 - amount * np.exp(-np.arange(r) / (r / 4))])
    for t in kick_ticks:
        s = ticks_to_samples(t, bpm)
        if s < len(g):
            seg = shape[: len(g) - s]
            g[s:s + len(seg)] = np.minimum(g[s:s + len(seg)], seg)
    return bass * g


# ---------------------------------------------------------------- bassline generation

def _register(pc: int) -> int:
    m = 24 + pc
    return m + 12 if m < LOW else m


def bar_roots(bar_chroma: list[list[float]], key: tuple[int, str] | None, key_conf: float) -> list[int]:
    """Pick a pitch class per bar by matching triads to the chroma. If the key is reliable, only diatonic
    roots are allowed. Without usable harmony, use the tonic (or the strongest chroma bin)."""
    roots = []
    scale = None
    if key and key_conf >= 0.35:
        scale = [(key[0] + d) % 12 for d in SCALES.get(key[1], SCALES["minor"])]
    for c in bar_chroma:
        c = np.asarray(c, float)
        if c.sum() < 1e-6:
            roots.append(key[0] if key else 0)
            continue
        c = c / (c.max() + 1e-9)
        best, best_s = None, -1e9
        for r in scale or range(12):
            third = 3 if scale and (r + 3) % 12 in scale and (r + 4) % 12 not in scale else 4
            if not scale:
                third = 3 if c[(r + 3) % 12] > c[(r + 4) % 12] else 4
            s = 1.2 * c[r] + 0.8 * c[(r + third) % 12] + 0.9 * c[(r + 7) % 12]
            if key and r == key[0]:
                s += 0.15  # gentle tonic preference when ambiguous
            if s > best_s:
                best, best_s = r, s
        roots.append(int(best))
    return roots


def generate(roots: list[int], kicks: list[dict], genre: str, seed: int, key: tuple[int, str] | None,
             complexity: float = 0.5) -> list[dict]:
    """Bass notes over len(roots) bars, placed on the kick pattern (bass and kick lock together)."""
    rng = np.random.default_rng(seed + 808)
    bars = len(roots)
    scale = [(key[0] + d) % 12 for d in SCALES.get(key[1], SCALES["minor"])] if key else None
    ks = sorted({k["tick"] - k["tick"] % (STEP_TICKS // 2) for k in kicks if k["inst"] == "kick"})
    if not ks:
        ks = [b * TICKS_PER_BAR for b in range(bars)]
    notes: list[dict] = []
    end = bars * TICKS_PER_BAR
    for i, t in enumerate(ks):
        if t >= end:
            break
        bar = t // TICKS_PER_BAR
        root = _register(roots[bar])
        nxt = ks[i + 1] if i + 1 < len(ks) else end
        midi, slide = root, False
        off = t % TICKS_PER_BAR
        if off and genre in ("trap", "drill") and rng.random() < 0.25 + 0.35 * complexity:
            midi = root + 12 if root + 12 <= HIGH + 12 and rng.random() < 0.5 else root + 7
            slide = rng.random() < 0.6
        elif off and genre in ("boombap", "lofi") and rng.random() < 0.3 * complexity:
            midi = root + 7 if rng.random() < 0.6 else root + 12
        if scale and midi % 12 not in scale:
            midi = root  # never a chromatic surprise by default
        if genre in ("boombap", "lofi"):
            dur = min(nxt - t, TICKS_PER_BAR // 2)
        else:
            dur = nxt - t if rng.random() < 0.8 else max(STEP_TICKS * 2, (nxt - t) // 2)
        notes.append({"type": "note", "tick": int(t), "dur": int(max(STEP_TICKS, dur)), "midi": int(midi),
                      "vel": round(float(0.95 if off == 0 else rng.uniform(0.75, 0.9)), 3), "slide": bool(slide)})
    # trap/drill: a sliding passing note into the next bar's root
    if genre in ("trap", "drill") and bars > 1:
        for b in range(bars - 1):
            if roots[b] != roots[b + 1] and rng.random() < 0.5 * complexity + 0.2:
                t = b * TICKS_PER_BAR + 14 * STEP_TICKS
                if not any(abs(n["tick"] - t) < STEP_TICKS for n in notes):
                    notes.append({"type": "note", "tick": t, "dur": STEP_TICKS * 2, "midi": _register(roots[b + 1]),
                                  "vel": 0.8, "slide": True})
    return sorted(notes, key=lambda n: n["tick"])


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"
