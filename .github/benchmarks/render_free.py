"""Disposable Linux Docker experiment; no application changes or real credentials."""
import base64
import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

IMAGE = "choplab:render-free"
REVISION = "4d62d58a6ba64f2f8efe642777ba3c057fcc8e64"
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
APPROXIMATE_E2_MICRO = "--approximate-e2-micro" in sys.argv[2:]
CPU = 0.25 if APPROXIMATE_E2_MICRO else 0.1
MEMORY = 1073741824 if APPROXIMATE_E2_MICRO else 536870912
AUTH = base64.b64encode(b"owner:workflow-test-password").decode()
RESULTS = {
    "revision": REVISION,
    "limits": {"cpu": CPU, "memory_bytes": MEMORY, "swap_bytes": 0},
    "environment": "GitHub Actions Ubuntu 24.04 Linux Docker; not a provider deployment",
    "approximate_e2_micro": APPROXIMATE_E2_MICRO,
    "storage": "Disposable Docker volume; NOT proof of provider persistence",
    "cases": [],
}


def command(*args):
    return subprocess.check_output(args, text=True, timeout=60).strip()


def save():
    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=2))


def state(name):
    return json.loads(command("docker", "inspect", name))[0]


def request(method, path, body=None, *, raw=None, content_type=None, timeout=30):
    headers = {"Authorization": f"Basic {AUTH}", "X-Forwarded-Proto": "https"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(
        "http://127.0.0.1:8765" + path,
        data=raw if raw is not None else json.dumps(body).encode() if body is not None else None,
        headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}: {error.read().decode(errors='replace')}") from error


def api(method, path, body=None, timeout=30):
    return json.loads(request(method, path, body, timeout=timeout))


def wait_job(job, deadline):
    while time.monotonic() < deadline:
        result = api("GET", f"/api/jobs/{job['id']}", timeout=min(10, max(1, deadline - time.monotonic())))
        if result["status"] == "completed":
            return result
        if result["status"] in ("failed", "canceled", "interrupted"):
            raise RuntimeError(json.dumps(result))
        time.sleep(1)
    raise TimeoutError(f"Job {job['id']} exceeded operation deadline")


class Measurement:
    def __init__(self, name, case):
        self.name, self.case, self.group = name, case, None

    def locate_group(self):
        pid = state(self.name)["State"]["Pid"]
        if not pid:
            return
        line = next(line for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                    if line.startswith("0::"))
        self.group = Path("/sys/fs/cgroup") / line.split("::", 1)[1].lstrip("/")
        assert (self.group / "cpu.max").read_text().split() == [str(int(CPU * 100000)), "100000"]
        assert int((self.group / "memory.max").read_text()) == MEMORY
        assert int((self.group / "memory.swap.max").read_text()) == 0

    def run(self, label, action, limit):
        row = {"operation": label, "timeout_seconds": limit}
        self.case["operations"].append(row)
        before = time.monotonic()
        samples = []
        peaks = []
        events = []
        stop = threading.Event()
        def monitor():
            while not stop.is_set():
                if self.group is None:
                    try:
                        self.locate_group()
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        pass
                if self.group is not None:
                    try:
                        samples.append(int((self.group / "memory.current").read_text()))
                        peaks.append(int((self.group / "memory.peak").read_text()))
                        events.append(dict(line.split() for line in
                                           (self.group / "memory.events").read_text().splitlines()))
                    except FileNotFoundError:
                        pass
                stop.wait(0.1)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        try:
            value = action(before + limit)
            row["outcome"] = "passed"
            return value
        except Exception as error:
            row["outcome"] = "timeout" if isinstance(error, TimeoutError) else "failed"
            row["error"] = f"{type(error).__name__}: {error}"
            return None
        finally:
            stop.set()
            thread.join(timeout=15)
            snapshot = state(self.name)
            row.update({
                "duration_seconds": round(time.monotonic() - before, 3),
                "sampled_peak_memory_bytes": max(samples, default=0),
                "cgroup_lifetime_peak_memory_bytes": max(peaks, default=0),
                "memory_events_last_sample": events[-1] if events else {},
                "container_state": snapshot["State"],
            })
            print(json.dumps(row), flush=True)
            save()


def create_input(seconds):
    inputs = OUT / "inputs"
    inputs.mkdir(exist_ok=True)
    inputs.chmod(0o777)
    # Input synthesis happens outside the measured service and is explicitly unconstrained.
    code = (
        "from choplab import demo,audio_io; import numpy as np; from pathlib import Path; "
        f"y=demo.make(bars=6); n={seconds}*48000; "
        "y=np.tile(y,(int(np.ceil(n/len(y))),1))[:n]; "
        f"audio_io.write_wav(Path('/inputs/{seconds}.wav'),y,subtype='PCM_16')"
    )
    command("docker", "run", "--rm", "--entrypoint", "python",
            "-v", f"{inputs}:/inputs", IMAGE, "-c", code)
    return inputs / f"{seconds}.wav"


def run_case(seconds):
    source = create_input(seconds)
    name = f"choplab-free-{seconds}"
    case = {"input_seconds": seconds, "input_sample_rate": 48000, "input_channels": 2,
            "input_bytes": source.stat().st_size, "operations": []}
    RESULTS["cases"].append(case)
    volume = f"choplab-free-{seconds}"
    measurement = Measurement(name, case)

    def startup(deadline):
        run = ["docker", "run", "-d", "--name", name, f"--cpus={CPU}",
               f"--memory={MEMORY}", f"--memory-swap={MEMORY}",
               "--env", "CHOPLAB_MODE=hosted", "--env", "CHOPLAB_PUBLIC_URL=https://localhost",
               "--env", "CHOPLAB_PROXY_IPS=*", "--env", "CHOPLAB_PASSWORD_HASH_FILE=/run/secrets/hash",
               "-v", f"{OUT / 'hash'}:/run/secrets/hash:ro", "-v", f"{volume}:/data",
               "-p", "127.0.0.1:8765:8765", IMAGE]
        case["docker_run_argv"] = run
        command(*run)
        measurement.locate_group()
        last_error = None
        while time.monotonic() < deadline:
            if not state(name)["State"]["Running"]:
                raise RuntimeError(f"Container exited during startup: {state(name)['State']}")
            try:
                if api("GET", "/api/health", timeout=3)["ok"]:
                    assert b'<div id="root">' in request("GET", "/")
                    caps = api("GET", "/api/system/capabilities")
                    assert caps["hosted"] and caps["ffmpeg"] and caps["max_audio_seconds"] == 300
                    case["capabilities"] = caps
                    return True
            except (OSError, RuntimeError) as error:
                last_error = str(error)
            time.sleep(1)
        raise TimeoutError(f"Startup exceeded deadline; last request: {last_error}")

    try:
        if not measurement.run("startup_and_protected_frontend", startup, 180):
            case["skipped"] = "All audio operations: startup failed"
            return False

        def upload(deadline):
            pid = api("POST", "/api/projects", {"name": f"Synthetic {seconds}s limit check"})["id"]
            case["project_id"] = pid
            boundary = "choplab-feasibility-boundary"
            prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                      f"filename=\"synthetic-{seconds}.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
            body = prefix + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
            imported = json.loads(request("POST", f"/api/projects/{pid}/sources", raw=body,
                                         content_type=f"multipart/form-data; boundary={boundary}",
                                         timeout=min(60, deadline - time.monotonic())))
            case["source_id"] = imported["source"]["id"]
            return wait_job(imported["job"], deadline)

        if not measurement.run("upload_decode_analysis_discovery", upload, 360):
            case["skipped"] = "HPSS/generation/HQ/WAV/MP3: import failed, no usable analyzed source"
            return False

        pid, sid = case["project_id"], case["source_id"]

        def separate(deadline):
            job = api("POST", f"/api/sources/{sid}/separate", {"model_id": "builtin-hpss"})
            result = wait_job(job, deadline)
            assert set(result["result"]["stems"]) == {"harmonic", "percussive"}
            stems = api("GET", f"/api/sources/{sid}/stems")
            assert {s["stem_type"] for s in stems} == {"harmonic", "percussive"}
            for stem in stems:
                audio = request("GET", f"/api/stems/{stem['id']}/audio")
                assert audio.startswith(b"RIFF") and len(audio) > 100
            return result

        if not measurement.run("builtin_hpss_and_stem_downloads", separate, 240):
            if not state(name)["State"]["Running"] or case["operations"][-1]["outcome"] == "timeout":
                case["skipped"] = "Generation/HQ/WAV/MP3: container died or HPSS remained in flight"
                return False

        def generate(deadline):
            return wait_job(api("POST", f"/api/projects/{pid}/generate",
                                {"bars": 8, "variations": 1, "seed": 11}), deadline)

        if not measurement.run("generate_one_eight_bar_variation", generate, 360):
            case["skipped"] = "HQ/WAV/MP3: generation failed"
            return False
        if APPROXIMATE_E2_MICRO:
            variation = api("GET", f"/api/projects/{pid}/variations")[0]
            audio = request("GET", f"/api/variations/{variation['id']}/audio")
            assert audio.startswith(b"RIFF") and len(audio) > 100
            case["generated_audio_bytes"] = len(audio)
            case["skipped"] = "HQ/WAV/MP3 and maximum input: outside focused approximation check"
            return all(row["outcome"] == "passed" for row in case["operations"])
        variation = api("GET", f"/api/projects/{pid}/variations")[0]
        api("POST", f"/api/variations/{variation['id']}/open-in-studio", {})

        def preview(deadline):
            result = api("POST", f"/api/projects/{pid}/preview",
                         {"scope": "loop", "loop_start_bar": 0, "loop_bars": 2},
                         timeout=max(1, deadline - time.monotonic()))
            audio = request("GET", result["url"])
            assert audio.startswith(b"RIFF") and len(audio) > 100
            case["preview_metrics"] = result["metrics"]
            return result

        measurement.run("hq_two_bar_preview_and_download", preview, 180)
        for fmt in ("wav24", "mp3"):
            if not state(name)["State"]["Running"] or case["operations"][-1]["outcome"] == "timeout":
                case["skipped"] = "Remaining exports: container died or previous operation still in flight"
                return False

            def export(deadline):
                exported = api("POST", f"/api/projects/{pid}/export",
                               {"format": fmt, "filename": f"synthetic-{seconds}", "stems": False})
                wait_job(exported["job"], deadline)
                record = api("GET", f"/api/exports/{exported['export_id']}")
                mix = next(f for f in record["files"] if f["kind"] == "mix")
                assert record["status"] == "done" and not mix["check"]["silent"]
                audio = request("GET", f"/api/exports/{record['id']}/download")
                assert len(audio) > 100
                case[f"{fmt}_checks"] = mix["check"]
                return record

            measurement.run(f"{fmt}_full_eight_bar_export_and_download", export, 180)
        return all(row["outcome"] == "passed" for row in case["operations"])
    finally:
        snapshot = state(name)
        case["final_container_state"] = snapshot["State"]
        (OUT / f"{seconds}s-docker-inspect.json").write_text(json.dumps(snapshot, indent=2))
        (OUT / f"{seconds}s-container.log").write_text(subprocess.check_output(
            ["docker", "logs", name], text=True, stderr=subprocess.STDOUT, timeout=60))
        # Bound failed jobs and remove only this experiment's named resources.
        command("docker", "rm", "-f", name)
        command("docker", "volume", "rm", volume)
        save()


def main():
    salt = bytes(range(16))
    digest = hashlib.pbkdf2_hmac("sha256", b"workflow-test-password", salt, 600000)
    (OUT / "hash").write_text(f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}")
    (OUT / "hash").chmod(0o644)
    RESULTS["docker_version"] = command("docker", "version", "--format", "{{json .}}")
    save()
    if APPROXIMATE_E2_MICRO:
        run_case(4)
        run_case(16)
    else:
        short_passed = run_case(16)
        if not short_passed:
            run_case(4)
        run_case(300)
    print("MEASUREMENT COMPLETE: operation failures are evidence, not CI infrastructure failures.")


if __name__ == "__main__":
    main()
