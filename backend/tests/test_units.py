"""Unit tests: musical time, caching keys, names, mastering helpers (spec 30)."""
import numpy as np
import pytest

from choplab import render
from choplab.core import (STEP_TICKS, TICKS_PER_BAR, cache_key, parse_key, swing_offset_ticks, ticks_to_samples,
                          ticks_to_seconds)


def test_tick_math():
    assert TICKS_PER_BAR == 1920
    assert ticks_to_seconds(TICKS_PER_BAR, 120) == pytest.approx(2.0)
    assert ticks_to_samples(TICKS_PER_BAR * 4, 90) == round(4 * 4 * 60 / 90 * 48000)
    # long arrangements do not drift: 128 bars at 87 BPM, to the sample
    assert ticks_to_samples(128 * TICKS_PER_BAR, 87) == round(128 * 4 * 60 / 87 * 48000)


@pytest.mark.parametrize("swing,expected", [(50, 0), (66, round(0.32 * STEP_TICKS)), (75, STEP_TICKS // 2)])
def test_swing_moves_only_offbeat_16ths(swing, expected):
    assert swing_offset_ticks(0, swing) == 0
    assert swing_offset_ticks(2 * STEP_TICKS, swing) == 0
    assert swing_offset_ticks(STEP_TICKS, swing) == expected
    assert swing_offset_ticks(STEP_TICKS + 5, swing) == expected  # humanized hits still swing


def test_cache_key_is_stable_and_sensitive():
    a = cache_key("tf", ("h1",), 1.0, 2.0, {"pitch": 1, "reverse": False})
    assert a == cache_key("tf", ("h1",), 1.0, 2.0, {"reverse": False, "pitch": 1})  # dict order irrelevant
    assert a != cache_key("tf", ("h1",), 1.0, 2.0, {"pitch": 2, "reverse": False})
    assert a != cache_key("tf", ("h2",), 1.0, 2.0, {"pitch": 1, "reverse": False})


def test_parse_key():
    assert parse_key("A minor") == (9, "minor")
    assert parse_key("C# major") == (1, "major")
    assert parse_key("H minor") is None
    assert parse_key(None) is None


@pytest.mark.parametrize("raw,clean", [("my beat", "my beat"), ("../../etc/passwd", "etc_passwd"),
                                       ("con:*?<>|", "con_"), ("NUL", "NUL_"), ("console", "console"),
                                       ("", "beat"), ("...", "beat")])
def test_safe_name(raw, clean):
    assert render.safe_name(raw) == clean


def test_limiter_never_exceeds_ceiling_and_leaves_quiet_audio_alone():
    rng = np.random.default_rng(1)
    y = (rng.normal(0, 0.3, (48000, 2)) * np.linspace(0.2, 3, 48000)[:, None]).astype(np.float32)
    out = render.limit(y, 0.89)
    assert np.abs(out).max() <= 0.89 + 1e-6
    quiet = y[:2000] * 0.1
    assert np.allclose(render.limit(quiet, 0.89), quiet)


def test_master_hits_loudness_target_and_true_peak_ceiling():
    t = np.arange(48000 * 6) / 48000
    y = (0.05 * np.sin(2 * np.pi * 220 * t) * (1 + np.sin(2 * np.pi * 2 * t))).astype(np.float32)
    y = np.stack([y, y], 1)
    m = render._master(y, {"a": y}, -14.0)["metrics"]
    assert m["true_peak_dbfs"] <= -1.0 + 0.01
    assert m["lufs"] == pytest.approx(-14.0, abs=1.0)
    assert m["clipped_samples"] == 0
