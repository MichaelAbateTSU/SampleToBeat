"""Beat generator (spec 26): chooses samples, tempo, pitch, chop sequences, drums, 808 and an arrangement,
then evaluates the result. Every decision is seeded so a variation can be reproduced from its seed."""
from __future__ import annotations

import hashlib
import json
from math import lcm

import librosa
import numpy as np

from . import bass, drums, render, transform
from .core import NOTE_NAMES, PPQ, SR, STEP_TICKS, TICKS_PER_BAR, parse_key

STRATEGIES = {
    "loop_flip": "Loop flip: the best phrase at its own pitch, looped, with a re-ordered take in the verses",
    "hard_chop": "Chopped: the phrase cut on the beat and re-sequenced into a new pattern",
    "dark": "Slowed: pitched down by slowing it like a record, darker and heavier",
    "creative": "Sped up: pitched up like a 45 at the wrong speed, brighter and faster",
}
ORDER = list(STRATEGIES)
# How a sample is fitted to a genre. A producer changes a record's speed (pitch and tempo together) before
# reaching for time-stretch, which smears the sound: so each strategy has semitone options, and whatever tempo
# gap is left is stretched by at most MAX_STRETCH. FEEL is the tempo of the drum grid (trap and drill count in
# double time, so an 70 BPM sample sits under a 140 BPM grid untouched).
FEEL = {"boombap": (80, 100), "lofi": (68, 90), "trap": (124, 160), "drill": (134, 150)}
PITCH_OPTIONS = {"loop_flip": [0, -1, 1], "hard_chop": [0, -1, 1, -2, 2], "dark": [-3, -4, -2, -5], "creative": [3, 4, 2, 5]}
MAX_STRETCH = 0.08
DRUMMY = 0.3  # share of percussive energy above which the sample's own drums are removed
SAMPLE_FX = {  # the sample is high-passed so the new bass owns the low end; little else, it is the star
    "boombap": ("Dusty", {"highpass_hz": 110, "saturation": 0.12}),
    "lofi": ("Lo-fi", {"highpass_hz": 100, "lowpass_hz": 6500, "saturation": 0.2, "wobble": 0.25, "vinyl": 0.4}),
    "trap": ("Wide", {"highpass_hz": 140, "reverb": 0.15, "width": 1.15}),
    "drill": ("Dark", {"highpass_hz": 150, "lowpass_hz": 9000, "reverb": 0.12}),
}
BASS_808 = {
    "boombap": {"decay": 0.38, "glide_ms": 0.0, "drive": 0.25, "harmonics": 0.35, "pitch_env_semis": 3.0, "duck": 0.5},
    "lofi": {"decay": 0.45, "glide_ms": 0.0, "drive": 0.15, "harmonics": 0.3, "pitch_env_semis": 2.0, "duck": 0.45},
    "trap": {"decay": 1.1, "glide_ms": 70.0, "drive": 0.5, "harmonics": 0.3, "duck": 0.6},
    "drill": {"decay": 0.9, "glide_ms": 55.0, "drive": 0.55, "harmonics": 0.3, "pitch_env_semis": 8.0, "duck": 0.6},
}
SAMPLE_DUCK = {"boombap": 0.18, "lofi": 0.3, "trap": 0.15, "drill": 0.15}  # how far the sample dips on each kick
TRACKS = [  # id, name, kind, color, volume_db, pan
    ("sample", "Sample", "sample", "#f59e0b", 1.5, 0.0),
    ("sample2", "Sample (filtered)", "sample", "#fb7185", 1.5, 0.0),
    ("kick", "Kick", "drums", "#ef4444", -3.0, 0.0),
    ("snare", "Snare / clap", "drums", "#3b82f6", -2.5, 0.0),
    ("hats", "Hi-hats", "drums", "#22d3ee", -8.5, 0.2),
    ("perc", "Percussion", "drums", "#a78bfa", -11.0, -0.2),
    ("bass", "808", "bass", "#22c55e", -8.5, 0.0),
    ("fx", "Transitions / FX", "drums", "#94a3b8", -16.0, 0.0),
]
LANES = ["kick", "snare", "hats", "perc"]
TEMPLATES = {
    "standard": [("Intro", 4), ("Hook", 8), ("Verse", 8), ("Breakdown", 4), ("Hook", 8), ("Outro", 4)],
    "verse_hook": [("Intro", 4), ("Verse", 8), ("Hook", 8), ("Verse", 8), ("Hook", 8), ("Outro", 4)],
    "loop": [("Intro", 4), ("Main", 16), ("Breakdown", 4), ("Main", 8), ("Outro", 4)],
}
ROLE = {"Intro": "intro", "Hook": "main", "Main": "main", "Verse": "verse", "Breakdown": "breakdown", "Outro": "outro"}
CHOP_STEPS = {  # 16th positions -> probability; 0 and 8 are anchors
    "boombap": {0: 1, 8: 1, 3: .5, 6: .6, 10: .5, 11: .3, 14: .5, 4: .3, 12: .3},
    "lofi": {0: 1, 8: 1, 6: .5, 10: .4, 14: .4, 3: .3},
    "trap": {0: 1, 8: 1, 6: .4, 12: .5, 14: .3, 3: .3},
    "drill": {0: 1, 8: 1, 6: .6, 10: .6, 3: .4, 14: .4},
}
DEFAULTS = {"genre": "boombap", "bpm": None, "bars": 36, "structure": "auto", "energy": 0.6, "chop_intensity": 0.5,
            "bass_intensity": 0.6, "drum_complexity": 0.5, "swing": None, "variations": 4, "sample_mode": "auto",
            "sample_ids": [], "seed": None}


