"""Host-side bounded original-duration helper measurement, without API/analysis."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir()
out.chmod(0o777)
name = "choplab-disk-decode"
image = "choplab:disk-decode"
subprocess.run(["docker", "run", "--rm", "-v", f"{out}:/evidence", image,
                "/checks/check_disk_decode.py", "fixture"], check=True, timeout=90)
argv = ["docker", "run", "-d", "--name", name, "--cpus=0.1", "--memory=512m", "--memory-swap=512m",
        "-e", "CHOPLAB_MAX_AUDIO_SECONDS=1200", "-v", f"{out}:/evidence", image,
        "/checks/check_disk_decode.py", "check"]
subprocess.run(argv, check=True, timeout=30)
start = time.monotonic()
samples = []
group = None
snapshot = None
try:
    while time.monotonic() - start < 900:
        if group is None or int((time.monotonic() - start) * 10) % 10 == 0:
            snapshot = json.loads(subprocess.check_output(["docker", "inspect", name], timeout=15))[0]
            if not snapshot["State"]["Running"]:
                break
            if group is None:
                pid = snapshot["State"]["Pid"]
                cgroup = next(line for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                              if line.startswith("0::")).split("::", 1)[1]
                group = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
                assert (group / "cpu.max").read_text().strip() == "10000 100000"
                assert (group / "memory.max").read_text().strip() == "536870912"
                assert (group / "memory.swap.max").read_text().strip() == "0"
        try:
            stat = dict(line.split() for line in (group / "memory.stat").read_text().splitlines())
            samples.append({
                "seconds": time.monotonic() - start,
                "current": int((group / "memory.current").read_text()),
                "peak": int((group / "memory.peak").read_text()),
                "anon": int(stat["anon"]), "file": int(stat["file"]),
                "events": dict(line.split() for line in (group / "memory.events").read_text().splitlines()),
            })
        except FileNotFoundError:
            pass
        time.sleep(.1)
    else:
        raise TimeoutError("Helper/parity exceeded 900 seconds")
finally:
    snapshot = json.loads(subprocess.check_output(["docker", "inspect", name], timeout=15))[0]
    (out / "inspect.json").write_text(json.dumps(snapshot, indent=2))
    (out / "samples.jsonl").write_text("".join(json.dumps(s) + "\n" for s in samples))
    (out / "result.json").write_text(json.dumps({
        "docker_argv": argv, "elapsed_seconds": time.monotonic() - start,
        "sampled_peak": max((s["current"] for s in samples), default=0),
        "lifetime_peak": max((s["peak"] for s in samples), default=0),
        "peak_anon": max((s["anon"] for s in samples), default=0),
        "peak_file": max((s["file"] for s in samples), default=0),
        "state": snapshot["State"], "parity_evidence_exists": (out / "parity.json").exists(),
    }, indent=2))
    (out / "container.log").write_bytes(subprocess.check_output(
        ["docker", "logs", name], stderr=subprocess.STDOUT, timeout=15))
    subprocess.run(["docker", "rm", "-f", name], check=True, timeout=30)
assert snapshot["State"]["ExitCode"] == 0 and not snapshot["State"]["OOMKilled"]
assert (out / "parity.json").exists()
