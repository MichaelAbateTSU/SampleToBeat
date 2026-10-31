"""Authoritative offline renderer (spec 17). Arrangement -> per-track buffers -> mix -> master -> files.
The UI plays these renders, so preview and export come from the same code path and cannot disagree."""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import numpy as np
import pyloudnorm
from scipy.ndimage import minimum_filter1d, uniform_filter1d
from scipy.signal import resample_poly

from . import audio_io, bass, drums, sampling, transform
from .core import PPQ, SR, TICKS_PER_BAR, db_to_gain, swing_offset_ticks, ticks_to_samples

TAIL_S = 2.0  # reverb/808 ring-out after the last bar (full renders); wrapped to the start for loops
CEILING_DB = -1.0  # true-peak ceiling


class MissingAsset(ValueError):
    pass


def timeline(arr: dict, track: dict, a: int = 0, b: int | None = None) -> list[tuple[int, dict, int]]:
    """(absolute tick, event, clip end tick) for every event; patterns tile across each clip."""
    b = arr["bars"] * TICKS_PER_BAR if b is None else b
    out = []
    for c in track["clips"]:
        p = arr["patterns"][c["pattern_id"]]
        c0 = int(round(c["start_bar"] * TICKS_PER_BAR))
        c1 = c0 + int(round(c["bars"] * TICKS_PER_BAR))
        for r in range(c0, c1, p["length_ticks"]):
            for e in p["events"]:
                t = r + e["tick"]
                if t < c1 and a <= t < b:
                    out.append((t, e, c1))
    return sorted(out, key=lambda x: (x[0], x[1].get("inst", "")))


def _place(out: np.ndarray, y: np.ndarray, pos: int) -> None:
    if pos < 0:
        y, pos = y[-pos:], 0
    n = min(len(y), len(out) - pos)
    if n > 0:
        out[pos:pos + n] += y[:n]


def sample_buffer(s: dict, src: dict, bpm: float, ratio: float, pitch: float) -> tuple[np.ndarray, float]:
    """The sample region stretched to the project tempo (ratio = project beats per source beat) and pitched."""
    paths, hashes = sampling.resolve(src, s["stems"])
    t = {**transform.DEFAULT_TRANSFORM, **(s.get("transform") or {}), "fade_in_ms": 0.0, "fade_out_ms": 0.0}
    t["pitch_semitones"] = float(t["pitch_semitones"]) + float(pitch)
    speed = bpm / (s["source_bpm"] * ratio)
    # grid-locked: a tape-mode pitch already speeds the audio up, so stretch only the remainder
    return transform.cached_transform(paths, hashes, s["start"], s["end"], t, speed / transform.tape_rate(t)), speed


def _sample_track(tl, samples, sources, bpm, n, swing, a, choke=True) -> np.ndarray:
    out = np.zeros((n, 2), np.float32)
    spb = 60.0 / bpm
    starts = [ticks_to_samples(t + swing_offset_ticks(t, swing) - a, bpm) for t, _, _ in tl]
    for i, (t, e, c1) in enumerate(tl):
        s = samples.get(e["sample_id"])
        if s is None or s["source_id"] not in sources:
            raise MissingAsset(f"Sample {e['sample_id']} referenced by the arrangement no longer exists.")
        buf, speed = sample_buffer(s, sources[s["source_id"]], bpm, e.get("ratio", 1.0), e.get("pitch", 0.0))
        tf = {**transform.DEFAULT_TRANSFORM, **(s.get("transform") or {})}
        base = int(round(s.get("pickup", 0.0) / speed * SR))  # buffer index of source beat 0
        off = base + int(round(e["s0"] * e.get("ratio", 1.0) * spb * SR))
        pre = base if e["s0"] < 1e-6 else 0  # chops from beat 1 keep the lead-in, placed just before the tick
        length = min(ticks_to_samples(min(e["dur"], c1 - t), bpm),
                     int(round((e["s1"] - e["s0"]) * e.get("ratio", 1.0) * spb * SR)))
        if choke and i + 1 < len(tl):  # mono choke: the next chop cuts this one
            length = min(length, starts[i + 1] - starts[i])
        seg = buf[off - pre: off + max(0, length)]
        if len(seg) < 16:
            continue
        if e.get("reverse"):
            seg = seg[::-1]
        seg = transform.fade(seg, tf["fade_in_ms"], tf["fade_out_ms"]) * np.float32(e.get("vel", 1.0) * db_to_gain(e.get("gain_db", 0.0)))
        if e.get("pan"):
            seg = transform.pan(seg, e["pan"])
        _place(out, seg, starts[i] - pre)
    return out


