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

KITS = {
    "boombap": {"label": "Boom Bap Classic", "kick": (55, 170, 0.28, 0.6, 1.6), "snare": (185, 0.2, 0.9, 9000),
                "hat": (0.05, 7000), "post": {"saturation": 0.25}},
    "trap": {"label": "Modern Trap", "kick": (48, 200, 0.45, 0.8, 2.2), "snare": (200, 0.16, 0.8, 12000),
             "hat": (0.035, 8500), "post": {}},
    "drill": {"label": "Dark Drill", "kick": (50, 180, 0.35, 0.7, 2.0), "snare": (230, 0.14, 1.0, 11000),
              "hat": (0.03, 9000), "post": {"saturation": 0.1}},
    "lofi": {"label": "Lo-fi Warm", "kick": (58, 140, 0.25, 0.3, 1.2), "snare": (175, 0.18, 0.6, 5500),
             "hat": (0.06, 5000), "post": {"lowpass_hz": 6500, "bitcrush_bits": 12, "saturation": 0.3}},
}


def _filt(y, *plugins):
    s = np.ascontiguousarray(np.stack([y, y]), dtype=np.float32)
    return pb.Pedalboard(list(plugins))(s, SR)[0]


def _t(sec):
    return np.arange(int(sec * SR)) / SR


def synth(kit: str, inst: str) -> np.ndarray:
    k = KITS[kit]
    rng = np.random.default_rng(INSTRUMENTS.index(inst) + 7)  # deterministic kit sounds
    if inst == "kick":
        f0, f1, decay, click, drive = k["kick"]
        t = _t(0.9)
        f = f0 + (f1 - f0) * np.exp(-t / 0.025)
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / decay)
        n = int(0.004 * SR)
        y[:n] += click * rng.uniform(-1, 1, n) * np.linspace(1, 0, n)
        y = np.tanh(drive * y)
    elif inst == "snare":
        tone, decay, noise, lp = k["snare"]
        t = _t(0.5)
        body = np.sin(2 * np.pi * tone * t) * np.exp(-t / 0.05)
        nz = _filt(rng.uniform(-1, 1, len(t)).astype(np.float32), pb.HighpassFilter(1200), pb.LowpassFilter(lp))
        y = 0.6 * body + noise * nz * np.exp(-t / decay) * 1.6
    elif inst == "clap":
        t = _t(0.45)
        env = sum(np.where(t >= d, np.exp(-(t - d) / 0.008), 0) for d in (0, 0.011, 0.023)) * 0.6
        env += np.where(t >= 0.03, np.exp(-(t - 0.03) / 0.12), 0)
        y = _filt(rng.uniform(-1, 1, len(t)).astype(np.float32), pb.HighpassFilter(900), pb.LowpassFilter(4000)) * env * 2
    elif inst in ("hat", "openhat", "ride", "crash"):
        decay, hp = k["hat"]
        decay = {"hat": decay, "openhat": decay * 6, "ride": 0.6, "crash": 1.1}[inst]
        t = _t(min(2.0, decay * 8))
        metal = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)) / 6
        y = _filt((0.6 * metal + 0.6 * rng.uniform(-1, 1, len(t))).astype(np.float32),
                  pb.HighpassFilter({"crash": 4000, "ride": 2500}.get(inst, hp))) * np.exp(-t / decay)
    elif inst == "rim":
        t = _t(0.12)
        y = (np.sin(2 * np.pi * 1700 * t) + 0.7 * np.sin(2 * np.pi * 820 * t)) * np.exp(-t / 0.018)
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
    return custom if custom.exists() else data_path("kits", kit, f"{inst}.wav")


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
    "boombap": {"label": "Boom bap", "bpm": (84, 96), "swing": (56, 62), "kit": "boombap"},
    "trap": {"label": "Trap", "bpm": (130, 150), "swing": (50, 52), "kit": "trap"},
    "drill": {"label": "Drill", "bpm": (138, 146), "swing": (50, 51), "kit": "drill"},
    "lofi": {"label": "Lo-fi hip-hop", "bpm": (72, 88), "swing": (58, 64), "kit": "lofi"},
}
KICK_PROB = {
    "boombap": {0: 1, 10: .75, 7: .35, 3: .25, 8: .2, 14: .2, 15: .15, 11: .2},
    "trap": {0: 1, 3: .35, 6: .45, 7: .3, 10: .55, 11: .35, 13: .2, 14: .3},
    "drill": {0: 1, 3: .2, 5: .3, 6: .25, 10: .45, 11: .4, 14: .3},
    "lofi": {0: 1, 7: .4, 10: .6, 8: .15, 2: .15},
}
HUMANIZE = {"boombap": 6, "lofi": 9, "trap": 2, "drill": 2}


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
            h.append(_hit(b0 + s * STEP_TICKS + (10 if genre == "lofi" else 0), "snare", .95 if genre == "boombap" else .75))
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
    amt = HUMANIZE[genre]
    for x in hits:
        if x.get("roll") or x["tick"] % TICKS_PER_BAR == 0:
            continue  # protect the downbeat anchor and rolls
        x["tick"] = int(max(0, x["tick"] + rng.integers(-amt, amt + 1)))
        x["vel"] = round(float(np.clip(x["vel"] + rng.uniform(-.06, .06), .05, 1)), 3)
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
