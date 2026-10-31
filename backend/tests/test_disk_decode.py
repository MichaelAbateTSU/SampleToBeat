"""Sample parity and failure/cleanup contracts for the standalone disk decoder."""
import os
import subprocess
import sys
import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from choplab import audio_io, disk_decode


def original_decode(path):
    """Frozen original libsndfile/SciPy path, independent of production decode."""
    y, rate = sf.read(path, dtype="float32", always_2d=True)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    y = np.ascontiguousarray(y[:, :2], dtype=np.float32)
    if rate != 48000:
        g = np.gcd(rate, 48000)
        y = resample_poly(y, 48000 // g, rate // g, axis=0).astype(np.float32)
    return np.nan_to_num(y) if not np.isfinite(y).all() else y


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
@pytest.mark.parametrize("channels", [1, 2, 4])
@pytest.mark.parametrize("frames", [1, 73, 65535, 65536, 65537, 150003])
def test_soundfile_sample_parity(tmp_path, rate, channels, frames):
    rng = np.random.default_rng(91)
    y = rng.uniform(-0.7, 0.7, (frames, channels)).astype(np.float32)
    source = tmp_path / "source.wav"
    sf.write(source, y, rate, subtype="FLOAT")
    expected = original_decode(source)
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
    expected = original_decode(source)
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
    np.testing.assert_array_equal(actual, original_decode(source))


@pytest.mark.parametrize("channels", [1, 2, 4])
def test_ffmpeg_fallback_parity(tmp_path, monkeypatch, channels):
    exe = audio_io.ffmpeg_exe()
    assert exe, "FFmpeg is required for fallback parity validation"
    source = tmp_path / "source.wav"
    sf.write(source, np.random.default_rng(2).normal(0, .1, (96003, channels)), 96000)
    compressed = tmp_path / "source.m4a"
    subprocess.run([exe, "-v", "error", "-y", "-i", str(source), "-c:a", "aac", str(compressed)],
                   check=True, timeout=30)
    reference = subprocess.run(
        [exe, "-v", "error", "-i", str(compressed), "-t", "1201",
         "-f", "f32le", "-ac", "2", "-ar", "48000", "-"],
        capture_output=True, check=True, timeout=180,
    )
    expected = np.frombuffer(reference.stdout, dtype=np.float32).reshape(-1, 2)
    expected = np.nan_to_num(expected) if not np.isfinite(expected).all() else expected
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


@pytest.mark.parametrize("rate", [44100, 96000])
def test_resampled_duration_boundary(tmp_path, monkeypatch, rate):
    monkeypatch.setattr(audio_io, "MAX_SECONDS", 1)
    source = tmp_path / "source.wav"
    sf.write(source, np.random.default_rng(3).normal(0, .1, (rate, 1)), rate, subtype="FLOAT")
    actual, _ = sf.read(disk_decode.decode_to_wav(source, tmp_path / "out.wav"),
                        dtype="float32", always_2d=True)
    np.testing.assert_array_equal(actual, original_decode(source))
    assert len(actual) == 48000
    sf.write(source, np.zeros((rate + 1, 1)), rate)
    with pytest.raises(audio_io.AudioError, match="longer"):
        disk_decode.decode_to_wav(source, tmp_path / "out.wav")


def test_fallback_unknown_duration_and_missing_decoder(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((48001, 2), np.float32), 48000)
    monkeypatch.setattr(audio_io, "MAX_SECONDS", 1)
    monkeypatch.setattr(disk_decode, "_probe", lambda *args: {"duration": 0})
    monkeypatch.setattr(disk_decode, "_soundfile",
                        lambda *args: (_ for _ in ()).throw(disk_decode._ReadError()))
    destination = tmp_path / "out.wav"
    with pytest.raises(audio_io.AudioError, match="longer"):
        disk_decode.decode_to_wav(source, destination)
    assert not destination.exists() and not list(tmp_path.glob(".decode-*"))
    monkeypatch.setattr(audio_io, "ffmpeg_exe", lambda: None)
    with pytest.raises(audio_io.AudioError, match="unavailable"):
        disk_decode.decode_to_wav(source, destination)
    assert not destination.exists() and not list(tmp_path.glob(".decode-*"))


@pytest.mark.skipif(os.name == "nt", reason="Synthetic executable requires POSIX shebang support")
def test_incomplete_pcm_and_probe_timeout(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((100, 2), np.float32), 48000)
    fake = tmp_path / "decoder"
    fake.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b'bad')\n")
    fake.chmod(0o700)
    monkeypatch.setattr(audio_io, "ffmpeg_exe", lambda: str(fake))
    monkeypatch.setattr(disk_decode, "_soundfile",
                        lambda *args: (_ for _ in ()).throw(disk_decode._ReadError()))
    destination = tmp_path / "out.wav"
    with pytest.raises(audio_io.AudioError, match="incomplete"):
        disk_decode.decode_to_wav(source, destination)
    assert not destination.exists() and not list(tmp_path.glob(".decode-*"))
    source.write_bytes(b"bad")
    fake.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n")
    with pytest.raises(audio_io.AudioError, match="probing timed out"):
        disk_decode.decode_to_wav(source, destination, timeout=.01)
    assert not destination.exists() and not list(tmp_path.glob(".decode-*"))


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

    monkeypatch.setattr(audio_io, "ffmpeg_exe", lambda: sys.executable)
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