# ---------------------------------------------------------------- building blocks

def sections(template: str, bars: int) -> list[dict]:
    if bars < 12:
        return [{"name": "Main", "start_bar": 0, "bars": bars}]
    t = TEMPLATES[template]
    tot = sum(b for _, b in t)
    want = [b * bars / tot for _, b in t]
    sizes = [max(2, int(round(w / 2)) * 2) for w in want]
    while (d := bars - sum(sizes)) != 0:  # hand the rounding error to whichever section is furthest from its share
        if d > 0:
            i = max(range(len(sizes)), key=lambda i: want[i] - sizes[i])
            sizes[i] += min(2, d)
        else:
            i = min((i for i in range(len(sizes)) if sizes[i] > 2), key=lambda i: want[i] - sizes[i])
            sizes[i] -= min(2, -d)
    out, s = [], 0
    for (name, _), b in zip(t, sizes):
        out.append({"name": name, "start_bar": s, "bars": b})
        s += b
    return out


def fit(src_bpm: float, genre: str, strategy: str, user_bpm: float | None = None) -> tuple[float, float, int]:
    """(project bpm, ratio, semitones). ratio = project beats per source beat (2 = the sample runs half-time
    under the grid). Picks the record-speed change and half/double-time reading that need the least stretching."""
    lo, hi = FEEL[genre]
    best = None
    for rank, st in enumerate(PITCH_OPTIONS[strategy]):
        for k in (0.5, 1.0, 2.0):
            natural = src_bpm * 2 ** (st / 12) * k
            target = float(user_bpm) if user_bpm else float(np.clip(natural, lo, hi))
            cost = abs(np.log2(target / natural)) + 0.012 * rank
            if best is None or cost < best[0] - 1e-9:
                best = (cost, target, k, st, natural)
    _, target, k, st, natural = best
    if not user_bpm and abs(target / natural - 1) > MAX_STRETCH:  # leave the usual range rather than smear the sample
        target = natural * (1 + MAX_STRETCH if target > natural else 1 - MAX_STRETCH)
    return round(target, 2), k, st


def target_tempo(src_bpm: float, genre: str, user_bpm: float | None = None) -> tuple[float, float]:
    bpm, ratio, _ = fit(src_bpm, genre, "loop_flip", user_bpm)
    return bpm, ratio


# A phrase is cut into 8 equal units (beats of a 2-bar loop, half-bars of a 4-bar one) and replayed in a new
# order. Every cut is on the beat, so the result always sits on the drums; the orders keep runs of the original
# so it still sounds like the record, which random slicing does not.
ORDERS = {
    "loop": [0, 1, 2, 3, 4, 5, 6, 7],
    "front_swap": [0, 1, 2, 3, 0, 1, 6, 7],
    "repeat_half": [0, 1, 0, 1, 4, 5, 6, 7],
    "back_first": [4, 5, 2, 3, 4, 5, 6, 7],
    "call_response": [0, 1, 4, 5, 2, 3, 6, 7],
    "tail_loop": [0, 1, 2, 3, 4, 5, 4, 5],
    "stutter": [0, 0, 1, 2, 4, 4, 5, 6],
}


