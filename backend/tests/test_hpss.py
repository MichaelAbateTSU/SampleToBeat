import librosa
import numpy as np
import pytest
import soundfile as sf

from choplab import audio_io, hpss, separation


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("shape", [(3, 4), (35, 57), (1025, 286)])
def test_rolling_reflect_medians_and_masks_are_exact(dtype, shape):
    spectrum = np.random.default_rng(6).normal(size=shape).astype(dtype)
    spectrum[:2] = 0
    for margin in (1.0, 2.0):
        actual = hpss.spectra(spectrum.astype(np.complex64 if dtype == np.float32 else np.complex128),
                              margin=margin, both=True)
        expected = librosa.decompose.hpss(spectrum.astype(actual[0].dtype), margin=margin)
        for a, b in zip(actual, expected):
            np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("length", [137, 2048, 275777])
def test_streamed_two_stem_centered_istft_is_exact(length):
    mono = np.random.default_rng(7).normal(0, 0.1, length).astype(np.float32)
    mono[:100] = 0
    mono[-100:] = 0
    actual = list(hpss.blocks(lambda a, b: mono[a:b], length, mono.dtype, margin=2, both=True))
    expected = librosa.decompose.hpss(librosa.stft(mono), margin=2)
    bounded = hpss.spectra(librosa.stft(mono), margin=2, both=True)
    assert all(a.flags.f_contiguous == b.flags.f_contiguous for a, b in zip(bounded, expected))
    for channel in range(2):
        np.testing.assert_array_equal(np.concatenate([pair[channel] for pair in actual]),
                                      librosa.istft(expected[channel], length=length))


@pytest.mark.parametrize("sr", [48000, 24000])
def test_adapter_streams_exact_original_stereo_outputs(tmp_path, sr):
    y = np.random.default_rng(8).normal(0, 0.1, (275777, 2)).astype(np.float32)
    source = audio_io.write_wav(tmp_path / "source.wav", y, sr=sr)
    y = audio_io.load(source)
    output = separation.HPSSAdapter().separate(source, tmp_path / "stems", lambda *args: None)
    for i in range(2):
        reference = librosa.decompose.hpss(librosa.stft(y[:, i]), margin=2)
        for j, name in enumerate(("harmonic", "percussive")):
            saved, _ = sf.read(output[name], dtype="float32", always_2d=True)
            np.testing.assert_array_equal(saved[:, i], librosa.istft(reference[j], length=len(y)))
