"""Music analysis engine: tempo (with half/double candidates), beat grid, downbeat estimate, key,
onsets, structural segments, loudness. Also emits per-beat features used by sample discovery."""
from __future__ import annotations

import numpy as np
import librosa
import pyloudnorm

from .audio_io import AudioError
from .core import NOTE_NAMES, SR, parse_key

ASR = 22050  # analysis rate
HOP = 512
MIN_SECONDS = 0.1
THOP = 256  # hop for tempo estimation
FLUX_LAG = 0.018
MIN_TEMPO_CONFIDENCE = 0.06  # below this the material has no usable pulse (pads, drones): use a plain grid
ONE_SHOT_BPM = 90.0  # nominal time base for material without a tempo
BHOP = 128  # finer hop for beat tracking: librosa's tracker uses an integer-frame period

# Krumhansl-Kessler key profiles
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def estimate_key(chroma_sum: np.ndarray) -> list[dict]:
    """Correlate a pitch-class distribution against 24 key profiles; returns ranked candidates."""
    c = chroma_sum / (chroma_sum.sum() + 1e-9)
    cands = []
    for tonic in range(12):
        for mode, prof in (("major", _MAJOR), ("minor", _MINOR)):
            r = float(np.corrcoef(c, np.roll(prof, tonic))[0, 1])
            cands.append({"key": f"{NOTE_NAMES[tonic]} {mode}", "tonic": tonic, "mode": mode, "score": round(r, 4)})
    cands.sort(key=lambda d: -d["score"])
    return cands


def _acf(x: np.ndarray) -> np.ndarray:
    n = len(x)
    f = np.fft.rfft(x - x.mean(), 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    return ac / (ac[0] + 1e-12)


RELATED = (1.0, 2.0, 0.5, 3.0, 1 / 3, 1.5, 2 / 3, 4.0, 0.25)


def tempo_candidates(mono: np.ndarray) -> tuple[list[dict], float]:
    """Tempo from how the music repeats, not from drum hits: the autocorrelation of note onsets *plus chord
    changes*, read with a comb at the beat, two beats, the bar and two bars. Samples people flip are often
    drumless (keys, strings, guitar), where a drum-oriented beat tracker guesses wrong; harmony changing every
    bar or two is the most reliable pulse they have. Returns candidates (best first) and a 0..1 confidence."""
    fps = ASR / THOP
    onset = librosa.onset.onset_strength(y=mono, sr=ASR, hop_length=THOP, aggregate=np.median)
    chroma = librosa.feature.chroma_stft(y=mono, sr=ASR, hop_length=THOP * 2, n_fft=4096)
    chroma = chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-9)
    change = np.concatenate([[0.0], np.maximum(0.0, np.diff(chroma, axis=1)).sum(axis=0)])
    change = np.repeat(change, 2)[: len(onset)]
    change = np.pad(change, (0, len(onset) - len(change)))
    z = lambda x: (x - x.mean()) / (x.std() + 1e-9)
    ac = _acf(z(onset) + z(change))

    def at(lag: float) -> float:
        i = int(lag)
        return float(ac[i] * (1 - (lag - i)) + ac[i + 1] * (lag - i)) if i + 1 < len(ac) else 0.0

    bpms = np.arange(50.0, 200.0, 0.25)
    comb = np.array([sum(w * at(60.0 / b * fps * k) for k, w in ((1, 1.0), (2, 0.8), (4, 0.8), (8, 0.5), (0.5, 0.3))) for b in bpms])
    prior = np.exp(-np.log2(bpms / 92.0) ** 2 / (2 * 0.42 ** 2))  # tempos of records people sample
    peaks = [i for i in range(1, len(comb) - 1) if comb[i] > comb[i - 1] and comb[i] >= comb[i + 1] and comb[i] > 0.4 * comb.max()]
    if not peaks:
        return [], 0.0
    # a peak at 3:2 or 4:3 of a much stronger one is the triplet/dotted echo of that tempo, not a tempo itself
    echo = lambda i: any(comb[j] > 1.3 * comb[i] and any(abs(bpms[i] / bpms[j] / r - 1) < 0.04 for r in (1.5, 2 / 3, 4 / 3, 0.75))
                         for j in peaks)
    rank = {i: comb[i] * prior[i] * (0.5 if echo(i) else 1.0) for i in peaks}
    peaks.sort(key=lambda i: -rank[i])
    best = peaks[0]
    related = lambda i: any(abs(bpms[i] / bpms[best] / r - 1) < 0.04 for r in RELATED)
    rival = max((comb[i] for i in peaks if not related(i)), default=0.0)
    confidence = float(np.clip((comb[best] - rival) / (abs(comb[best]) + 1e-9), 0.0, 1.0)) if comb[best] > 0.12 else 0.0
    # refine the winner to a hundredth of a BPM using longer lags (a 4-bar loop cut 1% off is ~100 ms out at its end)
    fine = np.arange(bpms[best] - 0.6, bpms[best] + 0.6, 0.02)
    sharp = [sum(w * at(60.0 / b * fps * k) for k, w in ((1, 1.0), (2, 1.0), (4, 1.0), (8, 1.0), (16, 0.7))) for b in fine]
    out = [{"bpm": round(float(bpms[i]), 2), "evidence": round(float(comb[i]), 3), "prior": round(float(prior[i]), 3),
            "score": round(float(rank[i]), 3)} for i in peaks[:6]]
    out[0]["bpm"] = round(float(fine[int(np.argmax(sharp))]), 2)
    return out, round(confidence, 3)


