"""Sample discovery: extract loop and chop candidates from the mix and separated stems, score them with
explainable weighted heuristics, then pick a diverse set with maximal-marginal-relevance (MMR)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import audio_io, store, transform
from .core import NOTE_NAMES, SR

WEIGHTS = {"harmonic": 0.20, "phrase": 0.20, "loop": 0.20, "signal": 0.15, "distinct": 0.15, "clean": 0.10}
LABELS = {"harmonic": "stable harmony", "phrase": "coherent phrase/rhythm", "loop": "loops seamlessly",
          "signal": "healthy level", "distinct": "recurring motif", "clean": "little drum/vocal bleed"}


def stem_sets(source: dict) -> dict[str, list[str]]:
    """Named audio sources a sample can come from (stem names, resolved to files at render time)."""
    stems = source.get("stems") or {}
    sets = {"mix": ["mix"]}
    melodic = [s for s in stems if s not in ("drums", "vocals", "percussive")]
    if melodic and len(melodic) < len(stems):
        sets["melodic"] = melodic  # e.g. other+bass, or harmonic
    if "vocals" in stems:
        sets["vocals"] = ["vocals"]
    return sets


def resolve(source: dict, stems: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Stem names -> (file paths, content identities) for the transform cache."""
    files = [source["internal_audio_path"] if s == "mix" else source["stems"][s] for s in stems]
    for f in files:
        if not Path(f).exists():
            raise FileNotFoundError(f"Missing audio for sample source: {f}")
    return tuple(files), tuple(f"{source['content_hash']}:{s}" for s in stems)


def _beat_rms(paths: tuple[str, ...], beat_times: np.ndarray) -> np.ndarray:
    y = transform.source_audio(paths).mean(axis=1)
    idx = np.clip((beat_times * SR).astype(int), 0, len(y) - 1)
    sq = np.add.reduceat(y.astype(np.float64) ** 2, idx) / np.maximum(1, np.diff(np.append(idx, len(y))))
    return np.sqrt(sq)


def _peaky(chroma: np.ndarray) -> np.ndarray:
    p = chroma / (chroma.sum(axis=0, keepdims=True) + 1e-9)
    return 1 - (-(p * np.log(p + 1e-9)).sum(axis=0) / np.log(12))  # 1 = one pitch class, 0 = noise


def _vec(f: dict, a: int, b: int) -> np.ndarray:
    v = np.concatenate([f["chroma"][:, a:b].mean(axis=1), f["mfcc"][1:, a:b].mean(axis=1) / 50])
    return v / (np.linalg.norm(v) + 1e-9)


def _candidate_metrics(f, a, b, rms_src, rms_mix, rms_bleed, peaky, rep_vecs, onsets_t, bt):
    n = len(bt)
    seg = slice(a, b)
    harmonic = float(np.clip(peaky[seg].mean() * 2.2, 0, 1))
    oo = f["onset"][seg]
    half = (b - a) // 2
    if half >= 2:  # phrase coherence: the rhythm of the first half recurs in the second half
        x, y = oo[:half], oo[half:2 * half]
        phrase = float(np.clip(0.5 + 0.5 * np.corrcoef(x, y)[0, 1], 0, 1)) if x.std() > 0 and y.std() > 0 else 0.5
    else:
        phrase = 0.55
    if b < n:  # restart continuity: does the beat after the loop look like the loop's first beat?
        loop = float(np.clip(_vec(f, a, a + 1) @ _vec(f, b, b + 1), 0, 1))
        loop = 0.7 * loop + 0.3 * float(1 - min(1, abs(rms_src[b - 1] - rms_src[a]) / (rms_src[a] + 1e-9)))
    else:
        loop = 0.4
    lvl = rms_src[seg].mean() / (np.median(rms_src) + 1e-9)
    signal = float(np.clip(min(lvl, 1 / max(lvl, 1e-9)) * 1.3, 0, 1))
    v = _vec(f, a, b)
    sims = rep_vecs @ v
    distinct = float(np.clip((np.sum(sims > 0.95) - 1) / 4, 0, 1))  # the motif returns elsewhere in the song
    bleed = rms_bleed[seg].mean() / (rms_mix[seg].mean() + 1e-9)
    clean = float(np.clip(1 - bleed * 1.5, 0, 1))
    return {"harmonic": harmonic, "phrase": phrase, "loop": loop, "signal": signal, "distinct": distinct,
            "clean": clean}, v


