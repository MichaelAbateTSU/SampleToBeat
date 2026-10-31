"""DSP tests: pitch, stretch, reverse, fades, 808 tuning, drums (spec 30)."""
import numpy as np
import pytest

from choplab import audio_io, bass, drums, render, transform
from choplab.core import SR, TICKS_PER_BAR, midi_to_hz, ticks_to_samples
from conftest import dominant_hz


def sine(hz, sec=1.0, amp=0.5):
    t = np.arange(int(SR * sec)) / SR
    y = (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    return np.stack([y, y], 1)


@pytest.mark.parametrize("semis", [-5, 3, 12])
def test_pitch_shift_changes_frequency_not_length(semis):
    y = transform.pitch_stretch(sine(440), semis, 1.0)
    assert abs(len(y) - SR) < SR * 0.01
    assert dominant_hz(y[SR // 10: -SR // 10]) == pytest.approx(440 * 2 ** (semis / 12), rel=0.01)


@pytest.mark.parametrize("speed", [0.75, 1.25, 2.0])
def test_stretch_changes_length_not_pitch(speed):
    y = transform.pitch_stretch(sine(440, 2.0), 0, speed)
    assert len(y) == pytest.approx(2 * SR / speed, rel=0.01)
    assert dominant_hz(y[SR // 10: -SR // 10]) == pytest.approx(440, rel=0.01)


def test_tape_mode_links_pitch_and_speed():
    y = transform.pitch_stretch(sine(440, 2.0), 12, 1.0, mode="tape")
    assert len(y) == pytest.approx(SR, rel=0.01)  # an octave up on a tape plays twice as fast
    assert dominant_hz(y) == pytest.approx(880, rel=0.01)
    t = {"pitch_semitones": 12, "stretch_mode": "tape"}
    locked = transform.transform(sine(440, 2.0), t, 1.0 / transform.tape_rate(t))  # grid-locked caller
    assert len(locked) == pytest.approx(2 * SR, rel=0.01) and dominant_hz(locked) == pytest.approx(880, rel=0.01)


def test_reverse_fades_gain_and_output_shape():
    ramp = np.stack([np.linspace(0, 1, SR, dtype=np.float32)] * 2, 1)
    y = transform.transform(ramp, {"reverse": True, "fade_in_ms": 10, "fade_out_ms": 10, "gain_db": -6})
    assert y.shape == ramp.shape and y.dtype == np.float32
    assert y[0, 0] == 0 and y[-1, 0] == 0  # fades reach silence at both ends
    mid = SR // 2
    assert y[mid - 100, 0] > y[mid + 100, 0]  # reversed ramp descends
    assert y[mid, 0] == pytest.approx(ramp[mid, 0] * 10 ** (-6 / 20), rel=0.01)


def test_effects_are_finite_stereo_and_bounded_length():
    y = sine(220, 1.0)
    fx = {**transform.DEFAULT_FX, "highpass_hz": 150, "lowpass_hz": 6000, "saturation": 0.5, "bitcrush_bits": 10,
          "reverb": 0.4, "delay": 0.3, "width": 1.3, "compress": True, "wobble": 0.5, "vinyl": 0.5,
          "eq_low_db": -3, "eq_mid_db": 2, "eq_high_db": -4}
    out = transform.effects(y, fx, 90)
    assert out.shape == y.shape and np.isfinite(out).all()


def test_eq_shelves_move_the_right_band():
    lows = transform.effects(sine(60), {"eq_low_db": -12})
    highs = transform.effects(sine(10000), {"eq_low_db": -12})
    assert np.abs(lows).max() < 0.5 * 10 ** (-9 / 20)
    assert np.abs(highs).max() == pytest.approx(0.5, rel=0.05)


@pytest.mark.parametrize("midi", [28, 33, 40])
def test_808_is_tuned_to_its_note(midi):
    y = bass.render([{"tick": 0, "dur": TICKS_PER_BAR, "midi": midi}], 90, SR * 3)
    assert y.ndim == 1 and np.isfinite(y).all() and np.abs(y).max() <= 1.0
    body = y[int(0.15 * SR): int(1.0 * SR)]  # after the pitch-envelope drop
    assert dominant_hz(body) == pytest.approx(midi_to_hz(midi), abs=1.5)


def test_808_slide_glides_to_target_without_retrigger():
    notes = [{"tick": 0, "dur": 960, "midi": 33}, {"tick": 480, "dur": 480, "midi": 45, "slide": True}]
    y = bass.render(notes, 120, SR * 2, {"drive": 0, "harmonics": 0})
    s = ticks_to_samples(480, 120)
    tail = y[s + int(0.12 * SR): s + int(0.45 * SR)]
    assert dominant_hz(tail) == pytest.approx(midi_to_hz(45), abs=3)
    assert len(bass._chains(notes, 120, SR * 2)) == 1  # one voice: legato, not a second attack


@pytest.mark.parametrize("genre", list(drums.GENRES))
def test_drum_patterns_are_deterministic_and_on_grid(genre):
    a = drums.generate(genre, 0.6, seed=7, bars=2)
    assert a == drums.generate(genre, 0.6, seed=7, bars=2)
    assert a != drums.generate(genre, 0.6, seed=8, bars=2)
    assert set(a) == {"main", "alt", "sparse", "fill"}
    hits = [h for lane in a.values() for h in lane]
    assert hits and all(0 <= h["tick"] < 2 * TICKS_PER_BAR + 40 for h in hits)
    assert any(h["inst"] == "kick" and h["tick"] < 20 for h in a["main"])  # kick on the one
    assert {h["inst"] for h in a["main"]} <= set(drums.INSTRUMENTS)


@pytest.mark.parametrize("kit", list(drums.KITS))
def test_kit_sounds_are_valid(kit):
    for inst in drums.INSTRUMENTS:
        y = drums.sound(kit, inst)
        assert y.ndim == 2 and y.shape[1] == 2 and len(y) > 100
        assert np.isfinite(y).all() and 0.05 < np.abs(y).max() <= 1.0


@pytest.mark.parametrize("length", [17, SR - 1, SR, SR + 1, 3 * SR + 7])
def test_blockwise_true_peak_matches_full_polyphase_filter(length):
    from scipy.signal import resample_poly

    y = np.random.default_rng(91).normal(0, 0.2, (length, 2)).astype(np.float32)
    for position in (0, SR - 1, SR, SR + 1, length - 1):
        if position < length:
            y[position] = [1.0, -0.9]
    expected = float(np.abs(resample_poly(y, 4, 1, axis=0)).max())
    assert render.true_peak(y) == pytest.approx(expected, abs=1e-7)
    assert render.true_peak(y[:0]) == 0


def test_partial_region_reads_match_aligned_stem_sum_without_source_cache(tmp_path, monkeypatch):
    a = sine(220, 2)
    b = sine(440, 2, amp=0.1)
    paths = tuple(str(audio_io.write_wav(tmp_path / f"{i}.wav", y)) for i, y in enumerate((a, b)))
    def no_full_read(*_):
        raise AssertionError("Region reads should not load an entire internal source.")
    monkeypatch.setattr(transform, "source_audio", no_full_read)
    result = transform.region(paths, 0.25, 1.25)
    np.testing.assert_array_equal(result, (a + b)[SR // 4:5 * SR // 4])
    assert result.dtype == np.float32
    with pytest.raises(ValueError, match="bounds"):
        transform.region(paths, 0, 3)


def test_master_stem_gain_and_metrics_preserve_dsp(tmp_path):
    y = sine(220, 2.1, amp=0.2)
    original = {"left": y.copy(), "right": y * np.float32(0.4)}
    mix = np.sum(list(original.values()), axis=0)
    result = render._master(mix, {k: v.copy() for k, v in original.items()}, -14)
    gain = np.float32(10 ** (result["metrics"]["master_gain_db"] / 20))
    # Gain in the reported metric is rounded; compare at the corresponding tolerance.
    trim = np.float32(10 ** (result["metrics"]["stem_trim_db"] / 20))
    for name, stem in result["stems"].items():
        np.testing.assert_allclose(stem, original[name] * gain * trim, atol=5e-4)
    assert result["metrics"]["clipped_samples"] == 0
    assert result["metrics"]["true_peak_dbfs"] <= -0.9


@pytest.mark.parametrize("seconds,amplitude", [(0.51, 0.2), (1.73, 0.4), (3.05, 0.01), (1, 0)])
def test_channelwise_loudness_matches_bs1770_meter(seconds, amplitude):
    import pyloudnorm

    y = sine(220, seconds, amp=amplitude)
    y[len(y)//3:len(y)//2] *= 0.01
    y[:, 1] *= 0.37
    reference = pyloudnorm.Meter(SR).integrated_loudness(y.astype(np.float64))
    measured = render.loudness(y)
    if not np.isfinite(reference) or reference < -70:
        assert measured is None
    else:
        assert measured == pytest.approx(reference, abs=1e-10)


def test_streamed_beat_rms_matches_full_source_reduceat(tmp_path, monkeypatch):
    from choplab import sampling

    y = sine(220, 2.13)
    path = str(audio_io.write_wav(tmp_path / "rms.wav", y))
    beats = np.array([0.0, 0.21, 0.21, 0.93, 1.37, 2.12])
    indices = np.clip((beats * SR).astype(int), 0, len(y) - 1)
    mono = y.mean(axis=1)
    expected = np.sqrt(np.add.reduceat(mono.astype(np.float64) ** 2, indices)
                       / np.maximum(1, np.diff(np.append(indices, len(y)))))
    def full_read(*_):
        raise AssertionError("Internal beat RMS must not retain a full-source cache.")
    monkeypatch.setattr(transform, "source_audio", full_read)
    np.testing.assert_allclose(sampling._beat_rms((path,), beats), expected, atol=1e-14)


@pytest.mark.parametrize("fail", [False, True])
def test_mp3_uses_exact_bounded_pcm_and_cleans_partial_files(tmp_path, monkeypatch, fail):
    from types import SimpleNamespace

    y = sine(220, 2.1, amp=1.2)
    expected = np.clip(y, -1, 1).astype(np.float32).tobytes()
    original = y.copy()
    monkeypatch.setattr(audio_io, "ffmpeg_exe", lambda: "synthetic-ffmpeg")
    def encode(args, **kwargs):
        assert "input" not in kwargs
        pcm_path = Path(args[args.index("-i") + 1])
        assert pcm_path.read_bytes() == expected
        Path(args[-1]).write_bytes(b"synthetic mp3")
        return SimpleNamespace(returncode=int(fail), stderr=b"synthetic encoding error")
    from pathlib import Path
    monkeypatch.setattr(audio_io.subprocess, "run", encode)
    target = tmp_path / "out.mp3"
    if fail:
        with pytest.raises(audio_io.AudioError, match="encoding failed"):
            audio_io.write_mp3(target, y)
        assert not target.exists()
    else:
        assert audio_io.write_mp3(target, y).read_bytes() == b"synthetic mp3"
    np.testing.assert_array_equal(y, original)
    assert sorted(p.name for p in tmp_path.iterdir()) == ([] if fail else ["out.mp3"])
