"""Default seven-octave CQT with bounded STFT projection, not chunk-local resampling."""
from __future__ import annotations

import librosa
import numpy as np
from librosa.core import constantq
from . import memory


def chroma(y, sr, hop, tuning):
    bins, octaves = 36, 7
    dtype = librosa.util.dtype_r2c(y.dtype)
    fmin = librosa.note_to_hz("C1") * 2 ** (tuning / bins)
    frequencies = librosa.interval_frequencies(n_bins=bins * octaves, fmin=fmin,
                                               intervals="equal", bins_per_octave=bins, sort=True)
    alpha = librosa.filters._relative_bandwidth(freqs=frequencies)
    _, cutoff = librosa.filters.wavelet_lengths(freqs=frequencies, sr=sr, window="hann",
                                                filter_scale=1, gamma=0, alpha=alpha)
    y, sr, hop = constantq.__early_downsample(y, sr, hop, "soxr_hq", octaves, sr / 2, cutoff, True)
    audio, rate, stride = y, sr, hop
    frames = 1 + len(y) // hop
    response = memory.empty((bins * octaves, frames), dtype, order="F")
    for octave in range(octaves):
        band = slice(-bins * (octave + 1), -bins * octave if octave else None)
        basis, n_fft, _ = constantq.__vqt_filter_fft(rate, frequencies[band], 1, 1, 0.01,
                                                    window="hann", gamma=0, dtype=dtype, alpha=alpha[band])
        basis[:] *= np.sqrt(sr / rate)
        count = 1 + len(audio) // stride
        frames = min(frames, count)
        for start in range(0, frames, 256):
            end = min(start + 256, frames)
            lo, hi = start * stride - n_fft // 2, (end - 1) * stride + n_fft // 2
            block = audio[max(0, lo):min(len(audio), hi)]
            block = np.pad(block, (max(0, -lo), max(0, hi - len(audio))))
            spectrum = librosa.stft(block, n_fft=n_fft, hop_length=stride, window="ones",
                                    center=False, dtype=dtype)
            response[band, start:end] = basis.dot(spectrum)
            memory.release_pages(audio)
            memory.release_pages(response)
        if octave < octaves - 1 and stride % 2 == 0 and frequencies[band.start - 1] <= rate / 5:
            previous = audio
            audio = librosa.resample(previous, orig_sr=2, target_sr=1, res_type="soxr_hq", scale=True)
            memory.release_pages(previous)
            rate /= 2
            stride //= 2
    response = response[:, :frames]
    lengths, _ = librosa.filters.wavelet_lengths(freqs=frequencies, sr=sr, window="hann",
                                                filter_scale=1, gamma=0, alpha=alpha)
    response /= np.sqrt(lengths[:, None])
    memory.release_pages(response)
    return librosa.feature.chroma_cqt(C=np.abs(response), sr=sr, hop_length=hop)
