"""Music analysis engine: tempo (with half/double candidates), beat grid, downbeat estimate, key,
onsets, structural segments, loudness. Also emits per-beat features used by sample discovery."""
from __future__ import annotations

import numpy as np
import librosa

from .audio_io import AudioError
from .core import NOTE_NAMES, SR, parse_key
from .render import loudness

ASR = 22050  # analysis rate
HOP = 512
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


def _alternation(low: np.ndarray, high: np.ndarray, frames: np.ndarray) -> float:
    """Backbeat cue: at the true beat level kick (low) and snare (high) alternate beat to beat."""
    if len(frames) < 8:
        return 0.0
    lo, hi = low[frames], high[frames]
    d = lo / (lo.mean() + 1e-9) - hi / (hi.mean() + 1e-9)
    sign = np.where(np.arange(len(d)) % 2 == 0, 1.0, -1.0)
    return abs(float(np.mean(d * sign)))


def _pick_tempo(oenv, low, high, base: float) -> tuple[float, np.ndarray, list[dict]]:
    cands = []
    for mult, label in ((0.5, "half-time"), (2 / 3, "two-thirds"), (1.0, "detected"), (1.5, "three-halves"),
                        (2.0, "double-time")):
        bpm = base * mult
        if not 50 <= bpm <= 200:
            continue
        _, bt = librosa.beat.beat_track(onset_envelope=oenv, sr=ASR, hop_length=BHOP, bpm=bpm, units="frames", trim=False)
        alt = _alternation(low, high, np.clip(bt, 0, len(low) - 1))
        prior = float(np.exp(-np.log2(bpm / 95) ** 2 / (2 * 0.45 ** 2)))  # hip-hop tempo prior
        if mult in (2 / 3, 1.5):
            prior *= 0.6  # 3:2 readings need real backbeat evidence to win
        cands.append({"bpm": round(bpm, 2), "interpretation": label, "backbeat": round(alt, 3),
                      "prior": round(prior, 3), "score": round(2 * alt + 0.5 * prior, 3), "_frames": bt})
    best = max(cands, key=lambda c: c["score"])
    frames = best["_frames"]
    if len(frames) > 4:  # tracker locked onto the off-beats? compare with the half-period-shifted grid
        half = int(round(np.median(np.diff(frames)) / 2))
        shifted = frames + half
        shifted = shifted[shifted < len(oenv)]
        if oenv[shifted].mean() > 1.15 * oenv[frames].mean():
            frames = shifted
    for c in cands:
        c.pop("_frames")
    return best["bpm"], frames, sorted(cands, key=lambda c: -c["score"])


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


