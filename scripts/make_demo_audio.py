"""Renders the audio on the landing page with the real engine: the built-in synthetic loop and two flips of it.
Run from the repo root:  cd backend && uv run python ../scripts/make_demo_audio.py"""
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("CHOPLAB_DATA", tempfile.mkdtemp(prefix="choplab_demo_"))

from choplab import analysis, audio_io, compose, demo, render, sampling  # noqa: E402
from choplab.core import SR  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public" / "demo"
FLIPS = [  # id, title, genre, strategy, seed
    ("boombap", "Boom bap flip", "boombap", "hard_chop", 21),
    ("trap", "Dark trap flip", "trap", "dark", 8),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    loop = demo.make()[: int(8 * 4 * 60 / demo.BPM * SR)]  # the 8 harmonic bars
    wav = audio_io.write_wav(Path(os.environ["CHOPLAB_DATA"]) / "loop.wav", loop)
    src = {"id": "demo", "internal_audio_path": str(wav), "content_hash": "demo", "stems": {}}
    doc, feats = analysis.analyze(loop)
    samples = sampling.discover(src, doc, feats)
    by = {s["id"]: s for s in samples}
    audio_io.write_mp3(OUT / "sample.mp3", loop, bitrate="128k")
    out = [{"id": "demo-sample", "url": "/demo/sample.mp3", "title": "8-bar keys loop", "source": True,
            "note": f"{doc['bpm']:g} BPM · {doc['key']}", "peaks": audio_io.peaks(loop, 160), "duration": round(len(loop) / SR, 2)}]
    for fid, title, genre, strategy, seed in FLIPS:
        arr = compose.variation({"genre": genre, "bars": 16}, samples, {"demo": src}, {"demo": doc}, strategy, seed)
        res = render.render(arr, by, {"demo": src})
        ev = compose.evaluate(arr, by, {"demo": src}, res)
        assert ev["passed"], [c for c in ev["checks"] if not c["ok"]]
        audio_io.write_mp3(OUT / f"{fid}.mp3", res["master"], bitrate="128k")
        out.append({"id": f"demo-{fid}", "url": f"/demo/{fid}.mp3", "title": title,
                    "note": f"{arr['bpm']:g} BPM · {arr['key']} · {arr['bars']} bars",
                    "peaks": audio_io.peaks(res["master"], 160), "duration": round(len(res["master"]) / SR, 2)})
        print(title, arr["meta"]["description"])
    (ROOT / "frontend" / "src" / "demos.json").write_text(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
