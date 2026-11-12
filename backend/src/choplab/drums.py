"""Drum engine: kits of recorded one-shots (public-domain packs, see assets/drums/SOURCES.md) with synthesized
fallbacks, and a genre-aware, seeded pattern generator."""
from __future__ import annotations

import json
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
LIBRARY = Path(__file__).parent / "assets" / "drums"
SAMPLED = json.loads((LIBRARY / "kits.json").read_text()) if (LIBRARY / "kits.json").exists() else {}
SYNTH = {
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
# every kit a beat can name: the recorded ones, then one synthesized kit per genre (also the fallback for any sound
# a recorded kit lacks, and what older saved beats refer to)
KITS = {**{k: {"label": v["label"], "genre": v["genre"]} for k, v in SAMPLED.items()},
        **{k: {"label": v["label"] + " (synth)", "genre": k} for k, v in SYNTH.items()}}


def kit_for(genre: str, seed: int) -> str:
    """One of the genre's recorded kits, so the flips of a sample do not all share the same drums."""
    names = sorted(k for k, v in SAMPLED.items() if v["genre"] == genre)
    return names[int(seed) % len(names)] if names else genre


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
    k = SYNTH[KITS[kit]["genre"]]
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
    if custom.exists():
        return custom
    recorded = LIBRARY / SAMPLED[kit]["sounds"][inst] if inst in SAMPLED.get(kit, {}).get("sounds", {}) else None
    return recorded if recorded and recorded.exists() else data_path("kits", f"v{KIT_VERSION}", kit, f"{inst}.wav")


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
    "trap": {"label": "Trap", "bpm": (130, 150), "swing": (50, 52), "kit": "trap", "half_time": True},
    "drill": {"label": "Drill", "bpm": (138, 146), "swing": (50, 51), "kit": "drill", "half_time": True},
    "lofi": {"label": "Lo-fi hip-hop", "bpm": (72, 88), "swing": (56, 64), "kit": "lofi"},
}
# Kick patterns, two bars each, as 16th-note steps (0 = beat 1, 4 = beat 2, 8 = beat 3, 12 = beat 4). They are the
# patterns producers actually program, not probabilities: a kick pattern is recognisable or it is noise.
KICKS = {
    "boombap": [([0, 10], [0, 7, 10]), ([0, 6, 8, 11], [0, 6, 10]), ([0, 3, 7], [0, 10, 11]), ([0, 8, 10], [0, 3, 10]),
                ([0, 7, 10], [0, 7, 10, 15])],
    "lofi": [([0, 10], [0, 7, 10]), ([0, 7], [0, 10, 11]), ([0, 10], [0, 10]), ([0, 6, 10], [0, 10])],
    # trap is written half-time: the clap falls on beat 3, so a bar feels twice as long as it reads
    "trap": [([0, 14], [2, 10]), ([0, 12], [4, 14]), ([0, 6], [0, 3, 10]), ([0, 10], [0, 6, 10]), ([0, 7, 10], [0, 14])],
    "drill": [([0, 14], [0, 3, 6, 14]), ([0, 6, 14], [0, 3, 10]), ([0, 10], [0, 3, 6]), ([0, 3, 14], [0, 6, 10])],
}
HUMANIZE = {"boombap": 3, "lofi": 4, "trap": 0, "drill": 0}  # random timing, in ticks (1 tick ~ 1.4 ms at 90 BPM)
# The feel is programmed, not random: each drum sits a constant distance from the grid for the whole beat (the kick
# a touch early, the snare late and lazy in boom bap / lo-fi), while trap and drill stay machine tight.
LAY = {"boombap": {"kick": -4, "snare": 9, "clap": 9, "hat": 0}, "lofi": {"kick": -5, "snare": 16, "clap": 16, "rim": 16, "hat": 4, "perc": 6},
       "trap": {}, "drill": {}}
DRILL_HATS = ([0, 3, 6, 8, 11, 14], [0, 3, 6, 8, 11, 14, 15])  # 3+3+2, the pattern that makes it drill


