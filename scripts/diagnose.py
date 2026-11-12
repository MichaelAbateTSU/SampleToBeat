"""Measures the things that make a flip sound wrong, on real audio, since nobody can listen in CI.

  cd backend && uv run python ../scripts/diagnose.py tests/audio/*.mp3 [--genres boombap,trap] [--out DIR]

For every sample and genre it generates the four flips and reports:
  tempo     detected BPM (the file name may end in _<bpm> as the expected value) and beat confidence
  stretch   how far the sample is time-stretched (percent) and pitched (semitones)
  grid      timing offset of the sample track against the eighth-note grid (ms; 0 = on the beat) and how
            focused its rhythm is on that grid (1.0 = unrelated)
  bass      share of 808 notes whose pitch is one of the three strongest pitch classes the sample plays under
            them, and the share that sit a semitone away from a stronger pitch (an audible clash)
  balance   level of sub / low / low-mid / mid / high bands of the master, and each stem's RMS/peak in dB
With --out it also writes each flip as an MP3 for listening."""
import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CHOPLAB_DATA", tempfile.mkdtemp(prefix="choplab_diag_"))

import librosa  # noqa: E402
import numpy as np  # noqa: E402

from choplab import analysis, audio_io, compose, render, sampling  # noqa: E402
from choplab.core import PPQ, SR, STEP_TICKS, swing_offset_ticks, ticks_to_seconds  # noqa: E402

BANDS = [("sub", 20, 60), ("low", 60, 200), ("lowmid", 200, 500), ("mid", 500, 2000), ("high", 2000, 8000), ("air", 8000, 20000)]


def band_levels(y: np.ndarray) -> dict[str, float]:
    m = y.mean(axis=1)
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    tot = spec.sum() + 1e-12
    return {n: round(10 * np.log10(spec[(f >= lo) & (f < hi)].sum() / tot + 1e-12), 1) for n, lo, hi in BANDS}


def grid_alignment(stem: np.ndarray, bpm: float, swing: float) -> tuple[float, float] | None:
    """(timing offset in ms, focus). Slides an eighth-note comb over the sample track's onset envelope: offset is
    where it fits best (0 = on the beat), focus is how much better that fit is than the average position
    (1.0 = no rhythm relation to the grid at all)."""
    sr, hop = 22050, 64
    m = librosa.resample(stem.mean(axis=1), orig_sr=SR, target_sr=sr)
    env = librosa.onset.onset_strength(y=m, sr=sr, hop_length=hop)
    if env.max() <= 0:
        return None
    fps = sr / hop
    eighth = 30.0 / bpm * fps
    lags = np.arange(-int(eighth / 2), int(eighth / 2) + 1)
    pos = np.arange(0, len(env) - eighth, eighth)
    score = np.array([env[np.clip(np.round(pos + lag).astype(int), 0, len(env) - 1)].mean() for lag in lags])
    best = int(np.argmax(score))
    return round(float(lags[best] / fps * 1000), 1), round(float(score[best] / (score.mean() + 1e-9)), 2)