def _phase_shift(oenv: np.ndarray, beat_times: np.ndarray, period: float) -> float:
    """Seconds to slide the whole grid so beats and off-beats sit on the *peaks* of the onset envelope. A note's
    perceived moment is where its energy rises fastest, not where it starts: with soft attacks (bass, guitar,
    brushes) those are 40-50 ms apart, enough to make new drums flam against the sample."""
    fps = ASR / BHOP
    pos = np.concatenate([beat_times, beat_times + period / 2]) * fps
    lags = np.arange(-int(0.07 * fps), int(0.07 * fps) + 1)
    score = [oenv[np.clip(np.round(pos + lag).astype(int), 0, len(oenv) - 1)].mean() for lag in lags]
    # the flux peak of an instantaneous attack reads ~18 ms late (analysis window); take that back out
    return float(lags[int(np.argmax(score))] / fps) - FLUX_LAG


def _swing(oenv: np.ndarray, beat_times: np.ndarray, period: float) -> float:
    """How far the sample's own off-beat 16ths are delayed, MPC style (50 = straight, 66 = triplet). Drums programmed
    with a different swing than the record flam against it, so the drums take theirs from here."""
    fps = ASR / BHOP
    step = period / 4
    odd = np.concatenate([beat_times + step, beat_times + 3 * step])

    def at(pct: float) -> float:
        idx = np.round((odd + (2 * pct / 100 - 1) * step + FLUX_LAG) * fps).astype(int)
        return float(oenv[idx[(idx >= 0) & (idx < len(oenv))]].mean()) if len(idx) else 0.0

    grid = np.arange(50, 68, 2.0)
    score = np.array([at(g) for g in grid])
    best = int(np.argmax(score))
    # needs real evidence: off-beat 16ths that exist at all, and clearly sit later than straight
    if score[best] < 0.6 * float(oenv.mean()) or score[best] < 1.2 * score[0]:
        return 50.0
    return float(grid[best])


def _rigid_grid(oenv: np.ndarray, bpm: float, duration: float) -> np.ndarray:
    """A metronomic grid at `bpm`, phased where the onsets are strongest."""
    period = 60.0 / bpm
    fps = ASR / BHOP
    t = np.arange(len(oenv)) / fps
    offs = np.arange(0.0, period, 0.004)
    score = [np.interp(np.arange(o, duration, period), t, oenv).mean() for o in offs]
    return np.arange(offs[int(np.argmax(score))], duration, period)