def _drum_track(tl, kit, bpm, n, swing, a) -> np.ndarray:
    out = np.zeros((n, 2), np.float32)
    starts = [ticks_to_samples(t + swing_offset_ticks(t, swing) - a, bpm) for t, _, _ in tl]
    for i, (t, e, _) in enumerate(tl):
        y = drums.sound(kit, e["inst"]) * np.float32(e.get("vel", 1.0))
        pos = starts[i]
        if e.get("reverse"):  # reversed hit swells INTO tick+dur (riser into the next downbeat)
            d = ticks_to_samples(e.get("dur", PPQ), bpm)
            y = transform.fade(y[::-1][-d:], 20, 2)
            pos += d - len(y)
        elif e.get("dur"):
            y = transform.fade(y[: ticks_to_samples(e["dur"], bpm)], 0, 5)
        group = drums.CHOKE.get(e["inst"])
        if group:  # closed hat chokes the open hat
            nxt = next((starts[j] for j in range(i + 1, len(tl)) if drums.CHOKE.get(tl[j][1]["inst"]) == group), None)
            if nxt is not None and nxt - pos < len(y):
                y = transform.fade(y[: max(0, nxt - pos)], 0, 5)
        _place(out, y, pos)
    return out


def _bass_track(tl, params, bpm, n, swing, a, kick_ticks) -> np.ndarray:
    notes = [{**e, "tick": t + swing_offset_ticks(t, swing) - a, "dur": min(e["dur"], c1 - t)} for t, e, c1 in tl]
    y = bass.render(notes, bpm, n, params)
    y = bass.duck(y, kick_ticks, bpm, amount=params.get("duck", 0.45))
    return np.stack([y, y], axis=1)


def render(arr: dict, samples: dict, sources: dict, scope: str = "full", loop_start_bar: int = 0,
           loop_bars: int = 4, master: bool = True) -> dict:
    """Returns {"master": (n,2), "stems": {track_id: (n,2)}, "metrics": {...}} - stems share gain and length."""
    bpm, swing = float(arr["bpm"]), float(arr.get("swing", 50))
    if scope == "loop":
        a = int(loop_start_bar) * TICKS_PER_BAR
        b = a + int(loop_bars) * TICKS_PER_BAR
        if not (0 <= a < b <= arr["bars"] * TICKS_PER_BAR):
            raise ValueError("Loop range lies outside the arrangement.")
    else:
        a, b = 0, arr["bars"] * TICKS_PER_BAR
    body = ticks_to_samples(b - a, bpm)
    tail = int(TAIL_S * SR)
    n = body + tail
    tracks = arr["tracks"]
    solo = any(t.get("solo") for t in tracks)
    kick_tr = next((t for t in tracks if t["id"] == "kick"), None)
    kick_ticks = [t + swing_offset_ticks(t, swing) - a for t, e, _ in timeline(arr, kick_tr, a, b)] if kick_tr else []
    bufs: dict[str, np.ndarray] = {}
    for tr in tracks:
        if tr.get("muted") or (solo and not tr.get("solo")):
            continue
        tl = timeline(arr, tr, a, b)
        if not tl:  # empty lanes produce no (silent) stem
            continue
        if tr["kind"] == "sample":
            y = _sample_track(tl, samples, sources, bpm, n, swing, a, tr.get("choke", True))
        elif tr["kind"] == "drums":
            y = _drum_track(tl, arr.get("kit", "boombap"), bpm, n, swing, a)
        else:
            y = _bass_track(tl, {**bass.DEFAULT_808, **arr.get("bass", {})}, bpm, n, swing, a, kick_ticks)
        if tr.get("fx"):
            y = transform.effects(y, tr["fx"], bpm, seed=int(arr.get("seed", 0)))
        y = transform.pan(y * np.float32(db_to_gain(tr.get("volume_db", 0.0))), tr.get("pan", 0.0))
        if scope == "loop":  # seamless loop: ring-out wraps onto the start
            y = y.copy()
            y[:tail] += y[body:body + tail]
            y = y[:body]
        bufs[tr["id"]] = np.nan_to_num(y.astype(np.float32))
    length = body if scope == "loop" else n
    mix = np.zeros((length, 2), np.float32)
    for buf in bufs.values():
        mix += buf
    if not master:
        return {"master": mix, "stems": bufs, "metrics": {}}
    return _master(mix, bufs, arr.get("master", {}).get("target_lufs", -14.0))