def bass_agreement(arr: dict, sample_stem: np.ndarray) -> tuple[float, float] | None:
    track = next(t for t in arr["tracks"] if t["kind"] == "bass")
    notes = [(t, e) for t, e, _ in render.timeline(arr, track)]
    if not notes:
        return None
    m = librosa.resample(sample_stem.mean(axis=1), orig_sr=SR, target_sr=22050)
    chroma = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(m), sr=22050, hop_length=512)
    good = clash = n = 0
    for t, e in notes:
        a = int(ticks_to_seconds(t, arr["bpm"]) * 22050 / 512)
        b = max(a + 2, int(ticks_to_seconds(t + e["dur"], arr["bpm"]) * 22050 / 512))
        c = chroma[:, a:b].mean(axis=1) if a < chroma.shape[1] else np.zeros(12)
        if c.sum() < 1e-6:
            continue
        pc = e["midi"] % 12
        n += 1
        good += pc in np.argsort(c)[-3:]
        clash += max(c[(pc + 1) % 12], c[(pc - 1) % 12]) > 1.25 * c[pc]
    return (round(100 * good / n, 1), round(100 * clash / n, 1)) if n else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--genres", default="boombap,trap")
    ap.add_argument("--out")
    ap.add_argument("--bars", type=int, default=16)
    a = ap.parse_args()
    summary, tilt = [], []
    for path in map(Path, a.files):
        y = audio_io.decode(path)
        wav = audio_io.write_wav(Path(os.environ["CHOPLAB_DATA"]) / f"{path.stem}.wav", y)
        src = {"id": path.stem, "internal_audio_path": str(wav), "content_hash": path.stem, "stems": {}}
        doc, feats = analysis.analyze(y)
        samples = sampling.discover(src, doc, feats)
        by = {s["id"]: s for s in samples}
        want = re.search(r"_(\d+)$", path.stem)
        print(f"\n=== {path.name}: {doc['bpm']} BPM (expected {want.group(1) if want else '?'}), {doc['key']} "
              f"(conf {doc['key_confidence']}), beat conf {doc['beat_confidence']}, weak grid {doc['weak_grid']}")
        for genre in a.genres.split(","):
            for i, strat in enumerate(compose.ORDER):
                arr = compose.variation({"genre": genre, "bars": a.bars}, samples, {src["id"]: src}, {src["id"]: doc}, strat, 100 + i)
                res = render.render(arr, by, {src["id"]: src})
                main_s = by[arr["meta"]["main_sample"]]
                speed = 1 + arr["meta"].get("stretch", 0.0)
                grid = grid_alignment(res["stems"]["sample"], arr["bpm"], arr["swing"]) if "sample" in res["stems"] else None
                bass = bass_agreement(arr, res["stems"]["sample"]) if "sample" in res["stems"] else None
                stems = {k: f"{20 * np.log10(np.sqrt(np.mean(v ** 2)) + 1e-9):.0f}/{20 * np.log10(np.abs(v).max() + 1e-9):.0f}" for k, v in res["stems"].items()}
                hook = next((sc for sc in arr["sections"] if compose.ROLE[sc["name"]] == "main"), arr["sections"][0])
                spb = 4 * 60.0 / arr["bpm"] * SR  # tone is judged on the hook: intros and breakdowns are filtered on purpose
                bands = {k: float(v) for k, v in band_levels(res["master"][int(hook["start_bar"] * spb):int((hook["start_bar"] + hook["bars"]) * spb)]).items()}
                print(f"  {genre:8s}{strat:10s} {arr['bpm']:6.1f} BPM  stretch {100 * (speed - 1):+6.1f}%  pitch {arr['meta']['pitch']:+d}  "
                      f"offset/focus {grid}  bass ok/clash {bass}  | {arr['meta'].get('orders')} {main_s['name'][:24]}")
                print(f"           bands {bands}  stems {stems}")
                summary.append((grid, bass, abs(speed - 1)))
                tilt.append([bands[k] - bands["low"] for k in ("sub", "lowmid", "mid", "high")])
                print(f"           balance {arr['meta'].get('balance')}  kit {arr['kit']}")
                if a.out:
                    Path(a.out).mkdir(parents=True, exist_ok=True)
                    audio_io.write_mp3(Path(a.out) / f"{path.stem}__{genre}_{strat}.mp3", res["master"], bitrate="192k")
    grids = [abs(g[0]) for g, _, _ in summary if g is not None]
    ok = [b[0] for _, b, _ in summary if b]
    clash = [b[1] for _, b, _ in summary if b]
    ok, clash = ok or [float("nan")], clash or [float("nan")]  # no 808 at all when every sample kept its own bass
    print(f"\nOVERALL  mean |timing offset| {np.mean(grids):.1f} ms   bass in-harmony {np.mean(ok):.1f}%   bass clashes {np.mean(clash):.1f}%   "
          f"median stretch {100 * np.median([s for _, _, s in summary]):.1f}%   flips {len(summary)}")
    t = np.array(tilt)
    # a commercial master falls about 5 dB per octave above 100 Hz, which in these bands is roughly -4 / -6 / -10
    print("TONE relative to the 60-200 Hz band (sub, low-mid, mid, high): mean " + " ".join(f"{v:+.1f}" for v in t.mean(axis=0))
          + "   worst-case mid " + f"{t[:, 2].min():+.1f}" + "   reference about -2 -4 -6 -10")


if __name__ == "__main__":
    sys.exit(main())
