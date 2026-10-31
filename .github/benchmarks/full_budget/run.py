"""Linux host orchestrator: app cgroup excludes clients, DB and observers."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave

from budget_probe import disk_bytes, summarize

SHA = "2057063a6515a4111624b9e89678f8c4853867b7399086a0138dd52817f0c03b"


def execute(args, *, timeout=120, check=True, stdout=None):
    result = subprocess.run(args, timeout=timeout, check=False,
                            stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and result.returncode:
        raise RuntimeError("Synthetic subprocess failed: " + Path(args[0]).name)
    return result


def events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def state(name):
    raw = json.loads(execute(["docker", "inspect", name]).stdout)[0]
    config = raw["HostConfig"]
    return {"state": {k: raw["State"][k] for k in ("Running", "OOMKilled", "ExitCode", "Pid")},
            "limits": {k: config[k] for k in ("Memory", "MemorySwap", "NanoCpus")},
            "healthcheck": raw["Config"].get("Healthcheck")}


def generate(root, mode):
    sys.path.insert(0, str(root / "generator/tools/valid-audio-preflight"))
    import combined_fixture as C
    import fixture as F
    output = root / "fixture/input.wav"
    if mode == "combined":
        data = C.write(output)
        assert data["actual_bytes"] == 1 << 30 and data["sha256"] == SHA
        expected = json.loads((Path(__file__).parent / "combined-wav-manifest.json").read_text())
    else:
        frames = 12 * C.RATE
        data = F.write(output, frames, iterator=lambda n: C.chunks(n, padding=0),
                       description=C.describe(frames, padding=0))
        expected = {"format": "choplab-independent-wav-1", "verified": True,
                    "size": data["actual_bytes"], "sha256": data["sha256"],
                    "frames": frames, "sample_rate": C.RATE, "channels": 2, "sample_width": 4}
    with wave.open(str(output), "rb") as wav:
        assert (wav.getnframes(), wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (
            expected["frames"], expected["sample_rate"], expected["channels"], expected["sample_width"])
    assert output.stat().st_size == expected["size"]
    (root / "fixture/fixture-manifest.json").write_text(json.dumps(expected))
    return {**data, "independent_manifest": expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--app-sha", required=True)
    parser.add_argument("--mode", choices=("control", "combined"), default="control")
    parser.add_argument("--confirm-final-combined-sha")
    args = parser.parse_args()
    if len(args.app_sha) != 40 or any(c not in "0123456789abcdef" for c in args.app_sha):
        parser.error("Exact full app SHA required")
    if args.mode == "combined" and args.confirm_final_combined_sha != args.app_sha:
        parser.error("Combined run requires explicit final SHA confirmation; no automatic promotion")
    root = args.root.resolve()
    assert root.parent == Path(os.environ["RUNNER_TEMP"]).resolve() and root.name == "combined-budget"
    for name in ("config", "cache0", "cache1", "temp0", "temp1", "evidence",
                 "app-evidence", "acceptance", "fixture"):
        path = root / name
        path.mkdir(exist_ok=True)
        os.chown(path, 10001, 10001)
        path.chmod(0o770)
    evidence = root / "evidence"
    report = {"format": "choplab-separated-budget-1", "app_sha": args.app_sha,
              "instrumentation_sha": "650c290f242d100d8f4479d9272c0340889a65c0",
              "generator_sha": "f6d7a9d6e8cab041415b538c5d81111a5712eb08",
              "mode": args.mode, "success": False, "phases": [], "app_containers": [],
              "limitations": [
                  "App cgroup includes installed app plus in-process S3 ledger wrapper, but no client/admin/sampler/DB/MinIO.",
                  "Instrumentation adds bounded wrapper/log work to app; not an uninstrumented production performance proof.",
                  "100ms anon/file/disk samples miss between-sample and open-unlinked/outside-cache transients.",
                  "Payload consumed or buffered/declared attempts are not acknowledged wire bytes or provider metering.",
                  "Synthetic inventory/deletion/forced expired lease do not establish Neon retention or provider replacement.",
                  "Journey is import/analysis, one-bar generation, studio preview, WAV16+project export and selected exact downloads.",
                  "HPSS, eight-bar maximum arrangements, MP3 and every library asset are not exercised by this journey.",
                  "Health observer requests add inbound synthetic test traffic; not idle/sleep/quota proof.",
              ]}
    app = None
    started = time.monotonic()
    stop = threading.Event()
    current = {"name": None, "cache": None, "temp": None, "cgroup": None}
    lock = threading.Lock()
    common = ["--network", "durable-acceptance", "--no-healthcheck", "--read-only",
              "--env-file", str(root / "synthetic.env"),
              "-v", f"{root / 'certs'}:/certs:ro",
              "-v", f"{Path(__file__).parent.resolve()}:/probe:ro",
              "-v", f"{root / 'app-evidence'}:/evidence",
              "--entrypoint", "python"]

    def admin(action, *, cache="cache0"):
        extra = ["-v", f"{root / cache}:/data", "-v", f"{root / 'acceptance'}:/acceptance:ro",
                 "-v", f"{root / 'fixture'}:/fixture:ro",
                 "-v", f"{root / 'config'}:/config" + ("" if action == "initialize" else ":ro"),
                 "--tmpfs", "/tmp:size=32m,uid=10001,gid=10001"]
        return execute(["docker", "run", "--rm", *common, *extra,
                        "choplab:budget", "/probe/admin.py", action], timeout=1800)

    def sample():
        with (evidence / "resource-samples.jsonl").open("w") as out:
            while not stop.is_set():
                with lock:
                    info = dict(current)
                data = {"elapsed_seconds": time.monotonic() - started, "app": info["name"]}
                if info["cache"]:
                    data["disk"] = disk_bytes([info["cache"], info["temp"]])
                group = info["cgroup"]
                if group:
                    metrics = {}
                    for key in ("cpu.max", "cpu.stat", "memory.current", "memory.peak", "memory.max",
                                "memory.swap.max", "memory.events", "memory.stat"):
                        try:
                            raw = (group / key).read_text().strip()
                            metrics[key] = (dict(line.split() for line in raw.splitlines())
                                            if key in ("cpu.stat", "memory.events", "memory.stat") else raw)
                        except FileNotFoundError:
                            metrics[key] = "cgroup_gone"
                    data["cgroup"] = metrics
                out.write(json.dumps(data) + "\n")
                out.flush()
                stop.wait(.1)

    def health():
        with (evidence / "health-observer.jsonl").open("w") as out:
            while not stop.is_set():
                begin = time.monotonic()
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=2) as response:
                        count = len(response.read(65536))
                        status = response.status
                except urllib.error.HTTPError as error:
                    status, count = error.code, len(error.read(65536))
                except (OSError, TimeoutError):
                    status, count = 0, 0
                out.write(json.dumps({"elapsed_seconds": begin - started, "status": status,
                                      "body_bytes": count, "seconds": time.monotonic() - begin}) + "\n")
                out.flush()
                stop.wait(1)

    observer = threading.Thread(target=sample, daemon=True)
    health_thread = threading.Thread(target=health, daemon=True)
    observer.start()
    health_thread.start()

    def start(index):
        nonlocal app
        app = f"combined-budget-app-{index}"
        cache, temp = root / f"cache{index}", root / f"temp{index}"
        execute(["docker", "create", "--name", app, *common,
                 "--memory=512m", "--memory-swap=512m", "--cpus=0.1",
                 "-v", f"{root / 'config'}:/config:ro",
                 "-v", f"{cache}:/data", "-v", f"{temp}:/scratch",
                 "-e", "TMPDIR=/scratch", "-e", "NUMBA_CACHE_DIR=/scratch/numba",
                 "-p", "127.0.0.1:8765:8765", "choplab:budget", "/probe/app_probe.py"])
        execute(["docker", "start", app])
        initial = state(app)
        assert initial["limits"] == {"Memory": 536870912, "MemorySwap": 536870912, "NanoCpus": 100000000}
        assert initial["healthcheck"]["Test"] == ["NONE"]
        pid = initial["state"]["Pid"]
        line = next(line for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines() if line.startswith("0::"))
        group = Path("/sys/fs/cgroup") / line[3:].lstrip("/")
        assert (group / "memory.max").read_text().strip() == "536870912"
        assert (group / "memory.swap.max").read_text().strip() == "0"
        assert (group / "cpu.max").read_text().strip() == "10000 100000"
        with lock:
            current.update(name=app, cache=cache, temp=temp, cgroup=group)
        report["app_containers"].append({"name": app, "initial": initial})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if not state(app)["state"]["Running"]:
                raise RuntimeError("App stopped during startup")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except (OSError, TimeoutError):
                pass
            time.sleep(1)
        raise TimeoutError("App startup deadline")

    def finish_app():
        if app:
            # Last running sample retains kernel peak/events before cgroup disappears.
            report["app_containers"][-1]["before_stop"] = state(app)
            execute(["docker", "kill", app], check=False)
            report["app_containers"][-1]["after_stop"] = state(app)

    def snapshot(name):
        entry = {"name": name, "elapsed_seconds": time.monotonic() - started}
        try:
            entry["inventory"] = json.loads(admin("inventory", cache="cache1" if "replacement" in name else "cache0").stdout)
        except Exception as error:
            entry["inventory_failed"] = type(error).__name__
        entry["application_object_traffic"] = summarize(events(root / "app-evidence/object-events.jsonl"))
        http = events(evidence / "client-events.jsonl")
        entry["client_response_body_bytes"] = sum(e.get("bytes", 0) for e in http if e["category"] == "http_download")
        entry["client_upload_bytes_yielded_not_acknowledged"] = sum(e.get("bytes", 0) for e in http if e["category"] == "http_upload_yield")
        report["phases"].append(entry)

    def client(phase):
        begin = time.monotonic()
        with (evidence / f"{phase}-client.log").open("w") as out:
            result = execute([sys.executable, str(Path(__file__).with_name("client.py")),
                              str(root), phase, args.mode], timeout=5400, stdout=out, check=False)
        report[phase] = {"exit_code": result.returncode, "seconds": time.monotonic() - begin}
        if result.returncode:
            raise RuntimeError("External client failed; inspect sanitized phase log, no retry")

    try:
        report["fixture"] = generate(root, args.mode)
        admin("initialize")
        start(0)
        client("prepare")
        snapshot("prepared")
        finish_app()
        execute(["docker", "rm", app])
        app = None
        # Only exact owned cache0/temp0 are destroyed; DB/object storage survives.
        import shutil
        for name in ("cache0", "temp0"):
            shutil.rmtree(root / name)
        report["cold_cache_destroyed"] = True
        admin("expire")
        start(1)
        client("verify")
        snapshot("verified-after-cold-replacement")
        begin = time.monotonic()
        admin("original", cache="cache1")
        report["optional_original_read"] = {"seconds": time.monotonic() - begin,
                                          "traffic": summarize(events(root / "app-evidence/admin-object-events.jsonl")),
                                          "excluded_from_app_cgroup_and_app_payload": True}
        report["success"] = True
    except Exception as error:
        report["failure"] = {"class": type(error).__name__,
                             "message": "Synthetic run stopped; no mutation retry or provider success inferred."}
        snapshot("failed")
    finally:
        if app:
            finish_app()
        stop.set()
        observer.join(timeout=5)
        health_thread.join(timeout=5)
        report["elapsed_seconds"] = time.monotonic() - started
        for name in ("object-events.jsonl", "admin-object-events.jsonl"):
            source = root / "app-evidence" / name
            if source.exists():
                import shutil
                shutil.copyfile(source, evidence / name)
        report["app_object_traffic"] = summarize(events(root / "app-evidence/object-events.jsonl"))
        (evidence / "budget-report.json").write_text(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