def _beats_at(oenv: np.ndarray, bpm: float) -> np.ndarray:
    """Beat frames at a given tempo; if the tracker locked onto the off-beats, shift by half a beat."""
    _, frames = librosa.beat.beat_track(onset_envelope=oenv, sr=ASR, hop_length=BHOP, bpm=bpm, units="frames", trim=False)
    if len(frames) > 4:
        half = int(round(np.median(np.diff(frames)) / 2))
        shifted = frames + half
        shifted = shifted[shifted < len(oenv)]
        if oenv[shifted].mean() > 1.15 * oenv[frames].mean():
            frames = shifted
    return frames


def _loop_tempo(duration: float, tracked: float, lead_in: float) -> float | None:
    """Tempo implied by the file length if it is a whole number of bars (2..64 beats) and agrees with the
    tempo estimate to within 0.8% (octave errors allowed). Far more exact than tracking a 2-bar loop."""
    if duration > 45 or lead_in > 0.05:  # a loop starts on its first hit, not after silence
        return None
    best = None
    for n in (2, 4, 8, 16, 32, 64):
        bpm = 60.0 * n / duration
        if not 60 <= bpm <= 180:
            continue
        err = min(abs(np.log2(bpm / (tracked * m))) for m in (0.5, 1.0, 2.0))
        if err < 0.012 and (best is None or err < best[0]):  # within 0.8%: the estimate is precise enough to demand it
            best = (err, bpm)
    return round(best[1], 3) if best else None


def _segments(feats: np.ndarray, beat_times: np.ndarray, duration: float, downbeats: np.ndarray) -> list[dict]:
    n = feats.shape[1]
    k = int(np.clip(round(duration / 15), 2, 10))
    if n < k * 2:
        return [{"start": 0.0, "end": round(duration, 3), "label": "Section A"}]
    bounds = librosa.segment.agglomerative(feats, k)
    # snap boundaries to the nearest bar line: sections start on downbeats
    snap = lambda t: float(downbeats[np.argmin(np.abs(downbeats - t))]) if len(downbeats) else t
    times = sorted({0.0, duration, *(snap(float(beat_times[min(b, n - 1)])) for b in bounds[1:])})
    times = [t for i, t in enumerate(times) if i == 0 or i == len(times) - 1 or (t - times[0] >= 1 and duration - t >= 1)]
    times = [t for i, t in enumerate(times) if i == 0 or t - times[i - 1] >= 1.0]  # no sliver sections
    times[-1] = duration
    idx = [int(np.searchsorted(beat_times, t)) for t in times]
    segs, protos = [], []
    for i in range(len(times) - 1):
        v = feats[:, idx[i]:max(idx[i + 1], idx[i] + 1)].mean(axis=1)
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = [float(v @ p) for p in protos]
        if sims and max(sims) > 0.97:  # recurring material gets the same letter
            label = f"Section {chr(65 + int(np.argmax(sims)))}"
        else:
            protos.append(v)
            label = f"Section {chr(64 + len(protos))}"
        segs.append({"start": round(times[i], 3), "end": round(times[i + 1], 3), "label": label})
    return segs


