"""Disposable Linux acceptance runner with exact cgroup evidence, not a benchmark."""
import json
import resource
import sys
from pathlib import Path

import pytest

status = pytest.main(sys.argv[1:])
cgroup = Path("/sys/fs/cgroup")
evidence = {
    "exit_code": status,
    "memory_max": int((cgroup / "memory.max").read_text()),
    "memory_swap_max": int((cgroup / "memory.swap.max").read_text()),
    "memory_peak": int((cgroup / "memory.peak").read_text()),
    "memory_events": dict(line.split() for line in (cgroup / "memory.events").read_text().splitlines()),
    "process_peak_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    "scope": "Small synthetic durable-asset and overlapping-process acceptance; not full audio DSP or live Neon/Render.",
}
Path("/working/evidence.json").write_text(json.dumps(evidence, indent=2))
print(json.dumps(evidence), flush=True)
raise SystemExit(status)