def unit_pattern(s: dict, ratio: float, pitch: float, order: str, **flags) -> dict:
    beats = float(s["beats"])
    total = int(round(beats * ratio * PPQ))  # one pass of the phrase, in project ticks
    seq = ORDERS[order]
    if beats * ratio < 3.5:  # very short phrase: four units, so a unit is never shorter than half a beat
        seq = sorted(set(u // 2 for u in seq), key=[u // 2 for u in seq].index)[:4] if order == "loop" else [u // 2 for u in seq[::2]]
    n = len(seq)
    unit_t, unit_b = total / n, beats / n
    ev, i = [], 0
    while i < n:
        run = 1
        while i + run < n and seq[i + run] == seq[i + run - 1] + 1:
            run += 1  # consecutive units play through as one piece: no retrigger click, the phrase breathes
        t0, t1 = int(round(i * unit_t)), int(round((i + run) * unit_t))
        ev.append(_chop(t0, t1 - t0, s, seq[i] * unit_b, (seq[i] + run) * unit_b, ratio, pitch, 0.9, **flags))
        i += run
    reps = max(1, int(np.ceil(TICKS_PER_BAR / total)))
    events = [{**e, "tick": e["tick"] + r * total} for r in range(reps) for e in ev]
    length = int(np.ceil(total * reps / TICKS_PER_BAR)) * TICKS_PER_BAR
    return {"kind": "sample", "length_ticks": length, "events": events}


def slices(s: dict, onsets: list[float], div: int) -> list[tuple[float, float]]:
    """Straight grid slices (in source beats) snapped to nearby transients, so cuts land on attacks."""
    beats = float(s["beats"])
    per = 60.0 / s["source_bpm"]
    beat0 = s["start"] + s.get("pickup", 0.0)
    rel = [(t - beat0) / per for t in onsets if s["start"] <= t < s["end"]]
    starts = []
    for g in np.arange(0, beats, 1.0 / div):
        near = [r for r in rel if abs(r - g) < 0.2 / div and r >= 0]
        starts.append(round(float(min(near, key=lambda r: abs(r - g)) if near else g), 4))
    starts = sorted(set(starts)) or [0.0]
    return [(x, starts[i + 1] if i + 1 < len(starts) else beats) for i, x in enumerate(starts)]


def _chop(tick, dur, s, s0, s1, ratio, pitch, vel, **kw) -> dict:
    return {"type": "chop", "tick": int(tick), "dur": int(max(1, dur)), "sample_id": s["id"], "s0": round(float(s0), 4),
            "s1": round(float(min(max(s1, s0 + 1e-3), s["beats"])), 4), "ratio": float(ratio), "pitch": float(pitch),
            "vel": round(float(vel), 3), **kw}


def loop_pattern(s: dict, ratio: float, pitch: float) -> dict:
    L = int(round(s["beats"] * ratio * PPQ))
    reps = max(1, int(np.ceil(TICKS_PER_BAR / L)))
    length = int(np.ceil(L * reps / TICKS_PER_BAR)) * TICKS_PER_BAR
    return {"kind": "sample", "length_ticks": length,
            "events": [_chop(i * L, L, s, 0, s["beats"], ratio, pitch, 0.9) for i in range(reps)]}


def chop_sequence(sl, s, ratio, pitch, genre, intensity, rng, bars=2, anchors_only=False, stutter=False,
                  reverse_p=0.0, octave_p=0.0) -> dict:
    """ChopSequenceGenerator: a 1-bar motif (anchors on beats 1 and 3, sequential slice order with occasional
    jumps), repeated with one or two intentional changes in the following bar(s)."""
    w = CHOP_STEPS[genre]
    steps = [0, 8] if anchors_only else sorted(st for st, p in w.items() if st in (0, 8) or rng.random() < p * (0.35 + intensity))
    n = len(sl)
    seq, idx = [], 0
    for st in steps:
        idx = 0 if st == 0 else ((idx + 1) % n if rng.random() < 0.6 else int(rng.integers(n)))
        seq.append([st, idx])
    bar_seqs = [seq]
    for _ in range(1, bars):
        v = [list(x) for x in seq]
        for j in rng.permutation(len(v))[: 1 + int(intensity > 0.6)]:
            if v[j][0] != 0:
                v[j][1] = int(rng.integers(n))
        bar_seqs.append(v)
    pos = [(b * 16 + st, si) for b, sq in enumerate(bar_seqs) for st, si in sq]
    L = bars * TICKS_PER_BAR
    ev = []
    for j, (p, si) in enumerate(pos):
        t = p * STEP_TICKS
        dur = (pos[j + 1][0] * STEP_TICKS if j + 1 < len(pos) else L) - t
        s0 = sl[si][0]
        kw = {"reverse": True} if reverse_p and p % 16 and rng.random() < reverse_p else {}
        dp = int(rng.choice([12, -12, 7])) if octave_p and p % 16 and rng.random() < octave_p else 0
        ev.append(_chop(t, dur, s, s0, s0 + dur / PPQ / ratio, ratio, pitch + dp,
                        0.92 if p % 8 == 0 else rng.uniform(0.72, 0.88), **kw))
    if stutter and ev:  # 1/32 retriggers over the final beat: a stutter fill into the repeat
        lo = L - PPQ
        last = max((e for e in ev if e["tick"] < lo), key=lambda e: e["tick"])
        ev = [e for e in ev if e["tick"] < lo]
        last["dur"] = min(last["dur"], lo - last["tick"])
        s0 = last["s0"]
        ev += [_chop(lo + i * PPQ // 8, PPQ // 8, s, s0, s0 + 0.125 / ratio, ratio, pitch, 0.55 + 0.05 * i) for i in range(8)]
    return {"kind": "sample", "length_ticks": L, "events": ev}


MELODY_PROGRESSIONS = [[0, 0, 5, 6], [0, 5, 6, 0], [0, 2, 6, 5], [0, 5, 2, 6], [0, 3, 5, 4]]  # scale degrees
MINOR = [0, 2, 3, 5, 7, 8, 10]


def _degree(d: int) -> int:
    semis = MINOR[d % 7] + 12 * (d // 7)
    return semis - 12 if semis > 7 else semis  # keep roots close to the one-shot's own pitch


def oneshot_melody(s: dict, bpm: float, pitch: float, genre: str, intensity: float, rng, progression: list[int],
                   anchors_only: bool = False, octave_p: float = 0.0, reverse_p: float = 0.0) -> dict:
    """A one-shot has no rhythm or harmony of its own, so it is played like a sampler instrument: a 4-bar
    minor-key motif (root on beat 1, chord tones on the genre's chop positions) at the sample's own speed."""
    ratio = bpm / s["source_bpm"]  # speed 1.0: only the pitch changes
    w = CHOP_STEPS[genre]
    ev = []
    L = len(progression) * TICKS_PER_BAR
    motif = [0] if anchors_only else sorted(st for st, p in w.items() if st in (0, 8) or rng.random() < p * (0.35 + intensity))
    for bar, deg in enumerate(progression):
        steps = motif if bar % 2 == 0 or anchors_only else sorted(set(motif) | ({int(rng.choice(list(w)))} if intensity > 0.3 else set()))
        for j, st in enumerate(steps):
            tone = 0 if st == 0 else int(rng.choice([0, 2, 4, 4, 7]))  # root, third, fifth, octave (in degrees)
            semis = _degree(deg) + (MINOR[(deg + tone) % 7] - MINOR[deg % 7]) % 12 if tone not in (0, 7) else _degree(deg) + (12 if tone else 0)
            if octave_p and st and rng.random() < octave_p:
                semis += int(rng.choice([12, -12]))
            t = bar * TICKS_PER_BAR + st * STEP_TICKS
            nxt = bar * TICKS_PER_BAR + (steps[j + 1] * STEP_TICKS if j + 1 < len(steps) else TICKS_PER_BAR)
            kw = {"reverse": True} if reverse_p and st and rng.random() < reverse_p else {}
            ev.append(_chop(t, nxt - t, s, 0, s["beats"], ratio, float(np.clip(pitch + semis, -24, 24)),
                            0.92 if st % 8 == 0 else rng.uniform(0.7, 0.86), **kw))
    return {"kind": "sample", "length_ticks": L, "events": ev}


def _tile(events, length, total):
    return [{**e, "tick": e["tick"] + r} for r in range(0, total, length) for e in events if e["tick"] + r < total]


def pitch_treatments(y: np.ndarray, cands: list[int]) -> list[dict]:
    """Spec 27.3: try several pitch treatments and measure an artifact proxy (spectral-flatness rise =
    smeared/noisy phase-vocoder output) instead of assuming a fixed shift sounds good."""
    ex = y[: 4 * SR]
    flat = lambda z: float(librosa.feature.spectral_flatness(y=z.mean(axis=1)).mean())
    base = flat(ex)
    return sorted(({"pitch": p, "artifact": round(max(0.0, flat(transform.pitch_stretch(ex, p, 1.0)) - base) * 100, 3)}
                   for p in cands), key=lambda d: d["artifact"])


def _compat(a: dict, b: dict) -> float:
    ca, cb = np.asarray(a.get("chroma") or np.ones(12)), np.asarray(b.get("chroma") or np.ones(12))
    return float(ca @ cb / (np.linalg.norm(ca) * np.linalg.norm(cb) + 1e-9))


def choose_samples(samples: list[dict], strategy: str, settings: dict, rng, rank: int = 0) -> tuple[dict, dict | None]:
    if settings["sample_mode"] == "manual" and settings["sample_ids"]:
        by = {s["id"]: s for s in samples}
        chosen = [by[i] for i in settings["sample_ids"] if i in by]
        if not chosen:
            raise ValueError("None of the manually selected samples exist any more.")
        return chosen[0], (chosen[1] if len(chosen) > 1 else None)
    score = lambda s: s.get("score") or 0.0
    loops = sorted([s for s in samples if s["kind"] == "loop" and s["stem"] != "vocals"], key=score, reverse=True)
    loops = loops or sorted(samples, key=score, reverse=True)
    if not loops:
        raise ValueError("No samples available: analyze a source or select a region first.")
    main = loops[({"loop_flip": 0, "hard_chop": 1, "dark": 0, "creative": 2}[strategy] + rank) % len(loops)]
    others = [s for s in samples if s["id"] != main["id"]]
    pool = [s for s in others if s["stem"] == "vocals"] or others
    if not pool:
        return main, None
    # harmonic compatibility first (spec 27.2), then quality
    return main, max(pool, key=lambda s: _compat(main, s) + 0.3 * score(s) + rng.uniform(0, 0.05))


def _roots(arr, pid, nbars, samples, sources, key, key_conf) -> list[int]:
    """Bass root for every half bar, heard from the *rendered* sample pattern (so pitch changes and chop order
    are accounted for). See bass.roots_from_audio."""
    tmp = {**arr, "bars": nbars, "tracks": [{"id": "x", "kind": "sample", "clips": [
        {"id": "c", "pattern_id": pid, "start_bar": 0, "bars": nbars}]}]}
    y = render.render(tmp, samples, sources, master=False)["master"]
    return bass.roots_from_audio(y.mean(axis=1), float(arr["bpm"]), nbars * 2, key, key_conf)


# ---------------------------------------------------------------- variation

def variation(settings: dict, samples: list[dict], sources: dict, analyses: dict, strategy: str, seed: int,
              rank: int = 0) -> dict:
    st = {**DEFAULTS, **settings}
    genre = st["genre"]
    rng = np.random.default_rng(seed)
    main, sec = choose_samples(samples, strategy, st, rng, rank)
    by_id = {s["id"]: s for s in samples}
    an = analyses[main["source_id"]]
    oneshot = main["kind"] == "oneshot"
    treatments = []
    pitch = 0
    if oneshot:  # no tempo of its own: sit in the middle of the genre's range
        bpm, ratio = float(st["bpm"] or round(float(np.mean(FEEL[genre])))), 1.0
        pitch = {"dark": -3, "creative": 3}.get(strategy, 0)
    else:
        bpm, ratio, pitch = fit(main["source_bpm"], genre, strategy, st["bpm"])
    src = sources[main["source_id"]]
    flags = {} if oneshot else {"tape": True}
    if not oneshot and genre in ("trap", "drill") and float(an.get("percussive_ratio") or 0.0) > DRUMMY:
        # A record's own drums under a double-time trap or drill grid fight the new ones, so they are taken out.
        # Boom bap and lo-fi keep them: new drums layered over a break is the sound of those styles.
        flags["harmonic"] = True
    k = parse_key(an.get("key"))
    key = (int((k[0] + pitch) % 12), k[1]) if k else None
    key_conf = float(an.get("key_confidence") or 0.0)
    if oneshot:  # the motif is written in minor around the one-shot's own strongest pitch
        key, key_conf = (int((int(np.argmax(main.get("chroma") or [1])) + pitch) % 12), "minor"), 0.6
    if st["swing"] is not None:
        swing = float(st["swing"])
    else:  # drums swing as much as the record does (never less than the genre's floor), or they flam against it
        lo, hi = drums.GENRES[genre]["swing"]
        swing = float(np.clip(an.get("swing") or lo, lo, max(hi, 66 if genre in ("boombap", "lofi") else hi)))
    template = st["structure"] if st["structure"] != "auto" else {
        "loop_flip": "loop", "hard_chop": "standard", "dark": "standard", "creative": "verse_hook"}[strategy]
    secs = sections(template, int(st["bars"]))
    energy, ci = float(st["energy"]), float(st["chop_intensity"])
    complexity = float(np.clip(st["drum_complexity"] + {"creative": 0.15, "dark": -0.05}.get(strategy, 0) + 0.2 * (energy - 0.5), 0, 1))
    fx_name, fx = SAMPLE_FX[genre]
    fx = dict(fx)
    if strategy == "dark":
        fx["lowpass_hz"] = min(fx.get("lowpass_hz") or 20000, 7000)
    params = {**BASS_808[genre]}
    if strategy == "dark":
        params.update(drive=params["drive"] + 0.1, decay=params["decay"] * 1.15)

    P: dict[str, dict] = {}
    if oneshot:
        prog = MELODY_PROGRESSIONS[(ORDER.index(strategy) + rank + int(rng.integers(2))) % len(MELODY_PROGRESSIONS)]
        creative = strategy == "creative"
        P["s_main"] = oneshot_melody(main, bpm, pitch, genre, ci, rng, prog, octave_p=0.2 if creative else 0.05)
        P["s_var"] = oneshot_melody(main, bpm, pitch, genre, min(1.0, ci + 0.25), rng, prog, octave_p=0.25,
                                    reverse_p=0.2 if creative else 0.0)
        P["s_filt"] = oneshot_melody(main, bpm, pitch, genre, 0, rng, prog, anchors_only=True)
        orders = ("melody", "melody")
    else:
        # the hook states the idea, the verse answers it with a different order of the same phrase
        chops = ["call_response", "back_first", "front_swap", "tail_loop"]
        if strategy == "hard_chop" or ci > 0.75:
            hook = chops[int(rng.integers(3))]
            verse = [c for c in chops + ["stutter"] if c != hook][int(rng.integers(3))]
        else:
            hook = "loop"
            verse = "loop" if ci < 0.2 else ["repeat_half", "front_swap", "tail_loop", "stutter"][int(rng.integers(3 + (ci > 0.6)))]
        P["s_main"] = unit_pattern(main, ratio, pitch, hook, **flags)
        P["s_var"] = unit_pattern(main, ratio, pitch, verse, **flags)
        P["s_filt"] = unit_pattern(main, ratio, pitch, "loop", **flags)  # played through a low-pass in intros and breakdowns
        orders = (hook, verse)
    sec = None  # a second sample layered on top clashes more often than it helps

    dv = drums.generate(genre, complexity, seed, bars=2)
    for name, evs in dv.items():
        for lane in LANES + ["fx"]:
            e = [x for x in evs if drums.TRACK_OF[x["inst"]] == lane]
            if e:
                P[f"d_{name}_{lane}"] = {"kind": "drums", "length_ticks": 2 * TICKS_PER_BAR, "events": e}
    P["fx_crash"] = {"kind": "drums", "length_ticks": TICKS_PER_BAR, "events": [
        {"type": "drum", "tick": 0, "inst": "crash", "vel": 0.7}]}
    P["fx_riser"] = {"kind": "drums", "length_ticks": TICKS_PER_BAR, "events": [
        {"type": "drum", "tick": TICKS_PER_BAR - 2 * PPQ, "inst": "crash", "vel": 0.6, "reverse": True, "dur": 2 * PPQ}]}

    arr = {"schema_version": 1, "bpm": bpm, "time_signature": "4/4", "key": f"{NOTE_NAMES[key[0]]} {key[1]}" if key else None,
           "genre": genre, "kit": drums.GENRES[genre]["kit"], "swing": swing, "bars": int(st["bars"]), "seed": int(seed),
           "strategy": strategy, "sections": secs, "patterns": P, "bass": params, "master": {"target_lufs": -11.0}, "tracks": []}
    for pid, p in P.items():
        p["id"] = pid
    # 808: roots from the rendered sample pattern, locked to the matching kick pattern
    smp = {s["id"]: s for s in [main] + ([sec] if sec else [])}
    for bname, spid, dname in (("b_main", "s_main", "main"), ("b_var", "s_var", "alt")):
        nb = lcm(max(1, P[spid]["length_ticks"] // TICKS_PER_BAR), 2)
        roots = _roots(arr, spid, nb, smp, sources, key, key_conf)
        kicks = _tile([x for x in dv[dname] if x["inst"] == "kick"], 2 * TICKS_PER_BAR, nb * TICKS_PER_BAR)
        notes = bass.generate(roots, kicks, genre, seed, key, float(st["bass_intensity"]))
        P[bname] = {"id": bname, "kind": "bass", "length_ticks": nb * TICKS_PER_BAR, "events": notes,
                    "roots": [NOTE_NAMES[r] for r in roots[::2]], "half_bar_roots": [NOTE_NAMES[r] for r in roots]}

    clips: dict[str, list] = {t[0]: [] for t in TRACKS}
    counter = iter(range(10 ** 6))

    def add(track, pid, start, bars):
        if pid in P and bars > 0:
            clips[track].append({"id": f"clip{next(counter)}", "pattern_id": pid, "start_bar": int(start), "bars": int(bars)})

    def drum_section(variant, s, b, fill=True):
        body, tail = (b - 2, 2) if fill and b >= 4 else (b, 0)
        for lane in LANES:
            add(lane, f"d_{variant}_{lane}", s, body)
            if tail:  # fills only at phrase boundaries
                add(lane, f"d_fill_{lane}", s + body, tail)

    for sc in secs:
        s, b = sc["start_bar"], sc["bars"]
        role = ROLE[sc["name"]]
        if role == "intro":  # the sample alone, muffled, then opening up into the drop
            h = b // 2 if b >= 4 else b
            add("sample2", "s_filt", s, h)
            add("sample", "s_main", s + h, b - h)
            if energy > 0.5 and b >= 4:
                add("hats", "d_sparse_hats", s + h, b - h)
            add("fx", "fx_riser", s + b - 1, 1)
        elif role == "main":
            add("sample", "s_main", s, b)
            drum_section("main", s, b)
            add("bass", "b_main", s, b)
            add("fx", "fx_crash", s, 1)
        elif role == "verse":
            add("sample", "s_var", s, b)
            drum_section("alt", s, b)
            add("bass", "b_var", s, b)
        elif role == "breakdown":  # drums drop out, the muffled sample carries it
            add("sample2", "s_filt", s, b)
            if energy >= 0.5:
                add("hats", "d_sparse_hats", s, b)
            add("fx", "fx_riser", s + b - 1, 1)
        else:  # outro: drums leave, then the sample goes back under the filter
            h = max(1, b // 2)
            add("sample", "s_main", s, h)
            add("sample2", "s_filt", s + h, b - h)
            drum_section("sparse", s, h, fill=False)
            add("fx", "fx_crash", s, 1)
    # quiet sources (and one-shots, which are mostly decay) are lifted so the sample still leads the mix
    lift = float(np.clip(-20.0 - float(main.get("rms_db") or -20.0), 0.0, 12.0)) + (5.0 if oneshot else 0.0)
    for tid, name, kind, color, vol, pan in TRACKS:
        vol = min(6.0, vol + lift) if tid == "sample" else vol
        arr["tracks"].append({"id": tid, "name": name, "kind": kind, "color": color, "volume_db": vol, "pan": pan,
                              "muted": False, "solo": False, "choke": kind == "sample",
                              "duck": SAMPLE_DUCK[genre] if kind == "sample" else 0.0,
                              "fx": ({**fx, "lowpass_hz": 700, "highpass_hz": 80} if tid == "sample2" else dict(fx)) if kind == "sample"
                              else ({"highpass_hz": 250, "reverb": 0.2} if tid == "fx" else {}),
                              "clips": clips[tid]})
    names = {s["id"]: s["name"] for s in [main] + ([sec] if sec else [])}
    arr["meta"] = {
        "description": f"{STRATEGIES[strategy].split(':')[0]} of {main['name']} · {bpm:g} BPM · {arr['key'] or 'key ?'}"
                       f" · pitch {pitch:+d} st · {len(secs)} sections ({template})",
        "strategy_detail": STRATEGIES[strategy], "sample_preset": fx_name, "main_sample": main["id"],
        "secondary_sample": sec["id"] if sec else None, "sample_names": names, "ratio": ratio, "pitch": pitch,
        "pitch_treatments": treatments, "template": template, "orders": list(orders),
        "stretch": 0.0 if oneshot else round(bpm / (main["source_bpm"] * 2 ** (pitch / 12) * ratio) - 1, 4),
        "own_drums_removed": bool(flags.get("harmonic")), "drum_complexity": round(complexity, 3),
        "conservative_bass": key_conf < 0.35, "rank": rank,
    }
    arr["meta"]["signature"] = signature(arr)
    return arr


def signature(arr: dict) -> str:
    """What makes two variations audibly different: samples, pitch, chop order, kick pattern, 808 line."""
    P = arr["patterns"]
    sig = [arr["meta"]["main_sample"], arr["meta"]["pitch"], arr["strategy"],
           [(e["tick"], e["s0"], e["pitch"]) for e in P["s_main"]["events"]],
           [e["tick"] for e in P.get("d_main_kick", {"events": []})["events"]],
           [(e["tick"], e["midi"]) for e in P["b_main"]["events"]]]
    return hashlib.sha1(json.dumps(sig).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- evaluation

def evaluate(arr: dict, samples: dict, sources: dict, res: dict | None = None) -> dict:
    """BeatEvaluator: technical checks are pass/fail; musical descriptors are reported, not graded."""
    checks = []

    def check(name, ok, detail="", kind="technical"):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "kind": kind})

    P = arr["patterns"]
    used = [(t, P[c["pattern_id"]]) for t in arr["tracks"] for c in t["clips"]]
    sample_ids = {e["sample_id"] for _, p in used for e in p["events"] if e["type"] == "chop"}
    missing = [i for i in sample_ids if i not in samples or samples[i]["source_id"] not in sources]
    check("assets_exist", not missing, f"missing: {missing}" if missing else f"{len(sample_ids)} samples")
    bad = [p["id"] for _, p in used if any(not 0 <= e["tick"] < p["length_ticks"] for e in p["events"])]
    bad += [e["sample_id"] for _, p in used for e in p["events"]
            if e["type"] == "chop" and e["sample_id"] in samples and e["s1"] > samples[e["sample_id"]]["beats"] + 1e-3]
    check("events_in_bounds", not bad, ", ".join(map(str, bad)))
    check("has_sample_events", any(e["type"] == "chop" for _, p in used for e in p["events"]))
    check("has_drums", any(t["kind"] == "drums" and p["events"] for t, p in used))
    notes = [e for _, p in used for e in p["events"] if e["type"] == "note"]
    check("bass_valid", bool(notes) and all(12 <= n["midi"] <= 72 and n["dur"] > 0 for n in notes), f"{len(notes)} notes")
    check("synchronized", all(p["length_ticks"] % STEP_TICKS == 0 for _, p in used), "pattern lengths on the 16th grid")
    if arr["bars"] >= 12:
        sp = {c["pattern_id"] for t in arr["tracks"] if t["kind"] == "sample" for c in t["clips"]}
        dp = {c["pattern_id"] for t in arr["tracks"] if t["id"] == "kick" for c in t["clips"]}
        check("meaningful_variation", len(sp) >= 2 and len(dp) >= 2, f"{len(sp)} sample / {len(dp)} kick patterns")
    musical = {}
    if res is not None:
        y = res["master"]
        m = res["metrics"]
        check("not_silent", float(np.abs(y).max()) > 1e-3, f"peak {m['peak_dbfs']} dBFS")
        check("not_clipped", m["clipped_samples"] == 0 and m["true_peak_dbfs"] <= -0.9, f"true peak {m['true_peak_dbfs']} dBTP")
        spb = TICKS_PER_BAR / PPQ * 60 / arr["bpm"]
        quiet = []
        for sc in arr["sections"]:
            seg = y[int(sc["start_bar"] * spb * SR): int((sc["start_bar"] + sc["bars"]) * spb * SR)]
            r = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9) if len(seg) else -200
            if r < -45:
                quiet.append(sc["name"])
        check("no_silent_sections", not quiet, ", ".join(quiet))
        bars = [int(round(i * spb * SR)) for i in range(1, arr["bars"])]
        tonal = [v for k, v in res["stems"].items() if next(t["kind"] for t in arr["tracks"] if t["id"] == k) != "drums"]
        # drum transients are intentional steps; clicks only matter on sustained sample/808 material
        jumps = [float(np.abs(v[b] - v[b - 1]).max()) for v in tonal for b in bars if b < len(v)]
        check("smooth_boundaries", max(jumps, default=0) < 0.1, f"max sample/808 bar-line step {max(jumps, default=0):.4f}")
        onset = librosa.onset.onset_strength(y=librosa.resample(y.mean(axis=1), orig_sr=SR, target_sr=22050), sr=22050)
        musical = {"lufs": m["lufs"], "onset_density": round(float((onset > onset.mean() + onset.std()).mean() * 43), 2),
                   "stem_rms_db": {k: round(float(20 * np.log10(np.sqrt(np.mean(v ** 2)) + 1e-9)), 1) for k, v in res["stems"].items()}}
    return {"passed": all(c["ok"] for c in checks), "checks": checks, "musical": musical}


def fingerprint(y: np.ndarray) -> np.ndarray:
    """Coarse log-mel/time fingerprint (24 x frames) used to measure how different two renders really are."""
    m = librosa.feature.melspectrogram(y=librosa.resample(y[: 60 * SR].mean(axis=1), orig_sr=SR, target_sr=11025),
                                       sr=11025, n_mels=24, hop_length=1024)
    return np.log1p(m)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    n = min(a.shape[1], b.shape[1])
    x, z = a[:, :n].ravel() - a[:, :n].mean(), b[:, :n].ravel() - b[:, :n].mean()
    return float(x @ z / (np.linalg.norm(x) * np.linalg.norm(z) + 1e-9))


def generate(settings: dict, samples: list[dict], sources: dict, analyses: dict, progress=lambda s, f=None: None,
             max_retries: int = 2, too_similar: float = 0.97) -> list[dict]:
    """N variations, each evaluated; failing or near-duplicate ones are regenerated with a new seed (spec 26)."""
    st = {**DEFAULTS, **settings}
    n = int(st["variations"])
    base = int(st["seed"]) if st["seed"] is not None else int(np.random.default_rng().integers(1, 10 ** 8))
    by = {s["id"]: s for s in samples}
    out, sigs, fps = [], set(), []
    for i in range(n):
        strat = ORDER[i % len(ORDER)]
        best = None
        for attempt in range(max_retries + 1):
            progress(f"variation {i + 1}/{n}" + (f" (retry {attempt})" if attempt else ""), (i + attempt / (max_retries + 1)) / n)
            seed = base + 1000 * i + attempt
            arr = variation(st, samples, sources, analyses, strat, seed, rank=i // len(ORDER) + attempt)
            res = render.render(arr, by, sources)
            ev = evaluate(arr, by, sources, res)
            fp = fingerprint(res["master"])
            sim = max((similarity(fp, q) for q in fps), default=0.0)
            dup = arr["meta"]["signature"] in sigs or sim > too_similar
            ev["checks"].append({"name": "distinct_from_others", "ok": not dup, "kind": "technical",
                                 "detail": f"max spectral similarity {sim:.3f}"})
            ev["passed"] = ev["passed"] and not dup
            ev["attempts"] = attempt + 1
            # keep only what gets stored: per-track stems of every variation would add up to gigabytes
            res = {"master": res["master"], "metrics": res["metrics"]}
            cand = {"arrangement": arr, "evaluation": ev, "render": res, "fingerprint": fp}
            if best is None or (ev["passed"] and not best["evaluation"]["passed"]):
                best = cand
            if ev["passed"]:
                break
        transform.release_memory()  # stretched-sample buffers of this variation are on disk if needed again
        sigs.add(best["arrangement"]["meta"]["signature"])
        fps.append(best.pop("fingerprint"))
        out.append(best)
    progress("done", 1.0)
    return out
