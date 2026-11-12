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


@pytest.mark.parametrize("bpm", [72, 86, 104, 128, 140])
def test_tempo_is_exact_up_to_the_octave(bpm):
    """70 and 140 describe the same grid (the composer picks half or double time per genre); what must never
    happen is a 3:2 or otherwise unrelated tempo, which puts every chop off the beat."""
    doc, _ = analysis.analyze(demo.make(bpm=float(bpm), bars=12))
    assert min(abs(doc["bpm"] * k - bpm) for k in (0.5, 1, 2)) < 0.3


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


REAL = [("soul_nodrums_88", 88), ("funk_band_94", 94), ("dark_piano_70", 70), ("jazz_trio_82", 82), ("nylon_guitar_100", 100)]


@pytest.mark.parametrize("name,expected", REAL)
def test_tempo_of_realistic_samples(name, expected):
    """Generated instrumentals in the styles people sample, three of them without drums. The expected value is the
    tempo asked for when they were generated, so allow 2%."""
    from pathlib import Path

    from choplab import audio_io

    doc, _ = analysis.analyze(audio_io.decode(Path(__file__).parent / "audio" / f"{name}.mp3"))
    assert not doc["weak_grid"]
    assert min(abs(doc["bpm"] * k / expected - 1) for k in (0.5, 1, 2)) < 0.02, doc["bpm_candidates"]


def test_a_pad_with_no_pulse_is_not_given_a_fake_beat():
    from pathlib import Path

    from choplab import audio_io

    doc, _ = analysis.analyze(audio_io.decode(Path(__file__).parent / "audio" / "strings_choir_65.mp3"))
    assert doc["weak_grid"] and doc["tempo_confidence"] < analysis.MIN_TEMPO_CONFIDENCE
