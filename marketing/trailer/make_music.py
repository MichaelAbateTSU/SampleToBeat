"""The trailer's soundtrack is made by the product: the built-in loop, flipped by the ChopLab engine.
Writes <out>/music.wav, <out>/dusty_keys.wav (the sample uploaded on screen) and <out>/music.json (tempo and
section starts, so picture cuts can land on bar lines).
Run:  cd backend && uv run python ../marketing/trailer/make_music.py <out_dir>"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("CHOPLAB_DATA", tempfile.mkdtemp(prefix="choplab_trailer_"))

from choplab import analysis, audio_io, compose, demo, render, sampling  # noqa: E402
from choplab.core import SR  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
loop = demo.make()[: int(8 * 4 * 60 / demo.BPM * SR)]
wav = audio_io.write_wav(out / "dusty_keys.wav", loop, subtype="PCM_16")
src = {"id": "demo", "internal_audio_path": str(wav), "content_hash": "trailer", "stems": {}}
doc, feats = analysis.analyze(loop)
samples = sampling.discover(src, doc, feats)
by = {s["id"]: s for s in samples}
arr = compose.variation({"genre": "boombap", "bars": 16, "energy": 0.8, "drum_complexity": 0.6}, samples, {"demo": src}, {"demo": doc}, "hard_chop", 21)
res = render.render(arr, by, {"demo": src})
assert compose.evaluate(arr, by, {"demo": src}, res)["passed"]
audio_io.write_wav(out / "music.wav", res["master"], subtype="PCM_24")
bar = 240 / arr["bpm"]
info = {"bpm": arr["bpm"], "bar_seconds": bar, "duration": len(res["master"]) / SR,
        "sections": [{"name": s["name"], "start": round(s["start_bar"] * bar, 3), "bars": s["bars"]} for s in arr["sections"]]}
(out / "music.json").write_text(json.dumps(info, indent=2))
print(json.dumps(info))
