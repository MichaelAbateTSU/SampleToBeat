"""Analysis regression tests on the deterministic synthetic demo (92 BPM, A minor, Am-F-C-G)."""
import numpy as np
import pytest

from choplab import analysis, demo
from choplab.audio_io import AudioError


@pytest.mark.parametrize("length", [137, 256, 513, 2000])
def test_bounded_tempogram_preserves_global_tempo(length):
    import librosa

    oenv = np.random.default_rng(9).random(length).astype(np.float32)
    oenv[::113] += 8
    expected = float(librosa.feature.tempo(onset_envelope=oenv, sr=analysis.ASR,
                                          hop_length=analysis.BHOP)[0])
    assert analysis._tempo(oenv) == expected


@pytest.mark.parametrize("hop,kwargs", [(analysis.HOP, {}), (analysis.BHOP, {}),
                                       (analysis.BHOP, {"fmax": 200, "n_mels": 32}),
                                       (analysis.BHOP, {"fmin": 1500, "n_mels": 32})])
def test_bounded_onset_frames_match_full_spectrogram(hop, kwargs):
    import librosa

    mono = np.random.default_rng(15).normal(0, 0.1, 79031).astype(np.float32)
    mono[:1000] *= 0.0001
    expected = librosa.onset.onset_strength(y=mono, sr=analysis.ASR, hop_length=hop, **kwargs)
    actual = analysis._onset_strength(mono, hop, **kwargs)
    np.testing.assert_allclose(actual, expected, atol=2e-6, rtol=2e-6)


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
