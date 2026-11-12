"""Drum engine: four synthesized kits (written to WAV so users can swap/import sounds) and a
genre-aware, seeded pattern generator with swing, ghost notes, hat rolls, triplets and fills."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pedalboard as pb

from . import audio_io
from .core import PPQ, SR, STEP_TICKS, TICKS_PER_BAR, data_path

INSTRUMENTS = ["kick", "snare", "clap", "hat", "openhat", "ride", "rim", "perc", "crash"]
# which arrangement track each instrument lives on
TRACK_OF = {"kick": "kick", "snare": "snare", "clap": "snare", "rim": "perc", "hat": "hats", "openhat": "hats", "ride": "hats",
            "perc": "perc", "crash": "fx"}
CHOKE = {"hat": "hh", "openhat": "hh"}

KIT_VERSION = 2  # bump when the synthesis changes, so cached kit files are rebuilt
KITS = {
    # kick: (start Hz, end Hz, sweep ms, decay s, click, drive) / snare: (tone Hz, body decay, noise decay, band lo, band hi)
    "boombap": {"label": "Boom Bap Classic", "kick": (150, 52, 28, 0.22, 0.5, 2.0), "snare": (185, 0.11, 0.16, 600, 6500),
                "hat": (0.035, 6500, 13000), "post": {"saturation": 0.2}},
    "trap": {"label": "Modern Trap", "kick": (190, 58, 18, 0.16, 0.8, 2.6), "snare": (210, 0.08, 0.13, 900, 8500),
             "hat": (0.028, 7500, 14000), "post": {}},
    "drill": {"label": "Dark Drill", "kick": (170, 55, 20, 0.18, 0.7, 2.4), "snare": (235, 0.07, 0.11, 1000, 9000),
              "hat": (0.025, 8000, 14000), "post": {"saturation": 0.1}},
    "lofi": {"label": "Lo-fi Warm", "kick": (120, 60, 35, 0.20, 0.2, 1.4), "snare": (170, 0.12, 0.15, 500, 4200),
             "hat": (0.045, 5000, 9000), "post": {"lowpass_hz": 7000, "bitcrush_bits": 12, "saturation": 0.25}},
}
METAL = (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)  # the six square oscillators of a TR-808 cymbal


def _filt(y, *plugins):
    s = np.ascontiguousarray(np.stack([y, y]), dtype=np.float32)
    return pb.Pedalboard(list(plugins))(s, SR)[0]


def _t(sec):
    return np.arange(int(sec * SR)) / SR


def _band(noise, lo, hi):
    """Steep band-pass (two cascaded stages each side): drum noise needs a defined colour, not white hiss."""
    return _filt(noise.astype(np.float32), pb.HighpassFilter(lo), pb.HighpassFilter(lo), pb.LowpassFilter(hi), pb.LowpassFilter(hi))


def synth(kit: str, inst: str) -> np.ndarray:
    """Analogue-drum-machine recipes: every hit is layered the way the classic circuits (and producers stacking
    samples) build them: a tuned body, a noise layer shaped into its own band, and a short transient on top."""
    k = KITS[kit]
    rng = np.random.default_rng(INSTRUMENTS.index(inst) + 7)  # deterministic kit sounds
    white = lambda n: rng.uniform(-1, 1, n)
    if inst == "kick":
        f0, f1, sweep, decay, click, drive = k["kick"]
        t = _t(0.7)
        f = f1 + (f0 - f1) * np.exp(-t / (sweep / 1000))
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / decay)
        punch = np.sin(2 * np.pi * np.cumsum(2.2 * f) / SR) * np.exp(-t / 0.022) * 0.35  # knock in the low mids
        y = body + punch
        n = int(0.005 * SR)
        y[:n] += click * 0.5 * _band(white(n * 4), 1800, 9000)[:n] * np.linspace(1, 0, n) ** 2
        y = np.tanh(drive * y) / np.tanh(drive)
        y = _filt(y.astype(np.float32), pb.HighpassFilter(28), pb.LowpassFilter(10000))
    elif inst == "snare":
        tone, body_decay, noise_decay, lo, hi = k["snare"]
        t = _t(0.45)
        drop = 1 + 0.6 * np.exp(-t / 0.012)  # the shell pitch falls as it is struck
        body = (np.sin(2 * np.pi * np.cumsum(tone * drop) / SR) + 0.55 * np.sin(2 * np.pi * np.cumsum(tone * 1.78 * drop) / SR)) * np.exp(-t / body_decay)
        wires = _band(white(len(t)), lo, hi) * np.exp(-t / noise_decay)
        snap = _band(white(len(t)), 4000, 14000) * np.exp(-t / 0.004)
        y = np.tanh(1.6 * (0.95 * body + 0.85 * wires + 0.35 * snap))
    elif inst == "clap":
        t = _t(0.4)
        env = sum(np.where(t >= d, np.exp(-(t - d) / 0.006), 0) for d in (0.0, 0.009, 0.018, 0.028)) * 0.7
        env += np.where(t >= 0.03, np.exp(-(t - 0.03) / 0.09), 0)
        y = np.tanh(1.5 * (_band(white(len(t)), 900, 3800) * 2.2 + 0.35 * _band(white(len(t)), 5000, 12000)) * env)
    elif inst in ("hat", "openhat", "ride", "crash"):
        decay, lo, hi = k["hat"]
        decay = {"hat": decay, "openhat": decay * 8, "ride": 0.5, "crash": 1.0}[inst]
        lo = {"crash": 3500, "ride": 4500}.get(inst, lo)
        t = _t(min(2.2, decay * 9))
        metal = sum(np.sign(np.sin(2 * np.pi * f * 1.48 * t)) for f in METAL) / 6
        mix = 0.75 * metal + 0.35 * white(len(t)) if inst in ("hat", "openhat", "ride") else 0.45 * metal + 0.7 * white(len(t))
        y = _band(mix, lo, hi) * np.exp(-t / decay)
        y[: int(0.002 * SR)] *= np.linspace(0.3, 1, int(0.002 * SR))
    elif inst == "rim":
        t = _t(0.12)
        y = (np.sin(2 * np.pi * 1700 * t) + 0.7 * np.sin(2 * np.pi * 820 * t)) * np.exp(-t / 0.016) + 0.4 * _band(white(len(t)), 2500, 9000) * np.exp(-t / 0.004)
    else:  # perc: conga-like tom
        t = _t(0.35)
        f = 210 + 90 * np.exp(-t / 0.02)
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.09)
    y = np.asarray(y, dtype=np.float32)
    n = min(len(y), int(0.003 * SR))
    y[-n:] *= np.linspace(1, 0, n)  # no click at the tail
    y = 0.9 * y / (np.abs(y).max() + 1e-9)
    if k["post"]:
        from .transform import effects

        y = effects(np.stack([y, y], 1), k["post"])[:, 0]
        y = 0.9 * y / (np.abs(y).max() + 1e-9)
    return y.astype(np.float32)


def kit_path(kit: str, inst: str) -> Path:
    custom = data_path("kits", kit, "custom", f"{inst}.wav")
    return custom if custom.exists() else data_path("kits", f"v{KIT_VERSION}", kit, f"{inst}.wav")


@lru_cache(maxsize=64)
def _sound(path: str, mtime: float) -> np.ndarray:
    return audio_io.load(Path(path))


def sound(kit: str, inst: str) -> np.ndarray:
    p = kit_path(kit, inst)
    if not p.exists():  # never reference a missing asset: synthesize on demand
        y = synth(kit, inst)
        audio_io.write_wav(p, np.stack([y, y], 1))
    return _sound(str(p), p.stat().st_mtime)


def import_sound(kit: str, inst: str, src: Path) -> Path:
    y = audio_io.decode(src)[: SR * 4]
    return audio_io.write_wav(data_path("kits", kit, "custom", f"{inst}.wav"), y)


def reset_sound(kit: str, inst: str) -> None:
    p = data_path("kits", kit, "custom", f"{inst}.wav")
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------- patterns

GENRES = {
    "boombap": {"label": "Boom bap", "bpm": (84, 96), "swing": (54, 62), "kit": "boombap"},
    "trap": {"label": "Trap", "bpm": (130, 150), "swing": (50, 52), "kit": "trap"},
    "drill": {"label": "Drill", "bpm": (138, 146), "swing": (50, 51), "kit": "drill"},
    "lofi": {"label": "Lo-fi hip-hop", "bpm": (72, 88), "swing": (56, 64), "kit": "lofi"},
}
KICK_PROB = {
    "boombap": {0: 1, 10: .75, 7: .35, 3: .25, 8: .2, 14: .2, 15: .15, 11: .2},
    "trap": {0: 1, 3: .35, 6: .45, 7: .3, 10: .55, 11: .35, 13: .2, 14: .3},
    "drill": {0: 1, 3: .2, 5: .3, 6: .25, 10: .45, 11: .4, 14: .3},
    "lofi": {0: 1, 7: .4, 10: .6, 8: .15, 2: .15},
}
HUMANIZE = {"boombap": 3, "lofi": 4, "trap": 0, "drill": 0}  # random timing, in ticks (1 tick ~ 1.4 ms at 90 BPM)
# The feel is programmed, not random: each drum sits a constant distance from the grid for the whole beat (the kick
# a touch early, the snare late and lazy in boom bap / lo-fi), while trap and drill stay machine tight.
LAY = {"boombap": {"kick": -4, "snare": 9, "hat": 0}, "lofi": {"kick": -5, "snare": 16, "hat": 4, "perc": 6},
       "trap": {}, "drill": {}}


def _hit(tick, inst, vel, **kw):
    return {"type": "drum", "tick": int(max(0, tick)), "inst": inst, "vel": round(float(np.clip(vel, 0.05, 1)), 3), **kw}


def _kicks(genre, rng, complexity, bar):
    out = []
    for step, p in KICK_PROB[genre].items():
        if step == 0 or rng.random() < min(1.0, p * (0.5 + complexity)):
            out.append(_hit(bar * TICKS_PER_BAR + step * STEP_TICKS, "kick", 1.0 if step == 0 else rng.uniform(.8, .95)))
    return out


def _bar(genre, rng, complexity, bar, kicks=True):
    b0 = bar * TICKS_PER_BAR
    h = _kicks(genre, rng, complexity, bar) if kicks else []
    if genre in ("boombap", "lofi"):
        for s in (4, 12):
            h.append(_hit(b0 + s * STEP_TICKS, "snare", .95 if genre == "boombap" else .75))
        for s in (7, 9, 15, 13):
            if rng.random() < .22 * complexity:
                h.append(_hit(b0 + s * STEP_TICKS, "snare", rng.uniform(.22, .34)))  # ghost note
        for s in range(0, 16, 2):
            h.append(_hit(b0 + s * STEP_TICKS, "hat", (.8 if s % 4 == 0 else .6) * (.75 if genre == "lofi" else 1)))
            if complexity > .6 and rng.random() < .3:
                h.append(_hit(b0 + (s + 1) * STEP_TICKS, "hat", .32))
        if rng.random() < .35:
            h = [x for x in h if not (x["inst"] == "hat" and x["tick"] == b0 + 14 * STEP_TICKS)]
            h.append(_hit(b0 + 14 * STEP_TICKS, "openhat", .55))
        if genre == "lofi":
            h += [_hit(b0 + s * STEP_TICKS, "perc", .25) for s in range(1, 16, 4) if rng.random() < .5 * complexity]
    else:
        snares = [8] if genre == "trap" or bar % 2 == 0 else [8, int(rng.choice([11, 13]))]
        for s in snares:
            h.append(_hit(b0 + s * STEP_TICKS, "snare", .95))
            if genre == "trap":
                h.append(_hit(b0 + s * STEP_TICKS, "clap", .8))
        if genre == "trap":
            base = 1 if complexity > .45 else 2
            hats = [_hit(b0 + s * STEP_TICKS, "hat", .75 if s % 4 == 0 else rng.uniform(.45, .65)) for s in range(0, 16, base)]
        else:  # drill: skippy triplet-feel groups
            grid = [0, 3, 6, 8, 11, 14] if bar % 2 == 0 else [0, 3, 6, 10, 12, 14]
            hats = [_hit(b0 + s * STEP_TICKS, "hat", rng.uniform(.55, .8)) for s in grid]
            h += [_hit(b0 + s * STEP_TICKS, "rim", .5) for s in (3, 7, 11) if rng.random() < .4 + .3 * complexity]
        # hat rolls: replace one beat with 1/32 or 1/16-triplet ratchets
        if rng.random() < .35 + .5 * complexity:
            beat = int(rng.choice([1, 3]) if genre == "trap" else rng.choice([1, 2, 3]))
            div = int(rng.choice([8, 6, 12 if complexity > .7 else 8]))  # hits per beat
            lo, hi = b0 + beat * PPQ, b0 + (beat + 1) * PPQ
            hats = [x for x in hats if not lo <= x["tick"] < hi]
            hats += [_hit(lo + i * PPQ // div, "hat", .35 + .55 * i / div, roll=True) for i in range(div)]
        h += hats
        if rng.random() < .3:
            h.append(_hit(b0 + int(rng.choice([6, 14])) * STEP_TICKS, "openhat", .5))
    return h


def _humanize(hits, genre, rng):
    amt, lay = HUMANIZE[genre], LAY[genre]
    for x in hits:
        if x.get("roll"):
            continue
        if x["tick"] % TICKS_PER_BAR:  # the downbeat stays put: it is the anchor everything else leans on
            x["tick"] = int(max(0, x["tick"] + lay.get(x["inst"], 0) + (rng.integers(-amt, amt + 1) if amt else 0)))
        x["vel"] = round(float(np.clip(x["vel"] + rng.uniform(-.05, .05), .05, 1)), 3)
    return sorted(hits, key=lambda x: (x["tick"], x["inst"]))


def _fill_bar(genre, rng, bar):
    b0 = bar * TICKS_PER_BAR
    h = [x for x in _bar(genre, rng, .5, bar) if x["tick"] < b0 + 12 * STEP_TICKS]
    if genre in ("boombap", "lofi"):
        h += [_hit(b0 + s * STEP_TICKS, "snare", .5 + .15 * i) for i, s in enumerate((12, 13, 14, 15))]
    elif genre == "trap":
        h += [_hit(b0 + 12 * STEP_TICKS + i * STEP_TICKS // 2, "hat", .4 + .07 * i, roll=True) for i in range(8)]
        h += [_hit(b0 + 15 * STEP_TICKS, "snare", .9)]
    else:
        h += [_hit(b0 + 12 * STEP_TICKS + i * PPQ // 3, "snare", .55 + .12 * i) for i in range(3)]
    return h


def generate(genre: str, complexity: float, seed: int, bars: int = 2) -> dict[str, list[dict]]:
    """Variant patterns (each `bars` long): main, alt (different kicks), sparse, fill."""
    rng = np.random.default_rng(seed)
    main = [x for b in range(bars) for x in _bar(genre, rng, complexity, b)]
    core = [x for x in main if x["inst"] != "kick"]
    alt = core + [x for b in range(bars) for x in _kicks(genre, rng, complexity, b)]  # same groove, new kicks
    sparse = [x for x in main if x["inst"] in ("hat",) or (x["inst"] == "kick" and x["tick"] % TICKS_PER_BAR == 0)]
    fill = [x for x in main if x["tick"] < (bars - 1) * TICKS_PER_BAR] + _fill_bar(genre, rng, bars - 1)
    return {k: _humanize([dict(x) for x in v], genre, np.random.default_rng(seed + i))
            for i, (k, v) in enumerate({"main": main, "alt": alt, "sparse": sparse, "fill": fill}.items())}
