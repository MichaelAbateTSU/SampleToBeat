"""Read-only sampled working-disk evidence; no provider access or audio reads."""
from pathlib import Path
import os
import stat


def sample_disk(pid: int, proc_root: Path = Path("/proc")) -> dict:
    root = proc_root / str(pid) / "root"
    errors = []

    def named(directory):
        if not directory.is_dir():
            errors.append("Working directory unavailable")
            return None
        apparent, allocated, files = 0, 0, 0
        for parent, _, names in os.walk(directory, onerror=lambda error: errors.append(type(error).__name__)):
            for name in names:
                try:
                    info = (Path(parent) / name).stat(follow_symlinks=False)
                except OSError as error:
                    errors.append(type(error).__name__)
                    continue
                if stat.S_ISREG(info.st_mode):
                    apparent += info.st_size
                    allocated = allocated + info.st_blocks * 512 if hasattr(info, "st_blocks") and allocated is not None else None
                    files += 1
        return {"apparent_bytes": apparent, "allocated_bytes": allocated, "files": files}

    data, temporary = named(root / "data"), named(root / "tmp")
    unlinked_bytes, unlinked_allocated, seen = 0, 0, set()
    try:
        descriptors = list((proc_root / str(pid) / "fd").iterdir())
    except OSError as error:
        errors.append(type(error).__name__)
        descriptors = []
    for descriptor in descriptors:
        try:
            info = descriptor.stat()
        except OSError as error:
            errors.append(type(error).__name__)
            continue
        identity = (info.st_dev, info.st_ino)
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 0 and identity not in seen:
            seen.add(identity)
            unlinked_bytes += info.st_size
            unlinked_allocated = unlinked_allocated + info.st_blocks * 512 if hasattr(info, "st_blocks") and unlinked_allocated is not None else None
    allocated = None
    if data is not None and temporary is not None:
        values = [data["allocated_bytes"], temporary["allocated_bytes"], unlinked_allocated]
        if all(value is not None for value in values):
            allocated = sum(values)
    return {"outcome": "partial" if errors else "sampled", "data": data, "tmp": temporary,
            "unlinked_regular_bytes": unlinked_bytes, "unlinked_allocated_bytes": unlinked_allocated,
            "observed_allocated_bytes": allocated, "errors": errors,
            "scope": "Sampled app data/tmp and parent-process unlinked FDs; may miss short-lived files or child-only FDs."}