def _tempo(oenv: np.ndarray) -> float:
    """The same mean tempogram/prior, without an entire recording's FFT workspace."""
    window = int(librosa.time_to_frames(8.0, sr=ASR, hop_length=BHOP))
    padded = np.pad(oenv, (window // 2, window // 2), mode="linear_ramp", end_values=(0, 0))
    total = np.zeros((window, 1), np.float64)
    for start in range(0, len(oenv), 256):
        end = min(len(oenv), start + 256)
        tg = librosa.feature.tempogram(onset_envelope=padded[start:end + window - 1],
                                      sr=ASR, hop_length=BHOP, win_length=window, center=False)
        total += tg.sum(axis=1, keepdims=True)
    return float(librosa.feature.tempo(tg=total / len(oenv), sr=ASR, hop_length=BHOP)[0])


def _mel_spectrogram(mono: np.ndarray, hop: int, **kwargs) -> np.ndarray:
    """Keep the identical centered mel frames/global dB floor, with a bounded STFT workspace."""
    n_fft, frames = 2048, 1 + len(mono) // hop
    mel = np.empty((kwargs.get("n_mels", 128), frames), dtype=mono.dtype)
    for start in range(0, frames, 256):
        end = min(frames, start + 256)
        lo, hi = start * hop - n_fft // 2, (end - 1) * hop + n_fft // 2
        audio = mono[max(0, lo):min(len(mono), hi)]
        if lo < 0 or hi > len(mono):
            audio = np.pad(audio, (max(0, -lo), max(0, hi - len(mono))))
        mel[:, start:end] = librosa.feature.melspectrogram(
            y=audio, sr=ASR, n_fft=n_fft, hop_length=hop, center=False, **kwargs)
    return mel


def _onset_strength(mono: np.ndarray, hop: int, **kwargs) -> np.ndarray:
    mel = _mel_spectrogram(mono, hop, **kwargs)
    return librosa.onset.onset_strength(S=librosa.power_to_db(mel), sr=ASR, hop_length=hop)


def _harmonic(mono: np.ndarray) -> np.ndarray:
    spectrum = librosa.stft(mono)
    # Default 31-frame median filter needs 15 original frames on either side.
    previous = spectrum[:, :0].copy()
    for start in range(0, spectrum.shape[1], 256):
        end = min(start + 256, spectrum.shape[1])
        block = np.concatenate([previous, spectrum[:, start:min(end + 15, spectrum.shape[1])]], axis=1)
        left = previous.shape[1]
        previous = spectrum[:, max(start, end - 15):end].copy()
        harmonic = librosa.decompose.hpss(block)[0]
        spectrum[:, start:end] = harmonic[:, left:left + end - start]
    return librosa.istft(spectrum, dtype=mono.dtype, length=len(mono))


def analyze(y48: np.ndarray, progress=lambda s, f=None: None, override: dict | None = None) -> tuple[dict, dict]:
    """Returns (analysis document, per-beat feature arrays). `override` = user corrections
    {bpm, grid_offset (a downbeat time), downbeat_phase, key}."""
    duration = len(y48) / SR
    mono = librosa.resample(y48.mean(axis=1), orig_sr=SR, target_sr=ASR)
    if np.sqrt(np.mean(mono ** 2)) < 1e-4:
        raise AudioError("The recording is silent; nothing to analyze.")

    progress("tempo", 0.1)
    oenv = _onset_strength(mono, HOP)
    oenv_f = _onset_strength(mono, BHOP)
    low = _onset_strength(mono, BHOP, fmax=200, n_mels=32)[: len(oenv_f)]
    high = _onset_strength(mono, BHOP, fmin=1500, n_mels=32)[: len(oenv_f)]
    base_tempo = _tempo(oenv_f) or 90.0
    base_tempo, frames, cands = _pick_tempo(oenv_f, low, high, base_tempo)
    beat_times = librosa.frames_to_time(frames, sr=ASR, hop_length=BHOP)
    weak_grid = len(beat_times) < 8
    if weak_grid:  # irregular / beatless material: fall back to a synthetic grid the user can correct
        beat_times = np.arange(0, duration, 60.0 / base_tempo)
    ibi = np.diff(beat_times)
    period = float(np.median(ibi))
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
    harm = _harmonic(mono)
    chroma = librosa.feature.chroma_cqt(y=harm, sr=ASR, hop_length=HOP)[:, :n_frames]
    keys = estimate_key(chroma.sum(axis=1))
    key_conf = round(max(0.0, keys[0]["score"] - keys[1]["score"]) * 5 + max(0.0, keys[0]["score"]) * 0.5, 3)
    chroma_b = _sync(chroma, beats)

    progress("downbeats", 0.45)
    # downbeats: strong low-frequency onsets + harmonic changes (chords tend to change on beat 1)
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
    onsets = librosa.onset.onset_detect(onset_envelope=oenv, sr=ASR, hop_length=HOP, units="time", backtrack=True)

    progress("structure", 0.7)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(_mel_spectrogram(mono, HOP)),
                               sr=ASR, hop_length=HOP, n_mfcc=13)[:, :n_frames]
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
    del mono, harm
    lufs = loudness(y48)
    peak = float(np.abs(y48).max())

    # per-bar chroma summary (used for key-aware bass generation)
    bar_chroma = [[round(float(x), 3) for x in chroma_b[:, i:i + 4].mean(axis=1)] for i in range(phase, len(bt), 4)]

    doc = {
        "duration": round(duration, 3), "sample_rate": SR,
        "bpm_candidates": cands, "detected_bpm": bpm, "bpm": bpm, "tempo_stability": round(stability, 3),
        "beat_confidence": 0.0 if weak_grid else round(stability, 3), "weak_grid": weak_grid,
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
    doc["override"] = {k: v for k, v in (override or {}).items() if v is not None}
    features = {"beat_times": bt, "chroma": chroma_b, "mfcc": mfcc_b, "rms": rms_b, "rms_perc": rmsp_b, "onset": oenv_b}
    return doc, features
