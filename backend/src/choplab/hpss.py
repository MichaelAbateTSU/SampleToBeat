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
    if spectrum.flags.f_contiguous:
        h = np.asfortranarray(h)
    if not both:
        return h
    mask = librosa.util.softmask(percussive, harmonic * margin, power=2.0, split_zeros=margin == 1)
    p = (magnitude * mask) * phase
    if spectrum.flags.f_contiguous:
        p = np.asfortranarray(p)
    return h, p


def blocks(read, length, dtype, margin=1.0, both=False):
    n_fft, hop = 2048, 512
    frames = 1 + length // hop
    window = get_window("hann", n_fft, fftbins=True)
    squared = window ** 2
    tails = [np.empty(0, dtype=dtype) for _ in range(2 if both else 1)]
    norm_tail = np.empty(0, dtype=dtype)
    columns = max(1, librosa.util.MAX_MEM_BLOCK // (1025 * np.dtype(librosa.util.dtype_r2c(dtype)).itemsize))
    starts = [0, *range(2 + columns * 8, frames, columns * 8)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else frames
        # Align the library's centered forward FFT batches, keeping artificial local padding outside the halo.
        a = max(0, (start - 17) // 32 * 32)
        audio = read(a * hop, min(length, (end + 47) * hop))
        spectrum = librosa.stft(audio, n_fft=n_fft, hop_length=hop)
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
            positions = [0]
            if not start and end > 2:
                positions.append(2)
            positions.extend(range(positions[-1] + columns, end - start, columns))
            for j, position in enumerate(positions):
                stop = positions[j + 1] if j + 1 < len(positions) else end - start
                if stop == position:
                    continue
                inverse = window[:, None] * irfft(
                    component[:, start - a + position:start - a + stop], n=n_fft, axis=0)
                _overlap_add(output[position * hop:], inverse, hop)
            results.append(output)
        tails = [output[emit:].copy() for output in results]
        norm_tail = norm[emit:].copy()
        chunks = [output[left:right] for output in results]
        for chunk in chunks:
            chunk[mask] /= norm[left:right][mask]
        yield tuple(chunks) if both else chunks[0]
