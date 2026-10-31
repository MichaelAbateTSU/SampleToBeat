"""Exact filesystem availability reporting for owner-protected capability checks."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def _measure(path: Path) -> tuple[int | None, int | None, str | None]:
    try:
        free = shutil.disk_usage(path).free
        device = os.stat(path).st_dev
    except OSError:
        return None, None, "unavailable"
    if not isinstance(free, int) or free < 0:
        return None, None, "invalid"
    return free, device, None


def storage_capacity(data_dir: Path) -> dict:
    """Return bytes available to this process's filesystem view, never paths or OS error text.

    Equal device ids mean the two paths share a filesystem id; unequal ids do not prove independent backing storage.
    """
    data_free, data_device, data_error = _measure(Path(data_dir))
    temp_free, temp_device, temp_error = _measure(Path(tempfile.gettempdir()))
    same = None if data_device is None or temp_device is None else data_device == temp_device
    return {"data_free_bytes": data_free, "data_free_error": data_error,
            "temp_free_bytes": temp_free, "temp_free_error": temp_error,
            "data_temp_same_filesystem": same}
