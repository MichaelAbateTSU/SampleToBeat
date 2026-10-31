"""Return unused native allocator arenas on glibc; never discard live DSP buffers."""
from __future__ import annotations

import ctypes
import sys

_trim = getattr(ctypes.CDLL(None), "malloc_trim", None) if sys.platform == "linux" else None
if _trim is not None:
    _trim.argtypes = [ctypes.c_size_t]
    _trim.restype = ctypes.c_int


def release_unused() -> bool:
    return bool(_trim(0)) if _trim is not None else False
