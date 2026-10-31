import gc
import os
import weakref
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from choplab import analysis, audio_io, core, memory


def test_large_private_array_keeps_file_until_last_view(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    array = memory.empty((1100000, 2), np.float32)
    assert isinstance(array, np.memmap)
    path = array.filename
    if os.name != "nt":
        assert os.stat(path).st_mode & 0o077 == 0
    array[:3] = [[1, 2], [3, 4], [5, 6]]
    view = np.asarray(array)[1:3]
    reference = weakref.ref(array)
    memory.release_pages(view)
    del array
    gc.collect()
    assert reference() is not None and os.path.exists(path)
    np.testing.assert_array_equal(view, [[3, 4], [5, 6]])
    del view
    gc.collect()
    assert reference() is None and not os.path.exists(path)


def test_disk_reserve_failure_removes_only_own_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setenv("CHOPLAB_MIN_FREE_MB", "0")
    monkeypatch.setattr(memory.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(OSError, match="reserve"):
        memory.empty((1100000, 2), np.float32)
    assert not list((tmp_path / ".scratch").iterdir())


def test_large_load_uses_only_bounded_soundfile_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    source = tmp_path / "source.wav"
    expected = np.random.default_rng(71).normal(0, .1, (1100037, 2)).astype(np.float32)
    sf.write(source, expected, core.SR, subtype="FLOAT")
    read = sf.SoundFile.read
    counts = []

    def bounded(file, frames=-1, *args, **kwargs):
        assert 0 <= frames <= 65536
        counts.append(frames)
        return read(file, frames, *args, **kwargs)

    monkeypatch.setattr(sf.SoundFile, "read", bounded)
    actual = audio_io.load(source)
    assert isinstance(actual, np.memmap) and len(counts) > 1
    path = actual.filename
    np.testing.assert_array_equal(actual, expected)
    del actual
    gc.collect()
    assert not os.path.exists(path)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_disk_backed_mean_square_preserves_global_reduction(tmp_path, monkeypatch, dtype):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    mono = np.random.default_rng(72).normal(0, .1, 2100037).astype(dtype)
    assert analysis._mean_square(mono) == np.mean(mono ** 2)
    gc.collect()
    assert not list((tmp_path / ".scratch").iterdir())


def test_fortran_disk_array_keeps_cqt_layout_and_values(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    array = memory.empty((252, 9001), np.complex64, order="F")
    assert isinstance(array, np.memmap) and array.flags.f_contiguous
    array[:] = 1 + 2j
    memory.release_pages(array)
    np.testing.assert_array_equal(array, np.full(array.shape, 1 + 2j, np.complex64))
    del array
    gc.collect()
    assert not list((tmp_path / ".scratch").iterdir())


@pytest.mark.parametrize("subtype", ["FLOAT", "PCM_16", "PCM_24"])
def test_streamed_wav_write_matches_one_shot_pcm(tmp_path, subtype):
    audio = np.random.default_rng(73).normal(0, .1, (150003, 2)).astype(np.float32)
    reference = tmp_path / "reference.wav"
    destination = tmp_path / "streamed.wav"
    sf.write(reference, audio, core.SR, subtype=subtype)
    audio_io.write_wav(destination, audio, subtype=subtype)
    expected, rate = sf.read(reference, dtype="float32", always_2d=True)
    actual, actual_rate = sf.read(destination, dtype="float32", always_2d=True)
    assert actual_rate == rate
    np.testing.assert_array_equal(actual, expected)


def test_region_load_remains_file_backed_and_exact(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    audio = np.random.default_rng(74).normal(0, .1, (1100037, 2)).astype(np.float32)
    source = tmp_path / "source.wav"
    sf.write(source, audio, core.SR, subtype="FLOAT")
    region = audio_io.load_region(source, 17, len(audio) - 7)
    assert isinstance(region, np.memmap)
    np.testing.assert_array_equal(region, audio[17:-7])
    del region
    gc.collect()
    assert not list((tmp_path / ".scratch").iterdir())


def test_multichannel_loader_bounds_input_allocation(tmp_path, monkeypatch):
    audio = np.random.default_rng(75).normal(0, .1, (4103, 256)).astype(np.float32)
    source = tmp_path / "channels.wav"
    sf.write(source, audio, core.SR, subtype="FLOAT")
    read = sf.SoundFile.read
    counts = []

    def bounded(file, frames=-1, *args, **kwargs):
        assert 0 <= frames * file.channels * 4 <= 1 << 20
        counts.append(frames)
        return read(file, frames, *args, **kwargs)

    monkeypatch.setattr(sf.SoundFile, "read", bounded)
    np.testing.assert_array_equal(audio_io.load(source), audio[:, :2])
    assert len(counts) > 1
