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
    from . import audio_io
    from .core import resolve_data

    src = audio_io.load(resolve_data(p["sample_path"])).mean(axis=1)
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
    return bass * (g[:, None] if bass.ndim == 2 else g)


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
        if key and c[best] < 0.45 and c[key[0]] >= c[best]:
            best = key[0]  # the "best" chord's own root is barely played: stay on the tonic
        if roots and best != roots[-1] and best_s < 1.08 * (1.2 * c[roots[-1]] + 0.9 * c[(roots[-1] + 7) % 12] + 0.8 * max(c[(roots[-1] + 3) % 12], c[(roots[-1] + 4) % 12])):
            best = roots[-1]  # not clearly a new chord: hold the previous root rather than jump
        roots.append(int(best))
    return roots


def own_bass_line(y: np.ndarray, bpm: float) -> bool:
    """True if the sample carries a bass line that moves faster than an 808 can follow (a bass guitar playing a
    riff, not a held root). Such a sample already has its low end; a second bass under it can only clash."""
    import librosa

    sr = 22050
    m = librosa.resample(y.astype(np.float32), orig_sr=SR, target_sr=sr)
    beat = int(round(60.0 / bpm * sr))
    if len(m) < 4 * beat or np.abs(m).max() < 1e-4:
        return False
    hop = 512
    spec = np.abs(librosa.stft(m, n_fft=4096, hop_length=hop)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=4096)
    if spec[(f >= 40) & (f < 130)].sum() < 0.12 * spec[f >= 130].sum():
        return False  # no real bass register to protect
    low = np.abs(librosa.cqt(m, sr=sr, hop_length=hop, fmin=librosa.note_to_hz("E1"), n_bins=24, bins_per_octave=12)) ** 2  # E1..D#3
    notes = []
    for i in range(len(m) // (beat // 2)):  # the strongest bass note in every eighth
        a, b = int(i * beat / 2 / hop), max(int(i * beat / 2 / hop) + 1, int((i + 1) * beat / 2 / hop))
        e = low[:, a:b].sum(axis=1)
        notes.append(int(np.argmax(e)) % 12 if e.sum() > 0.25 * low.sum() / max(1, len(m) // (beat // 2)) else None)
    heard = [n for n in notes if n is not None]
    if len(heard) < 8:
        return False
    moves = sum(1 for a, b in zip(heard, heard[1:]) if a != b) / (len(heard) - 1)
    return moves > 0.4  # changes on more than two eighths in five: a line, not a pedal


def roots_from_audio(y: np.ndarray, bpm: float, n_half_bars: int, key: tuple[int, str] | None, key_conf: float) -> list[int]:
    """One root per half bar. If the sample has a bass line of its own, follow it: the lowest strong pitch is
    what a producer matches the 808 to by ear, and it is right even when the chord above is ambiguous. Without
    one, fall back to matching triads against the full harmony. A change of root has to be clearly heard;
    otherwise the previous root is held."""
    import librosa

    sr = 22050
    m = librosa.resample(y.astype(np.float32), orig_sr=SR, target_sr=sr)
    half = int(round(2 * 60.0 / bpm * sr))
    if len(m) < half or np.abs(m).max() < 1e-4:
        return [key[0] if key else 0] * n_half_bars
    hop = 512
    low = np.abs(librosa.cqt(m, sr=sr, hop_length=hop, fmin=librosa.note_to_hz("C1"), n_bins=36, bins_per_octave=12)) ** 2  # C1..B3
    full = librosa.feature.chroma_cqt(y=m, sr=sr, hop_length=hop)
    scale = [(key[0] + d) % 12 for d in SCALES.get(key[1], SCALES["minor"])] if key and key_conf >= 0.35 else None
    total_low = low.sum()
    has_bass = total_low > 0.08 * (np.abs(librosa.stft(m, hop_length=hop)) ** 2).sum()
    roots: list[int] = []
    for i in range(n_half_bars):
        a, b = int(i * half / hop), max(int(i * half / hop) + 2, int((i + 1) * half / hop))
        lo_pc = low[:, a:b].sum(axis=1).reshape(3, 12).sum(axis=0)
        c = full[:, a:b].mean(axis=1) if a < full.shape[1] else np.zeros(12)
        prev = roots[-1] if roots else None
        if c.sum() < 1e-6:
            roots.append(prev if prev is not None else (key[0] if key else 0))
            continue
        c = c / (c.max() + 1e-9)
        cand = None
        if has_bass and lo_pc.sum() > 0.25 * total_low / n_half_bars:
            order = np.argsort(lo_pc)[::-1]
            if lo_pc[order[0]] > 1.6 * lo_pc[order[1]] or (scale and order[0] in scale and order[1] not in scale):
                cand = int(order[0])
        if cand is None:  # triad matching on the full harmony
            best_s = -1e9
            for r in scale or range(12):
                third = max(c[(r + 3) % 12], c[(r + 4) % 12])
                sc = 1.2 * c[r] + 0.8 * third + 0.9 * c[(r + 7) % 12] + (0.15 if key and r == key[0] else 0.0)
                if sc > best_s:
                    cand, best_s = int(r), sc
        if scale and cand not in scale:
            cand = prev if prev is not None else key[0]
        # the root has to be present, and must not sit a semitone under/over something louder
        clash = max(c[(cand + 1) % 12], c[(cand - 1) % 12]) > 1.2 * c[cand]
        if c[cand] < 0.35 or clash:
            cand = prev if prev is not None and not max(c[(prev + 1) % 12], c[(prev - 1) % 12]) > 1.2 * c[prev] else (key[0] if key else cand)
        roots.append(int(cand))
    return roots


def generate(roots: list[int], kicks: list[dict], genre: str, seed: int, key: tuple[int, str] | None,
             complexity: float = 0.5) -> list[dict]:
    """Bass notes; `roots` has one entry per half bar. The line plays the root of each bar and nothing else: a wrong fifth or
    octave against a sample is far more audible than a plain line is dull. It locks to the kick: boom bap and
    lo-fi take the main kicks with short notes, trap and drill hold long 808s and slide into the next root."""
    rng = np.random.default_rng(seed + 808)
    bars = len(roots) // 2
    end = bars * TICKS_PER_BAR
    ks = sorted({k["tick"] - k["tick"] % STEP_TICKS for k in kicks if k["inst"] == "kick" and k["tick"] < end})
    long_808 = genre in ("trap", "drill")
    if long_808:  # fewer, longer notes: the downbeat plus up to two more kicks per bar
        keep = []
        for b in range(bars):
            bar = [t for t in ks if t // TICKS_PER_BAR == b]
            first = [t for t in bar if t % TICKS_PER_BAR == 0] or [b * TICKS_PER_BAR]
            rest = [t for t in bar if t % TICKS_PER_BAR >= 6 * STEP_TICKS]
            n_extra = int(round(2 * complexity)) if rest else 0
            keep += first + sorted(rng.choice(rest, size=min(n_extra, len(rest)), replace=False).tolist() if n_extra else [])
        ks = sorted(set(int(t) for t in keep))
    if not ks:
        ks = [b * TICKS_PER_BAR for b in range(bars)]
    notes: list[dict] = []
    for i, t in enumerate(ks):
        bar = t // TICKS_PER_BAR
        nxt = ks[i + 1] if i + 1 < len(ks) else end
        midi = _register(roots[min(len(roots) - 1, t // (TICKS_PER_BAR // 2))])
        dur = nxt - t if long_808 else min(nxt - t, 6 * STEP_TICKS)
        slide = bool(long_808 and notes and notes[-1]["midi"] != midi and t % TICKS_PER_BAR == 0 and rng.random() < 0.5)
        notes.append({"type": "note", "tick": int(t), "dur": int(max(STEP_TICKS, dur)), "midi": int(midi),
                      "vel": round(float(0.95 if t % TICKS_PER_BAR == 0 else rng.uniform(0.78, 0.9)), 3), "slide": slide})
    return notes


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"
