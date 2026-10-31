"""Bounded default HPSS workspaces with the original reflect-boundary median."""
from __future__ import annotations

import librosa
import numpy as np
from numba import njit
from scipy.ndimage import median_filter
from scipy.fft import irfft
from scipy.signal import get_window
from librosa.core.spectrum import __overlap_add as _overlap_add
from librosa.filters import __window_ss_fill as _window_fill


@njit(cache=True)
def _rolling_median(rows):
    out = np.empty_like(rows)
    for row in range(rows.shape[0]):
        window = np.sort(rows[row, :31])
        out[row, 0] = window[15]
        for col in range(1, out.shape[1] - 30):
            remove = np.searchsorted(window, rows[row, col - 1])
            for i in range(remove, 30):
                window[i] = window[i + 1]
            insert = np.searchsorted(window[:30], rows[row, col + 30])
            for i in range(30, insert, -1):
                window[i] = window[i - 1]
            window[insert] = rows[row, col + 30]
            out[row, col] = window[15]
    return out[:, :out.shape[1] - 30]


def _median(magnitude, axis):
    if not np.isfinite(magnitude).all():
        return median_filter(magnitude, size=(1, 31) if axis == 1 else (31, 1), mode="reflect")
    rows = magnitude if axis == 1 else magnitude.T
    padded = np.ascontiguousarray(np.pad(rows, ((0, 0), (15, 15)), mode="symmetric"))
    result = _rolling_median(padded)
    return result if axis == 1 else result.T


def spectra(spectrum, margin=1.0, both=False):
    magnitude, phase = librosa.magphase(spectrum)
    harmonic, percussive = _median(magnitude, 1), _median(magnitude, 0)
    mask = librosa.util.softmask(harmonic, percussive * margin, power=2.0, split_zeros=margin == 1)
    h = (magnitude * mask) * phase
    if not both:
        return h
    mask = librosa.util.softmask(percussive, harmonic * margin, power=2.0, split_zeros=margin == 1)
    return h, (magnitude * mask) * phase


def blocks(read, length, dtype, margin=1.0, both=False):
    n_fft, hop = 2048, 512
    frames = 1 + length // hop
    window = get_window("hann", n_fft, fftbins=True)
    squared = window ** 2
    tails = [np.empty(0, dtype=dtype) for _ in range(2 if both else 1)]
    norm_tail = np.empty(0, dtype=dtype)
    for start in range(0, frames, 256):
        end = min(start + 256, frames)
        a, b = max(0, start - 15), min(frames, end + 15)
        lo, hi = a * hop - n_fft // 2, (b - 1) * hop + n_fft // 2
        audio = read(max(0, lo), min(length, hi))
        audio = np.pad(audio, (max(0, -lo), max(0, hi - length)))
        spectrum = librosa.stft(audio, n_fft=n_fft, hop_length=hop, center=False)
        separated = spectra(spectrum, margin, both)
        separated = separated if both else (separated,)
        size = (end - start - 1) * hop + n_fft
        norm = np.zeros(size, dtype=dtype)
        norm[:len(norm_tail)] = norm_tail
        _window_fill(norm, squared, end - start, hop)
        emit = (end - start) * hop if end < frames else size
        left = max(0, n_fft // 2 - start * hop)
        right = min(emit, length + n_fft // 2 - start * hop)
        mask = norm[left:right] > librosa.util.tiny(norm)
        results = []
        for component, tail in zip(separated, tails):
            output = np.zeros(size, dtype=dtype)
            output[:len(tail)] = tail
            inverse = window[:, None] * irfft(component[:, start - a:end - a], n=n_fft, axis=0)
            _overlap_add(output, inverse, hop)
            results.append(output)
        tails = [output[emit:].copy() for output in results]
        norm_tail = norm[emit:].copy()
        chunks = [output[left:right] for output in results]
        for chunk in chunks:
            chunk[mask] /= norm[left:right][mask]
        yield tuple(chunks) if both else chunks[0]