@pytest.mark.parametrize("rate,frames", [
    (1, 1), (1, 19), (1, 20), (1, 21), (7, 241), (31, 1301),
    (47999, 73), (47999, 959980), (47999, 959981),
    (48001, 131079), (96001, 200003),
])
def test_expanding_and_coprime_parity(tmp_path, rate, frames):
    source = tmp_path / "source.wav"
    sf.write(source, np.random.default_rng(84).normal(0, .1, (frames, 2)).astype(np.float32),
             rate, subtype="FLOAT")
    actual, _ = sf.read(disk_decode.decode_to_wav(source, tmp_path / "out.wav"),
                        dtype="float32", always_2d=True)
    np.testing.assert_array_equal(actual, original_decode(source))


@pytest.mark.parametrize("rate,channels", [(1, 1024), (7, 128), (44100, 1024), (48000, 1024)])
def test_channel_reads_bounded_before_stereo_conversion(tmp_path, monkeypatch, rate, channels):
    frames = min(rate * 2, 3003)
    source = tmp_path / "source.wav"
    sf.write(source, np.random.default_rng(42).normal(0, .1, (frames, channels)),
             rate, subtype="FLOAT")
    expected = original_decode(source)
    read = sf.SoundFile.read
    reads = []

    def bounded_read(self, frames=-1, *args, **kwargs):
        if self.channels == channels:
            reads.append(frames)
            assert 0 < frames * channels * 4 <= disk_decode.READ_BYTES
        return read(self, frames, *args, **kwargs)

    monkeypatch.setattr(sf.SoundFile, "read", bounded_read)
    output = disk_decode.decode_to_wav(source, tmp_path / "out.wav")
    monkeypatch.setattr(sf.SoundFile, "read", read)
    actual, _ = sf.read(output, dtype="float32", always_2d=True)
    np.testing.assert_array_equal(actual, expected)
    assert reads


def test_coprime_filter_designed_once(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((959981, 2), np.float32), 47999, subtype="FLOAT")
    design = disk_decode.firwin
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return design(*args, **kwargs)

    monkeypatch.setattr(disk_decode, "firwin", counted)
    disk_decode.decode_to_wav(source, tmp_path / "out.wav")
    assert calls == [960001]


def test_pathological_fir_budget_explicit_error_and_cleanup(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((73, 2), np.float32), 1000003, subtype="FLOAT")
    destination = tmp_path / "out.wav"
    destination.write_bytes(b"existing")
    monkeypatch.setattr(disk_decode, "firwin", lambda *a, **kw: pytest.fail("allocated oversized FIR"))
    with pytest.raises(audio_io.AudioError, match="Exact resampling.*FIR/workspace budget"):
        disk_decode.decode_to_wav(source, destination)
    assert destination.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".decode-*"))


def test_truncated_read_cleanup(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((100, 2), np.float32), 48000)
    destination = tmp_path / "out.wav"
    destination.write_bytes(b"existing")
    read = sf.SoundFile.read

    def short(self, frames=-1, *args, **kwargs):
        return read(self, max(0, frames - 1), *args, **kwargs)

    monkeypatch.setattr(sf.SoundFile, "read", short)
    with pytest.raises(audio_io.AudioError, match="incomplete audio frames"):
        disk_decode.decode_to_wav(source, destination)
    assert destination.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".decode-*"))