def _explain(m: dict, kind: str = "loop") -> list[str]:
    labels = {**LABELS, "loop": "clean attack", "phrase": "clear pitch"} if kind == "chop" else LABELS
    best = sorted(m, key=lambda k: -m[k] * WEIGHTS[k])[:3]
    out = [labels[k] for k in best if m[k] >= 0.5]
    out += [f"weak: {labels[k]}" for k in m if m[k] < 0.25][:2]
    return out


def _mmr(cands: list[dict], k: int, diversity: float) -> list[dict]:
    chosen: list[dict] = []
    pool = sorted(cands, key=lambda c: -c["score"])[: k * 20]
    while pool and len(chosen) < k:
        def mmr(c):
            if not chosen:
                return c["score"]
            sim = max(float(c["_v"] @ s["_v"]) * (0.6 + 0.4 * (c["kind"] == s["kind"]))
                      * (1.0 if c["stem"] == s["stem"] else 0.8)
                      * (1.0 if abs(c["start"] - s["start"]) < 1.0 else 0.85) for s in chosen)
            return (1 - diversity) * c["score"] - diversity * sim
        best = max(pool, key=mmr)
        chosen.append(best)
        pool.remove(best)
    return chosen


def discover(source: dict, analysis: dict, f: dict, count: int = 16, diversity: float = 0.5,
             weights: dict | None = None) -> list[dict]:
    w = {**WEIGHTS, **(weights or {})}
    bt = np.asarray(f["beat_times"])
    n = len(bt)
    phase = analysis["downbeat_phase"]
    onsets_t = np.asarray(analysis["onsets"])
    peaky = _peaky(f["chroma"])
    rep_vecs = np.stack([_vec(f, i, min(n, i + 4)) for i in range(phase, max(phase + 1, n - 3), 4)])
    sets = stem_sets(source)
    rms = {name: _beat_rms(resolve(source, st)[0], bt) for name, st in sets.items()}
    stems = source.get("stems") or {}
    drums = rms_perc = f["rms_perc"]
    if "drums" in stems:
        drums = _beat_rms(resolve(source, ["drums"])[0], bt)
    vocals = _beat_rms(resolve(source, ["vocals"])[0], bt) if "vocals" in stems else np.zeros(n)
    duration = analysis["duration"]
    period = 60 / analysis["bpm"]
    cands = []

    def add(kind, stem, a, b, start, end, bars, beats):
        bleed = vocals + drums if stem == "melodic" else (drums if stem == "vocals" else rms_perc + vocals * 0.5)
        m, v = _candidate_metrics(f, a, b, rms[stem], rms["mix"], bleed, peaky, rep_vecs, onsets_t, bt)
        if kind == "chop":  # chops care about a clean attack, not loop restarts
            on = f["onset"][a] / (np.median(f["onset"]) + 1e-9)
            m["loop"], m["phrase"] = float(np.clip(on / 3, 0, 1)), float(np.clip(peaky[a] * 2, 0, 1))
        if stem == "vocals":
            m["harmonic"] = max(m["harmonic"], 0.5)
        score = sum(w[k] * m[k] for k in w) / sum(w.values())
        if bars in (2, 4):
            score += 0.04  # producers' favourite loop lengths
        cands.append({"kind": kind, "stem": stem, "stems": sets[stem], "start": round(float(start), 4),
                      "end": round(float(min(end, duration)), 4), "bars": bars, "beats": beats,
                      "metrics": {k: round(x, 3) for k, x in m.items()}, "score": round(float(score), 4), "_v": v})

    for stem in sets:
        for bars in (1, 2, 4, 8):
            for a in range(phase, n - 4 * bars + 1, 4 if bars < 4 else 2 * 4):
                b = a + 4 * bars
                start = bt[a]
                # pickups: allow an onset just before the bar line to lead in
                pre = onsets_t[(onsets_t < start) & (onsets_t > start - 0.06)]
                start = float(pre[0]) if len(pre) else start
                end = bt[b] if b < n else start + bars * 4 * period
                add("loop", stem, a, b, start, end, bars, 4 * bars)
        # chops: onset-aligned (note/transient) and beat-aligned half/one/two-beat slices
        src = rms[stem]
        for i in range(n - 2):
            if src[i] < 0.15 * np.median(src):
                continue  # silence-based boundary: skip empty beats
            for beats in (0.5, 1, 2):
                add("chop", stem, i, min(n, i + max(1, int(beats))), bt[i], bt[i] + beats * period, 0, beats)
        for t in onsets_t:
            i = int(np.searchsorted(bt, t, side="right") - 1)
            if 0 <= i < n - 1:
                nxt = onsets_t[onsets_t > t + 0.08]
                end = min(float(nxt[0]) if len(nxt) else t + period, t + 2 * period)
                add("chop", stem, i, i + 1, t, end, 0, round((end - t) / period, 3))

    loops = _mmr([c for c in cands if c["kind"] == "loop"], max(4, count * 2 // 3), diversity)
    chops = _mmr([c for c in cands if c["kind"] == "chop"], max(4, count - len(loops)), diversity)
    out = []
    for c in loops + chops:
        paths, _ = resolve(source, c["stems"])
        y = transform.region(paths, c["start"], c["end"])
        ch = f["chroma"][:, int(np.searchsorted(bt, c["start"])):int(np.searchsorted(bt, c["end"])) + 1]
        pc = int(np.argmax(ch.sum(axis=1))) if ch.size else None
        c.pop("_v")
        out.append({**c, "id": store.new_id("smp"), "source_id": source["id"], "origin": "auto",
                    "duration": round(c["end"] - c["start"], 4), "source_bpm": analysis["bpm"],
                    "key": analysis["key"], "dominant_pitch": NOTE_NAMES[pc] if pc is not None else None,
                    "rms_db": round(20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9), 1),
                    "explanation": _explain(c["metrics"], c["kind"]), "tags": _tags(c),
                    "peaks": audio_io.peaks(y, 120), "name": _name(c)})
    return sorted(out, key=lambda s: -s["score"])


def _tags(c):
    t = [c["kind"], c["stem"]]
    if c["kind"] == "loop":
        t.append(f"{c['bars']}-bar")
    if c["metrics"]["clean"] > 0.7:
        t.append("clean")
    if c["metrics"]["distinct"] > 0.5:
        t.append("motif")
    return t


def _name(c):
    m, s = divmod(c["start"], 60)
    what = f"{c['bars']}-bar loop" if c["kind"] == "loop" else f"{c['beats']}-beat chop"
    return f"{what} @ {int(m)}:{s:04.1f} ({c['stem']})"


def manual(source: dict, analysis: dict, start: float, end: float, stems: list[str], name: str | None = None) -> dict:
    """A user-highlighted region becomes a sample (validated against the real audio length)."""
    if not 0 <= start < end <= analysis["duration"] + 1e-3:
        raise ValueError("Sample start/end must lie inside the song and start < end.")
    paths, _ = resolve(source, stems)
    y = transform.region(paths, start, end)
    period = 60 / analysis["bpm"]
    beats = round((end - start) / period, 3)
    return {"id": store.new_id("smp"), "source_id": source["id"], "origin": "manual", "kind": "loop" if beats >= 4 else "chop",
            "stem": "+".join(stems), "stems": stems, "start": round(start, 4), "end": round(end, 4),
            "duration": round(end - start, 4), "bars": int(beats // 4) if beats >= 4 else 0, "beats": beats,
            "source_bpm": analysis["bpm"], "key": analysis["key"], "score": None, "metrics": {}, "explanation": ["manual selection"],
            "tags": ["manual"], "peaks": audio_io.peaks(y, 120), "name": name or f"Manual {start:.2f}-{end:.2f}s",
            "rms_db": round(20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9), 1)}
