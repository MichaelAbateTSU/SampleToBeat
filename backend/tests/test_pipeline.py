"""Sample discovery -> generator -> renderer -> export, on the synthetic demo (spec 30 integration)."""
import numpy as np
import pytest

from choplab import audio_io, compose, demo, render, sampling, schemas
from choplab.core import SR, TICKS_PER_BAR, data_path, ticks_to_samples


@pytest.fixture(scope="module")
def world(demo_analysis):
    doc, feats = demo_analysis
    wav = demo.write(data_path("pipeline", "demo.wav"))
    src = {"id": "src_demo", "project_id": "prj_t", "internal_audio_path": str(wav),
           "content_hash": audio_io.file_hash(wav), "stems": {}, "analysis": doc}
    samples = sampling.discover(src, doc, feats, count=12, diversity=0.5)
    return {"src": src, "sources": {src["id"]: src}, "analyses": {src["id"]: doc}, "samples": samples,
            "by": {s["id"]: s for s in samples}}


def test_discovery_finds_ranked_bar_aligned_loops(world):
    ss = world["samples"]
    assert len(ss) >= 6
    loops = [s for s in ss if s["kind"] == "loop"]
    assert loops and all(s["bars"] in (1, 2, 4, 8) for s in loops)
    assert all(s["explanation"] and 0 <= s["score"] <= 1 for s in ss)
    assert len({(s["start"], s["end"], s["stem"]) for s in ss}) == len(ss)  # no duplicates
    beat = 60 / world["analyses"]["src_demo"]["bpm"]
    for s in loops:  # loop length is a whole number of beats
        assert (s["end"] - s["start"] - s["pickup"]) / beat == pytest.approx(round(s["beats"]), abs=0.05)


def test_manual_sample_validates_bounds(world):
    doc = world["analyses"]["src_demo"]
    s = sampling.manual(world["src"], doc, 0.0, 60 / 92 * 4, ["mix"])
    assert s["kind"] == "loop" and s["beats"] == pytest.approx(4, abs=0.01)
    with pytest.raises(ValueError):
        sampling.manual(world["src"], doc, 10.0, 5.0, ["mix"])
    with pytest.raises(ValueError):
        sampling.manual(world["src"], doc, 0.0, doc["duration"] + 5, ["mix"])


@pytest.fixture(scope="module")
def variations(world):
    out = {}
    for strat in compose.ORDER:
        st = {**compose.DEFAULTS, "genre": "boombap", "bars": 8}
        arr = compose.variation(st, world["samples"], world["sources"], world["analyses"], strat, seed=11)
        out[strat] = (arr, render.render(arr, world["by"], world["sources"]))
    return out


def test_every_strategy_yields_a_valid_playable_arrangement(world, variations):
    for strat, (arr, res) in variations.items():
        assert schemas.arrangement(arr) == arr, strat  # generator output is already schema-clean
        m = res["metrics"]
        assert m["true_peak_dbfs"] <= -0.99 and m["clipped_samples"] == 0, strat
        assert m["lufs"] == pytest.approx(-14, abs=1.5), strat
        assert np.isfinite(res["master"]).all() and res["master"].shape[1] == 2
        assert len(res["master"]) == ticks_to_samples(arr["bars"] * TICKS_PER_BAR, arr["bpm"]) + int(render.TAIL_S * SR)
        assert {"kick", "snare", "bass"} <= set(res["stems"]), strat
        ev = compose.evaluate(arr, world["by"], world["sources"], res)
        failed = [c for c in ev["checks"] if not c["ok"] and c["kind"] == "technical"]
        assert not failed, (strat, failed)


def test_variations_differ_from_each_other(variations):
    sigs = {compose.signature(a) for a, _ in variations.values()}
    assert len(sigs) == len(variations)
    fps = [compose.fingerprint(r["master"]) for _, r in variations.values()]
    assert max(compose.similarity(a, b) for i, a in enumerate(fps) for b in fps[i + 1:]) < 0.97


def test_same_seed_is_reproducible(world, variations):
    arr, res = variations["loop_flip"]
    st = {**compose.DEFAULTS, "genre": "boombap", "bars": 8}
    again = compose.variation(st, world["samples"], world["sources"], world["analyses"], "loop_flip", seed=11)
    assert again == arr
    assert np.array_equal(render.render(again, world["by"], world["sources"])["master"], res["master"])


def test_loop_render_is_exact_and_seamless(world, variations):
    arr, _ = variations["loop_flip"]
    res = render.render(arr, world["by"], world["sources"], "loop", 2, 2)
    assert len(res["master"]) == ticks_to_samples(2 * TICKS_PER_BAR, arr["bpm"])
    with pytest.raises(ValueError):
        render.render(arr, world["by"], world["sources"], "loop", arr["bars"] - 1, 4)


def test_mute_solo_and_missing_sample(world, variations):
    arr, _ = variations["loop_flip"]
    solo = {**arr, "tracks": [{**t, "solo": t["id"] == "kick"} for t in arr["tracks"]]}
    assert set(render.render(solo, world["by"], world["sources"])["stems"]) == {"kick"}
    with pytest.raises(render.MissingAsset):
        render.render(arr, {}, world["sources"])


@pytest.mark.parametrize("fmt,ext", [("wav24", "wav"), ("wav16", "wav"), ("mp3", "mp3")])
def test_export_writes_verified_files(world, variations, tmp_path, fmt, ext):
    arr, _ = variations["loop_flip"]
    out = render.export(arr, world["by"], world["sources"], tmp_path, "My Beat", fmt, "loop", 0, 4, stems=fmt == "wav24",
                        project_json={"arrangement": arr})
    names = {f["name"] for f in out["files"]}
    assert f"My Beat.{ext}" in names and "My Beat.choplab.json" in names
    mix = next(f for f in out["files"] if f["kind"] == "mix")
    assert not mix["check"]["silent"] and mix["check"]["channels"] == 2
    assert mix["check"]["duration_s"] == pytest.approx(4 * 4 * 60 / arr["bpm"], abs=0.06)  # mp3 padding is trimmed
    if fmt == "wav24":
        stems = [f for f in out["files"] if f["kind"] == "stem"]
        assert len(stems) >= 4 and all(not f["check"]["silent"] for f in stems)
        assert (tmp_path / "My Beat_stems.zip").exists()
        ys = [audio_io.decode(f["path"]) for f in stems]
        assert len({len(y) for y in ys}) == 1  # stems line up sample-for-sample