def _hit(tick, inst, vel, **kw):
    return {"type": "drum", "tick": int(max(0, tick)), "inst": inst, "vel": round(float(np.clip(vel, 0.05, 1)), 3), **kw}


def _roll(start: int, kind: str) -> list[dict]:
    """A hat roll starting at `start`. Rolls fall away or dip in the middle; a roll that gets louder is a build-up,
    and a build-up every few bars is tiring."""
    if kind == "32":  # three 1/32 notes, the middle one ducked
        return [_hit(start + i * STEP_TICKS // 2, "hat", v, roll=True) for i, v in enumerate((.7, .42, .6))]
    if kind == "triplet":  # one beat of 1/16 triplets, falling
        return [_hit(start + i * PPQ // 6, "hat", .72 - .07 * i, roll=True) for i in range(6)]
    return [_hit(start + i * STEP_TICKS // 2, "hat", .7 - .05 * i, roll=True) for i in range(8)]  # one beat of 1/32, falling


def _bar(genre, rng, complexity, bar, kicks, vary=False):
    """One bar. `vary` marks the last bar of a four-bar phrase, the only place hat rolls and extra hits go."""
    b0 = bar * TICKS_PER_BAR
    at = lambda step: b0 + step * STEP_TICKS
    h = [_hit(at(s), "kick", 1.0 if s == 0 else .88) for s in kicks]
    if genre in ("boombap", "lofi"):
        soft = genre == "lofi"
        for s in (4, 12):
            h.append(_hit(at(s), "snare", .78 if soft else .95))
        if complexity > .4 and (vary or rng.random() < .3):  # ghost note leaning into the backbeat
            h.append(_hit(at(int(rng.choice([7, 15]))), "snare", .28))
        skip = int(rng.choice([6, 10])) if soft and rng.random() < .4 else -1  # lo-fi hats breathe
        for s in range(0, 16, 2):
            if s != skip and not (vary and s == 14):
                h.append(_hit(at(s), "hat", (.78 if s % 4 == 0 else .55) * (.8 if soft else 1)))
        if vary:  # the phrase ends on an open hat
            h.append(_hit(at(14), "openhat", .5 if soft else .6))
        if soft and complexity > .5:
            h += [_hit(at(s), "perc", .3) for s in (2, 10)]
    elif genre == "trap":
        h += [_hit(at(8), "snare", .95), _hit(at(8), "clap", .8)]
        hats = [_hit(at(s), "hat", .74 if s % 4 == 0 else .52) for s in range(0, 16, 2)]  # steady eighths
        if vary:
            kind = str(rng.choice(["32", "32", "triplet", "burst"] if complexity > .45 else ["32", "none"]))
            if kind == "32":  # the last two hats of the phrase turn into quick triples
                hats = [x for x in hats if x["tick"] < at(12)] + _roll(at(12), "32") + _roll(at(14), "32")
            elif kind != "none":
                hats = [x for x in hats if x["tick"] < at(12)] + _roll(at(12), kind)
        elif bar % 2 == 1 and complexity > .6 and rng.random() < .5:
            hats = [x for x in hats if x["tick"] != at(6)] + _roll(at(6), "32")
        h += hats
        if bar % 2 == 0 and rng.random() < .35:
            h = [x for x in h if not (x["inst"] == "hat" and x["tick"] == at(14))] + [_hit(at(14), "openhat", .5)]
    else:  # drill
        h.append(_hit(at(8 if bar % 2 == 0 else 12), "snare", .95))  # beat 3, then beat 4: the lopsided backbeat
        grid = DRILL_HATS[bar % 2]
        if vary and rng.random() < .5:  # leave a hole instead of adding more
            grid = [s for s in grid if not 8 <= s < 12]
        hats = [_hit(at(s), "hat", .72 if s in (0, 8) else (.4 if s == 15 else .56)) for s in grid]
        if vary and complexity > .45 and rng.random() < .6:
            hats = [x for x in hats if x["tick"] < at(12)] + _roll(at(12), "triplet")
        h += hats
        if bar % 2 == 1:
            h.append(_hit(at(8), "perc", .5))
        if complexity > .5:
            h += [_hit(at(s), "rim", .42) for s in ((3, 11) if bar % 2 == 0 else (6,))]
    return h


def _humanize(hits, genre, rng):
    amt, lay = HUMANIZE[genre], LAY[genre]
    for x in hits:
        if x.get("roll"):
            continue
        if x["tick"] % TICKS_PER_BAR:  # the downbeat stays put: it is the anchor everything else leans on
            x["tick"] = int(max(0, x["tick"] + lay.get(x["inst"], 0) + (rng.integers(-amt, amt + 1) if amt else 0)))
        x["vel"] = round(float(np.clip(x["vel"] + rng.uniform(-.04, .04), .05, 1)), 3)
    return sorted(hits, key=lambda x: (x["tick"], x["inst"]))


def _ending(genre, rng, bar, kicks, how):
    """The last bar before a new section. "cut": everything but the backbeat stops for the last two beats, the
    oldest transition in hip-hop. "fill": a short drum figure. Neither needs a riser."""
    b0 = bar * TICKS_PER_BAR
    at = lambda step: b0 + step * STEP_TICKS
    h = [x for x in _bar(genre, rng, .3, bar, [k for k in kicks if k < 8]) if x["tick"] < at(8) and x["inst"] != "openhat"]
    backbeat = {"boombap": 12, "lofi": 12, "trap": 8, "drill": 12}[genre]
    if how == "cut":
        return h + [_hit(at(backbeat), "snare", .95)]
    if genre in ("boombap", "lofi"):
        v = .8 if genre == "lofi" else 1
        return h + [_hit(at(s), "hat", .6 * v) for s in (8, 10)] + [_hit(at(10), "kick", .85), _hit(at(12), "snare", .95 * v),
                                                                     _hit(at(14), "snare", .55 * v), _hit(at(15), "snare", .7 * v)]
    if genre == "trap":
        return h + [_hit(at(8), "snare", .95), _hit(at(8), "clap", .8)] + [_hit(at(s), "hat", .55) for s in (8, 10)] \
            + _roll(at(12), "burst") + [_hit(at(14), "snare", .7)]
    return h + [_hit(at(8), "hat", .6), _hit(at(12), "snare", .95)] + [_hit(at(12) + i * PPQ // 3, "snare", .5 + .12 * i) for i in (1, 2)]


def generate(genre: str, complexity: float, seed: int, bars: int = 4) -> dict[str, list[dict]]:
    """Drum patterns. main / alt (another kick pattern, for verses) / sparse are `bars` long and only vary in their
    last bar; fill and cut are two bars long and close a section."""
    rng = np.random.default_rng(seed)
    choices = KICKS[genre]
    i = int(rng.integers(len(choices)))
    k_main, k_alt = choices[i], choices[(i + 1 + int(rng.integers(len(choices) - 1))) % len(choices)]

    def phrase(kicks, n, cx):
        out = []
        for b in range(n):
            last = b == n - 1 and n >= 4
            ks = list(kicks[b % 2])
            if last and cx > .5 and genre != "lofi":  # one extra kick pushes the phrase over the bar line
                ks = sorted(set(ks + [15 if genre == "boombap" else 13]))
            out += _bar(genre, rng, cx, b, ks, vary=last)
        return out

    main = phrase(k_main, bars, complexity)
    alt = phrase(k_alt, bars, complexity * 0.8)
    sparse = [x for x in phrase(k_main, bars, 0.0) if x["inst"] == "hat" or (x["inst"] == "kick" and x["tick"] % TICKS_PER_BAR == 0)]
    lead = _bar(genre, rng, complexity, 0, k_main[0])
    out = {"main": main, "alt": alt, "sparse": sparse,
           "fill": lead + _ending(genre, rng, 1, k_main[1], "fill"), "cut": lead + _ending(genre, rng, 1, k_main[1], "cut")}
    return {k: _humanize([dict(x) for x in v], genre, np.random.default_rng(seed + j)) for j, (k, v) in enumerate(out.items())}
