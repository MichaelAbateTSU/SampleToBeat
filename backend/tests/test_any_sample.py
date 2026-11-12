"""'Any sample': tight loops, loosely cut snippets and one-shots all have to turn into a valid beat."""
import numpy as np
import pytest

from choplab import analysis, audio_io, compose, render, sampling, store
from choplab.core import PPQ, SR

SPB = 60 / 92


def _source(tmp_path, y, name):
    path = audio_io.write_wav(tmp_path / f"{name}.wav", y)
    return {"id": store.new_id("src"), "internal_audio_path": str(path), "content_hash": name, "stems": {}}


def _beat(tmp_path, y, name, genre="boombap", strategy="loop_flip"):
    src = _source(tmp_path, y, name)
    doc, feats = analysis.analyze(y)
    found = sampling.discover(src, doc, feats)
    arr = compose.variation({"genre": genre, "bars": 16}, found, {src["id"]: src}, {src["id"]: doc}, strategy, seed=11)
    by = {s["id"]: s for s in found}
    res = render.render(arr, by, {src["id"]: src})
    return doc, found, arr, compose.evaluate(arr, by, {src["id"]: src}, res)


@pytest.mark.parametrize("bars", [1, 2, 4])
def test_tight_loop_gets_exact_tempo_and_is_used_whole(tmp_path, demo_audio, bars):
    y = demo_audio[: int(bars * 4 * SPB * SR)]
    doc, found, arr, ev = _beat(tmp_path, y, f"loop{bars}")
    assert doc["loop_locked"] and not doc["one_shot"] and doc["bpm"] == pytest.approx(92.0, abs=0.01)
    assert doc["downbeat_phase"] == 0 and doc["beats"][0] == 0.0
    assert found[0].get("full") and found[0]["beats"] == bars * 4 and found[0]["start"] == 0.0
    assert arr["meta"]["main_sample"] == found[0]["id"]
    assert ev["passed"], [c for c in ev["checks"] if not c["ok"]]


def test_loosely_cut_snippet_still_tiles_on_the_grid(tmp_path, demo_audio):
    y = demo_audio[: int(7.4 * SPB * SR)]  # not a whole number of beats
    doc, found, arr, ev = _beat(tmp_path, y, "loose")
    whole = found[0]
    assert whole.get("full") and whole["beats"] == 8 and not doc["loop_locked"]
    assert whole["source_bpm"] == pytest.approx(60 * 8 / (len(y) / SR), rel=1e-3)  # its own tempo, so 8 beats fill 2 bars
    assert ev["passed"], [c for c in ev["checks"] if not c["ok"]]


@pytest.mark.parametrize("seconds,genre,strategy", [(0.12, "trap", "loop_flip"), (0.5, "boombap", "dark"), (1.0, "drill", "creative")])
def test_one_shot_becomes_a_melody(tmp_path, demo_audio, seconds, genre, strategy):
    y = demo_audio[: int(seconds * SR)]
    doc, found, arr, ev = _beat(tmp_path, y, f"shot{seconds}", genre, strategy)
    assert doc["one_shot"] and len(found) == 1 and found[0]["kind"] == "oneshot"
    chops = arr["patterns"]["s_main"]["events"]
    assert len({e["pitch"] for e in chops}) >= 3  # played at several pitches, not just retriggered
    assert all(e["ratio"] == pytest.approx(arr["bpm"] / 90.0) for e in chops)  # never time-stretched
    assert arr["key"].endswith("minor") and arr["patterns"]["b_main"]["events"]
    assert ev["passed"], [c for c in ev["checks"] if not c["ok"]]


def test_unusable_audio_is_rejected_with_a_reason(demo_audio):
    with pytest.raises(audio_io.AudioError, match="too short"):
        analysis.analyze(demo_audio[: int(0.05 * SR)])
    with pytest.raises(audio_io.AudioError, match="silent"):
        analysis.analyze(np.zeros((SR, 2), np.float32))


def test_midi_export_matches_the_arrangement(tmp_path, demo_audio):
    _, _, arr, _ = _beat(tmp_path, demo_audio[: int(8 * SPB * SR)], "midi", "trap")
    data = render.midi(arr)
    assert data[:4] == b"MThd" and int.from_bytes(data[12:14], "big") == PPQ
    n_tracks = int.from_bytes(data[10:12], "big")
    assert data.count(b"MTrk") == n_tracks == 1 + sum(1 for t in arr["tracks"] if t["clips"])
    tempo = data.index(b"\xff\x51\x03")
    assert 60_000_000 / int.from_bytes(data[tempo + 3:tempo + 6], "big") == pytest.approx(arr["bpm"], rel=1e-4)
    # every chunk length is consistent: walking the chunks lands exactly on the end of the file
    pos = 14
    for _ in range(n_tracks):
        assert data[pos:pos + 4] == b"MTrk"
        pos += 8 + int.from_bytes(data[pos + 4:pos + 8], "big")
    assert pos == len(data)
    kick = next(t for t in arr["tracks"] if t["id"] == "kick")
    hits = len(render.timeline(arr, kick))
    assert data.count(bytes([0x99, 36])) == hits  # one note-on per kick, GM note 36 on channel 10
