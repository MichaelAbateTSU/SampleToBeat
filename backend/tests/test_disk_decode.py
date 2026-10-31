"""Sample parity and failure/cleanup contracts for the standalone disk decoder."""
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from choplab import audio_io, disk_decode


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
@pytest.mark.parametrize("channels", [1, 2, 4])
@pytest.mark.parametrize("frames", [1, 73, 65535, 65536, 65537, 150003])
def test_soundfile_sample_parity(tmp_path, rate, channels, frames):
    rng = np.random.default_rng(91)
    y = rng.uniform(-0.7, 0.7, (frames, channels)).astype(np.float32)
    source = tmp_path / "source.wav"
    sf.write(source, y, rate, subtype="FLOAT")
    expected = audio_io.decode(source)
    output = disk_decode.decode_to_wav(source, tmp_path / "out.wav")
    actual, sr = sf.read(output, dtype="float32", always_2d=True)
    assert sr == 48000 and actual.shape == expected.shape
    np.testing.assert_array_equal(actual, expected)
    assert sf.info(output).subtype == "FLOAT"
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
def test_nonfinite_parity(tmp_path, rate):
    y = np.zeros((150003, 2), np.float32)
    y[65530:65545] = np.nan
    y[131070] = np.inf
    y[131071] = -np.inf
    source = tmp_path / "nonfinite.wav"
    sf.write(source, y, rate, subtype="FLOAT")
    expected = audio_io.decode(source)
    actual, _ = sf.read(disk_decode.decode_to_wav(source, tmp_path / "out.wav"),
                        dtype="float32", always_2d=True)
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("format,subtype", [("FLAC", "PCM_16"), ("AIFF", "PCM_24"), ("OGG", "VORBIS")])
def test_container_formats(tmp_path, format, subtype):
    source = tmp_path / "source.audio"
    sf.write(source, np.random.default_rng(5).normal(0, .1, (70003, 2)), 44100,
             format=format, subtype=subtype)
    actual, _ = sf.read(disk_decode.decode_to_wav(source, tmp_path / "out.wav"),
                        dtype="float32", always_2d=True)
    np.testing.assert_array_equal(actual, audio_io.decode(source))


@pytest.mark.parametrize("channels", [1, 2, 4])
def test_ffmpeg_fallback_parity(tmp_path, monkeypatch, channels):
    exe = audio_io.ffmpeg_exe()
    assert exe, "FFmpeg is required for fallback parity validation"
    source = tmp_path / "source.wav"
    sf.write(source, np.random.default_rng(2).normal(0, .1, (96003, channels)), 96000)
    compressed = tmp_path / "source.m4a"
    subprocess.run([exe, "-v", "error", "-y", "-i", str(source), "-c:a", "aac", str(compressed)],
                   check=True, timeout=30)
    expected = audio_io.decode(compressed)
    actual, _ = sf.read(disk_decode.decode_to_wav(compressed, tmp_path / "out.wav"),
                        dtype="float32", always_2d=True)
    np.testing.assert_array_equal(actual, expected)


def test_bounds_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_io, "MAX_SECONDS", 1)
    source = tmp_path / "source.wav"
    destination = tmp_path / "out.wav"
    sf.write(source, np.zeros((48000, 2), np.float32), 48000)
    disk_decode.decode_to_wav(source, destination)
    assert sf.info(destination).frames == 48000
    original = destination.read_bytes()
    for frames in [0, 48001]:
        sf.write(source, np.zeros((frames, 2), np.float32), 48000)
        with pytest.raises(audio_io.AudioError, match="empty|longer"):
            disk_decode.decode_to_wav(source, destination)
        assert destination.read_bytes() == original
        assert not list(tmp_path.glob(".decode-*"))
    source.write_bytes(b"corrupt audio")
    with pytest.raises(audio_io.AudioError, match="decodable|Unsupported"):
        disk_decode.decode_to_wav(source, destination)
    assert destination.read_bytes() == original


def test_ffmpeg_timeout_and_error_cleanup(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((100, 2), np.float32), 48000)
    destination = tmp_path / "out.wav"
    destination.write_bytes(b"existing")
    monkeypatch.setattr(disk_decode, "_soundfile",
                        lambda *args: (_ for _ in ()).throw(disk_decode._ReadError()))
    with pytest.raises(audio_io.AudioError, match="timed out"):
        disk_decode.decode_to_wav(source, destination, timeout=0.000001)
    assert destination.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".decode-*"))

    monkeypatch.setattr(audio_io, "ffmpeg_exe", lambda: "/bin/false")
    with pytest.raises(audio_io.AudioError, match="Decoding failed"):
        disk_decode.decode_to_wav(source, destination)
    assert destination.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".decode-*"))


def test_hidden_duration_and_write_failure_cleanup(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((48001, 2), np.float32), 48000)
    monkeypatch.setattr(audio_io, "MAX_SECONDS", 1)
    monkeypatch.setattr(disk_decode, "_probe", lambda *args: {"duration": 0})
    destination = tmp_path / "out.wav"
    with pytest.raises(audio_io.AudioError, match="longer"):
        disk_decode.decode_to_wav(source, destination)
    assert not destination.exists() and not list(tmp_path.glob(".decode-*"))
    monkeypatch.setattr(audio_io, "MAX_SECONDS", 1200)
    monkeypatch.setattr(disk_decode, "_write",
                        lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        disk_decode.decode_to_wav(source, destination)
    assert not destination.exists() and not list(tmp_path.glob(".decode-*"))