def true_peak(y: np.ndarray) -> float:
    """4x-oversampled peak estimate (ITU-style approximation)."""
    peak = 0.0
    block, halo = SR, 32  # default polyphase FIR is shorter than this overlap
    for start in range(0, len(y), block):
        end = min(start + block, len(y))
        lo, hi = max(0, start - halo), min(len(y), end + halo)
        up = resample_poly(y[lo:hi], 4, 1, axis=0)
        peak = max(peak, float(np.abs(up[(start - lo) * 4:(end - lo) * 4]).max()))
    return peak


def loudness(y: np.ndarray) -> float | None:
    if len(y) <= SR // 2:
        return None
    meter = pyloudnorm.Meter(SR)
    step = 1.0 - meter.overlap
    count = int(np.round((len(y) / SR - meter.block_size) / (meter.block_size * step))) + 1
    energy = np.zeros((y.shape[1], count), np.float64)
    # BS.1770 weighting/gating is channel-independent up to the final channel sum.
    # Filter one channel at a time rather than copying/filtering full stereo buffers.
    for channel in range(y.shape[1]):
        weighted = y[:, channel].astype(np.float64)
        for stage in meter._filters.values():
            weighted = stage.apply_filter(weighted)
        for block in range(count):
            lo = int(meter.block_size * block * step * SR)
            hi = int(meter.block_size * (block * step + 1) * SR)
            energy[channel, block] = np.sum(np.square(weighted[lo:hi])) / (meter.block_size * SR)
        del weighted
    gains = np.asarray([1.0, 1.0, 1.0, 1.41, 1.41])[:y.shape[1]]
    with np.errstate(divide="ignore", invalid="ignore"):
        levels = -0.691 + 10 * np.log10(gains @ energy)
        absolute = levels >= -70
        if not absolute.any():
            return None
        relative = -0.691 + 10 * np.log10(gains @ energy[:, absolute].mean(axis=1)) - 10
        gated = (levels > relative) & (levels > -70)
        if not gated.any():
            return None
        v = -0.691 + 10 * np.log10(gains @ energy[:, gated].mean(axis=1))
    return None if v is None or not np.isfinite(v) or v < -70 else float(v)


def limit(y: np.ndarray, ceiling: float, window_ms: float = 10.0) -> np.ndarray:
    """Look-ahead brickwall limiter. The gain is a moving average of a running minimum twice as wide,
    so at every sample it is <= the gain that sample needs (no overs) yet ramps smoothly."""
    w = max(1, int(window_ms * SR / 1000))
    need = np.minimum(1.0, ceiling / np.maximum(np.abs(y).max(axis=1), 1e-9))
    g = uniform_filter1d(minimum_filter1d(need, 2 * w + 1), w + 1 - (w % 2))  # odd size keeps the window centred
    return (y * g[:, None]).astype(np.float32)


def _master(mix: np.ndarray, bufs: dict, target_lufs: float) -> dict:
    lufs_in = loudness(mix)
    # conservative gain toward the target; never boost near-silence by more than 12 dB
    g_db = float(np.clip(target_lufs - lufs_in, -24, 12)) if lufs_in is not None else 0.0
    pre = mix * np.float32(db_to_gain(g_db))
    clipped_pre = int((np.abs(pre) >= 1.0).sum())
    out = limit(pre, db_to_gain(CEILING_DB))
    del pre
    tp = true_peak(out)
    if tp > db_to_gain(CEILING_DB):
        out *= np.float32(db_to_gain(CEILING_DB) / tp)
        tp = true_peak(out)
    stems = bufs
    for v in stems.values():
        v *= np.float32(db_to_gain(g_db))
    sp = max((float(np.abs(v).max()) for v in stems.values()), default=0.0)
    stem_trim = min(1.0, 0.98 / sp) if sp > 0 else 1.0  # stems are unlimited: one common trim keeps balance
    for v in stems.values():
        v *= np.float32(stem_trim)
    peak = float(np.abs(out).max()) if len(out) else 0.0
    lufs_out = loudness(out)
    metrics = {"duration_s": round(len(out) / SR, 3), "lufs_in": None if lufs_in is None else round(lufs_in, 2),
               "lufs": None if lufs_out is None else round(lufs_out, 2), "master_gain_db": round(g_db, 2),
               "peak_dbfs": round(20 * np.log10(max(peak, 1e-9)), 2), "true_peak_dbfs": round(20 * np.log10(max(tp, 1e-9)), 2),
               "clipped_samples": int((np.abs(out) >= 1.0).sum()), "limited_samples_pre": clipped_pre,
               "stem_trim_db": round(20 * np.log10(stem_trim), 2), "rms_dbfs": round(20 * np.log10(np.sqrt(np.mean(out ** 2)) + 1e-9), 2)}
    metrics = {k: v.item() if hasattr(v, "item") else v for k, v in metrics.items()}  # plain JSON numbers for the API
    return {"master": out, "stems": stems, "metrics": metrics}