def _sync(m: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Mean of each column span [idx[j], idx[j+1]) -> exactly one column per beat."""
    m = np.atleast_2d(m)
    bounds = list(idx) + [m.shape[1]]
    return np.stack([m[:, a:max(b, a + 1)].mean(axis=1) for a, b in zip(bounds[:-1], bounds[1:])], axis=1)


def analyze(y48: np.ndarray, progress=lambda s, f=None: None, override: dict | None = None) -> tuple[dict, dict]:
    """Returns (analysis document, per-beat feature arrays). `override` = user corrections
    {bpm, grid_offset (a downbeat time), downbeat_phase, key}."""
    duration = len(y48) / SR
    mono = librosa.resample(y48.mean(axis=1), orig_sr=SR, target_sr=ASR)
    if np.sqrt(np.mean(mono ** 2)) < 1e-4:
        raise AudioError("The recording is silent; nothing to analyze.")

    if duration < MIN_SECONDS:
        raise AudioError(f"The sample is too short ({duration:.2f}s); use at least {MIN_SECONDS}s of audio.")

    progress("tempo", 0.1)
    oenv = librosa.onset.onset_strength(y=mono, sr=ASR, hop_length=HOP)
    onsets = librosa.onset.onset_detect(onset_envelope=oenv, sr=ASR, hop_length=HOP, units="time", backtrack=True)
    # a single hit / held note has no tempo of its own: the composer plays it chromatically instead of looping it
    one_shot = duration < 1.5 or (duration < 8.0 and len(onsets) <= 1)
    if one_shot:
        base_tempo, cands, beat_times, tempo_conf = ONE_SHOT_BPM, [], np.zeros(0), 0.0
    else:
        oenv_f = librosa.onset.onset_strength(y=mono, sr=ASR, hop_length=BHOP)
        low = librosa.onset.onset_strength(y=mono, sr=ASR, hop_length=BHOP, fmax=200, n_mels=32)[: len(oenv_f)]
        cands, tempo_conf = tempo_candidates(mono)
        base_tempo = cands[0]["bpm"] if cands else 90.0
        frames = _beats_at(oenv_f, base_tempo) if tempo_conf >= MIN_TEMPO_CONFIDENCE else np.zeros(0, int)
        beat_times = librosa.frames_to_time(frames, sr=ASR, hop_length=BHOP)
    weak_grid = len(beat_times) < 8
    if weak_grid:  # irregular / beatless material: fall back to a synthetic grid the user can correct
        beat_times = np.arange(0, duration, 60.0 / base_tempo)
    loop_bpm = None if one_shot or override else _loop_tempo(duration, base_tempo, float(np.argmax(np.abs(mono) > 0.1 * np.abs(mono).max())) / ASR)
    if loop_bpm:  # a tightly cut loop: its length gives the tempo exactly, and beat 1 is the first sample
        override = {"bpm": loop_bpm, "grid_offset": 0.0, "_auto": True}
    ibi = np.diff(beat_times)
    period = float(np.median(ibi)) if len(ibi) else 60.0 / base_tempo
    stability = float(1.0 - min(1.0, np.std(ibi) / (np.mean(ibi) + 1e-9))) if len(ibi) > 2 else 0.0
    # least-squares fit over all beats: sub-frame tempo precision (frame-quantized IBIs are ~2% off at 140)
    n_idx = np.round((beat_times - beat_times[0]) / period)
    if not weak_grid:
        # onset-flux peaks lag the attack by ~20 ms: re-anchor on backtracked onsets (constant shift)
        ons = librosa.onset.onset_detect(onset_envelope=oenv_f, sr=ASR, hop_length=BHOP, units="time", backtrack=True)
        if len(ons) >= 4:
            d = ons[np.argmin(np.abs(ons[None, :] - beat_times[:, None]), axis=1)] - beat_times
            d = d[np.abs(d) < 0.07]
            if len(d) >= 4:
                beat_times = beat_times + float(np.median(d))
        # robust line fit; drop tracker outliers (e.g. beats invented in a fade-out) before refitting
        coef = np.polyfit(n_idx, beat_times, 1)
        keep = np.abs(beat_times - np.polyval(coef, n_idx)) < 0.05
        if keep.sum() > 3:
            coef = np.polyfit(n_idx[keep], beat_times[keep], 1)
        period = float(coef[0])
        if np.median(np.abs(beat_times - np.polyval(coef, n_idx))) < 0.012:  # metronomic (programmed/quantized)
            beat_times = np.unique(np.polyval(coef, n_idx))
    if not weak_grid and abs(60.0 / period / base_tempo - 1) > 0.012:
        # the beat tracker wandered away from the tempo the music repeats at: trust the repetition
        beat_times = _rigid_grid(oenv_f, base_tempo, duration)
        period = 60.0 / base_tempo
    if not weak_grid:
        beat_times = beat_times + _phase_shift(oenv_f, beat_times, period)
    swing = 50.0 if weak_grid else _swing(oenv_f, beat_times, period)
    bpm = round(60.0 / period, 2)
    # extend the grid over quiet intro/outro parts the tracker skipped
    pre = beat_times[0] - period * np.arange(int(beat_times[0] / period), 0, -1)
    post = beat_times[-1] + period * np.arange(1, int((duration - beat_times[-1]) / period) + 1)
    beat_times = np.concatenate([pre, beat_times, post])
    beat_times = beat_times[(beat_times >= 0) & (beat_times < duration)]
    if override and (override.get("bpm") or override.get("grid_offset") is not None):
        # user-corrected grid: a strict metronomic grid through the given downbeat
        period = 60.0 / float(override.get("bpm") or bpm)
        bpm = round(60.0 / period, 2)
        off = float(override["grid_offset"]) if override.get("grid_offset") is not None else float(beat_times[0])
        k0 = int(np.floor(off / period))
        beat_times = off + period * np.arange(-k0, int((duration - off) / period) + 1)
        beat_times = beat_times[(beat_times >= 0) & (beat_times < duration)]
        weak_grid = False
    n_frames = len(oenv)
    # frame indices are only for feature syncing; beat_times stay sub-frame precise (HOP is ~23 ms)
    beats = np.clip(librosa.time_to_frames(beat_times, sr=ASR, hop_length=HOP), 0, n_frames - 1)

    progress("key", 0.25)
    harm = librosa.effects.harmonic(mono)
    chroma = librosa.feature.chroma_cqt(y=harm, sr=ASR, hop_length=HOP)[:, :n_frames]
    keys = estimate_key(chroma.sum(axis=1))
    key_conf = round(max(0.0, keys[0]["score"] - keys[1]["score"]) * 5 + max(0.0, keys[0]["score"]) * 0.5, 3)
    chroma_b = _sync(chroma, beats)

    progress("downbeats", 0.45)
    # downbeats: strong low-frequency onsets + harmonic changes (chords tend to change on beat 1)
    if one_shot:
        low_b = np.ones(len(beat_times))
    else:
        low_b = low[np.clip(librosa.time_to_frames(beat_times, sr=ASR, hop_length=BHOP), 0, len(low) - 1)]
    cn = chroma_b / (np.linalg.norm(chroma_b, axis=0, keepdims=True) + 1e-9)
    nov = np.concatenate([[0.0], 1 - np.sum(cn[:, 1:] * cn[:, :-1], axis=0)])
    z = lambda x: x / (x.mean() + 1e-9)
    score = z(low_b) + 2 * z(nov)
    phase_scores = [float(score[p::4].mean()) if len(score[p::4]) else 0.0 for p in range(4)]
    phase = int(np.argmax(phase_scores))
    srt = sorted(phase_scores, reverse=True)
    downbeat_conf = round((srt[0] - srt[1]) / (srt[0] + 1e-9), 3)
    if override and override.get("grid_offset") is not None:
        phase, downbeat_conf = k0 % 4, 1.0
    if override and override.get("downbeat_phase") is not None:
        phase, downbeat_conf = int(override["downbeat_phase"]), 1.0
    downbeats = beat_times[phase::4]

    progress("onsets", 0.55)

    progress("structure", 0.7)
    mfcc = librosa.feature.mfcc(y=mono, sr=ASR, hop_length=HOP, n_mfcc=13)[:, :n_frames]
    rms = librosa.feature.rms(y=mono, hop_length=HOP)[0][:n_frames]
    rms_p = librosa.feature.rms(y=mono - harm, hop_length=HOP)[0][:n_frames]
    mfcc_b = _sync(mfcc, beats)
    rms_b, rmsp_b, oenv_b = _sync(rms, beats)[0], _sync(rms_p, beats)[0], _sync(oenv, beats)[0]
    bt = beat_times
    feats = np.vstack([librosa.util.normalize(chroma_b, axis=0), mfcc_b[1:] / (np.abs(mfcc_b[1:]).max() + 1e-9),
                       2 * rmsp_b[None] / (rmsp_b.max() + 1e-9), rms_b[None] / (rms_b.max() + 1e-9)])
    segs = _segments(feats, bt, duration, downbeats)
    for s in segs:
        m = (onsets >= s["start"]) & (onsets < s["end"])
        a, b = np.searchsorted(bt, [s["start"], s["end"]])
        seg_chroma = chroma_b[:, a:max(b, a + 1)]
        s["energy"] = round(float(rms_b[a:max(b, a + 1)].mean()), 4)
        s["rhythmic_density"] = round(float(m.sum() / max(s["end"] - s["start"], 1e-3)), 3)
        s["tonal_stability"] = round(float(1 - np.mean(np.std(seg_chroma, axis=1))), 3)

    progress("loudness", 0.85)
    try:
        lufs = float(pyloudnorm.Meter(SR).integrated_loudness(y48)) if duration > 0.5 else None
    except ValueError:
        lufs = None
    peak = float(np.abs(y48).max())

    # per-bar chroma summary (used for key-aware bass generation)
    bar_chroma = [[round(float(x), 3) for x in chroma_b[:, i:i + 4].mean(axis=1)] for i in range(phase, len(bt), 4)]

    doc = {
        "duration": round(duration, 3), "sample_rate": SR,
        "bpm_candidates": cands, "detected_bpm": bpm, "bpm": bpm, "tempo_stability": round(stability, 3),
        "beat_confidence": 0.0 if weak_grid else round(stability, 3), "weak_grid": weak_grid,
        "tempo_confidence": tempo_conf, "swing": swing,
        "percussive_ratio": round(float(rms_p.mean() / (rms.mean() + 1e-9)), 3),
        "one_shot": bool(one_shot), "loop_locked": bool(loop_bpm),
        "beats": [round(float(t), 4) for t in beat_times],
        "downbeat_phase": phase, "downbeat_confidence": downbeat_conf,
        "downbeats": [round(float(t), 4) for t in downbeats], "grid_offset": round(float(downbeats[0]) if len(downbeats) else 0.0, 4),
        "key_candidates": keys[:5], "detected_key": keys[0]["key"], "key": keys[0]["key"], "key_confidence": min(1.0, key_conf),
        "pitch_class_distribution": [round(float(x), 4) for x in chroma.sum(axis=1) / (chroma.sum() + 1e-9)],
        "onsets": [round(float(t), 4) for t in onsets],
        "segments": segs, "bar_chroma": bar_chroma,
        "loudness_lufs": None if lufs is None or not np.isfinite(lufs) else round(lufs, 2),
        "peak_dbfs": round(20 * np.log10(max(peak, 1e-9)), 2),
    }
    if override and override.get("key"):
        if parse_key(override["key"]) is None:
            raise ValueError(f"Unknown key {override['key']!r}; use e.g. 'A minor'.")
        doc.update(key=override["key"], key_confidence=1.0)
    doc["override"] = {} if (override or {}).get("_auto") else {k: v for k, v in (override or {}).items() if v is not None}
    features = {"beat_times": bt, "chroma": chroma_b, "mfcc": mfcc_b, "rms": rms_b, "rms_perc": rmsp_b, "onset": oenv_b}
    return doc, features
