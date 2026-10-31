"""Return unused native allocator arenas on glibc; never discard live DSP buffers."""
from __future__ import annotations

import ctypes
import mmap
import os
import shutil
import sys
import tempfile
import weakref
from pathlib import Path

import numpy as np
from numpy.typing import DTypeLike

_trim = getattr(ctypes.CDLL(None), "malloc_trim", None) if sys.platform == "linux" else None
if _trim is not None:
    _trim.argtypes = [ctypes.c_size_t]
    _trim.restype = ctypes.c_int


def release_unused() -> bool:
    return bool(_trim(0)) if _trim is not None else False


def require_space(directory: Path, size: int = 0) -> None:
    reserve = int(os.environ.get("CHOPLAB_MIN_FREE_MB",
                                "2048" if os.environ.get("CHOPLAB_MODE") == "hosted" else "0")) * (1 << 20)
    if reserve < 0:
        raise ValueError("Free-space reserve must be nonnegative")
    if shutil.disk_usage(directory).free < reserve + size:
        raise OSError("Insufficient working disk space; the configured free-space reserve must remain available.")


def private_file(suffix: str = ".pcm") -> Path:
    from . import core

    directory = core.DATA_DIR / ".scratch"
    directory.mkdir(parents=True, exist_ok=True)
    require_space(directory)
    fd, name = tempfile.mkstemp(suffix=suffix, dir=directory)
    os.close(fd)
    return Path(name)


def own_file(array: np.memmap, path: Path) -> np.memmap:
    def close(mapping, filename):
        mapping.close()
        filename.unlink(missing_ok=True)
    weakref.finalize(array, close, array._mmap, path)
    return array


def empty(shape: int | tuple[int, ...], dtype: DTypeLike, *, order: str = "C") -> np.ndarray:
    size = int(np.prod(shape)) * np.dtype(dtype).itemsize
    if size <= 8 << 20:
        return np.empty(shape, dtype=dtype, order=order)
    path = private_file()
    array = None
    try:
        require_space(path.parent, size)
        array = np.memmap(path, dtype=dtype, mode="w+", shape=shape, order=order)
        if hasattr(mmap, "MADV_RANDOM"):
            array._mmap.madvise(mmap.MADV_RANDOM)
        return own_file(array, path)
    except BaseException:
        if array is not None:
            array._mmap.close()
        path.unlink(missing_ok=True)
        raise


def release_pages(array: np.ndarray) -> None:
    while not isinstance(array, np.memmap) and isinstance(array.base, np.ndarray):
        array = array.base
    if not isinstance(array, np.memmap) or array._mmap is None:
        return
    if array.mode != "r":
        array.flush()
    if hasattr(mmap, "MADV_DONTNEED"):
        array._mmap.madvise(mmap.MADV_DONTNEED)
    if hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED"):
        with open(array.filename, "rb") as file:
            os.fsync(file.fileno())
            os.posix_fadvise(file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