# ---------------------------------------------------------------- files

FORMATS = {"wav16": ("wav", "PCM_16"), "wav24": ("wav", "PCM_24"), "wav32f": ("wav", "FLOAT"), "mp3": ("mp3", None)}


def safe_name(name: str) -> str:
    """User-supplied names never reach a shell, but keep them filesystem-safe and short."""
    s = re.sub(r"[^\w\- ]+", "_", name or "").strip(" ._")[:80]
    if s.upper() in _RESERVED:  # Windows device names can't be file names, even with an extension
        s = f"{s}_"
    return s or "beat"


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def write_audio(path: Path, y: np.ndarray, fmt: str) -> Path:
    ext, subtype = FORMATS[fmt]
    if ext == "mp3":
        return audio_io.write_mp3(path, y)
    if subtype == "PCM_16":  # TPDF dither before truncating to 16 bits
        rng = np.random.default_rng(0)
        y = y + ((rng.random(y.shape) - rng.random(y.shape)) / 32768.0).astype(np.float32)
    return audio_io.write_wav(path, np.clip(y, -1, 1) if subtype != "FLOAT" else y, subtype=subtype)


def write_preview(path: Path, y: np.ndarray) -> Path:
    # WAV, not MP3: MP3 encoder delay would offset audio against the UI playhead
    return audio_io.write_wav(path.with_suffix(".wav"), np.clip(y, -1, 1), subtype="PCM_16")


def verify(path: Path) -> dict:
    """Read the written file back (spec 36): duration, peak, silence."""
    y = audio_io.decode(path)
    peak = float(np.abs(y).max())
    return {"file": path.name, "duration_s": round(len(y) / SR, 3), "peak_dbfs": round(20 * np.log10(max(peak, 1e-9)), 2),
            "silent": bool(np.sqrt(np.mean(y ** 2)) < 1e-4), "channels": int(y.shape[1]), "bytes": path.stat().st_size}


def export(arr: dict, samples: dict, sources: dict, out_dir: Path, filename: str = "beat", fmt: str = "wav24",
           scope: str = "full", loop_start_bar: int = 0, loop_bars: int = 4, stems: bool = False,
           project_json: dict | None = None, progress=lambda s, f=None: None) -> dict:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format {fmt}")
    name = safe_name(filename)
    progress("rendering", 0.1)
    res = render(arr, samples, sources, scope, loop_start_bar, loop_bars)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    progress("writing", 0.6)
    main = write_audio(out_dir / f"{name}.{FORMATS[fmt][0]}", res["master"], fmt)
    files.append({"kind": "mix", "name": main.name, "path": str(main), "check": verify(main)})
    if stems:
        progress("writing stems", 0.75)
        stem_fmt = fmt if fmt != "mp3" else "wav24"  # stems are always WAV
        names = {t["id"]: safe_name(t["name"]) for t in arr["tracks"]}
        zpath = out_dir / f"{name}_stems.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for tid, y in res["stems"].items():
                p = write_audio(out_dir / "stems" / f"{name}_{names[tid]}.wav", y, stem_fmt)
                z.write(p, p.name)
                files.append({"kind": "stem", "track": tid, "name": p.name, "path": str(p), "check": verify(p)})
        files.append({"kind": "zip", "name": zpath.name, "path": str(zpath)})
    if project_json is not None:
        jp = out_dir / f"{name}.choplab.json"
        jp.write_text(json.dumps(project_json, indent=2))
        files.append({"kind": "project", "name": jp.name, "path": str(jp)})
    progress("verifying", 0.95)
    return {"files": files, "metrics": res["metrics"], "folder": str(out_dir)}
