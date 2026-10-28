"""Analysis regression tests on the deterministic synthetic demo (92 BPM, A minor, Am-F-C-G)."""
import numpy as np
import pytest

from choplab import analysis, demo
from choplab.audio_io import AudioError


def test_demo_tempo_key_and_downbeat(demo_analysis):
    doc, feats = demo_analysis
    assert doc["bpm"] == pytest.approx(92, abs=0.5)
    assert doc["key"] == "A minor"
    assert doc["downbeats"][0] == pytest.approx(0.0, abs=0.01)
    b = np.array(doc["beats"])
    p = 60 / 92
    assert np.abs(b - np.round(b / p) * p).max() < 0.008  # every beat within 8 ms of the true grid (no flam)
    assert len(doc["bar_chroma"]) >= 15 and feats  # one chroma per bar + feature arrays for discovery


def test_fast_tempo_is_not_halved():
    doc, _ = analysis.analyze(demo.make(bpm=140, bars=12))
    assert doc["bpm"] == pytest.approx(140, abs=1.0)


def test_user_override_builds_a_strict_grid(demo_audio):
    doc, _ = analysis.analyze(demo_audio, override={"bpm": 46, "grid_offset": 60 / 92 * 2, "key": "C major"})
    assert doc["bpm"] == 46.0
    assert np.allclose(np.diff(doc["beats"]), 60 / 46, atol=1e-3)
    assert min(abs(d - 60 / 92 * 2) for d in doc["downbeats"]) < 1e-3  # the given downbeat is on the grid
    assert doc["key"] == "C major" and doc["key_confidence"] == 1.0
    assert doc["detected_key"] == "A minor"  # the detection is kept for reference
    assert doc["override"]["bpm"] == 46


def test_silence_is_rejected():
    with pytest.raises(AudioError):
        analysis.analyze(np.zeros((48000 * 3, 2), np.float32))
