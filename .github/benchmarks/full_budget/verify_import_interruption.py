"""Session-only in-flight import observation across an EXTERNAL replacement.

start creates a separate synthetic project; verify is GET-only. Exit codes:
0 = active start observed / interrupted job and linked source verified
1 = unsafe state, failure, timeout or invalid input
2 = inconclusive (completion or another terminal outcome, no recovery proof)
No retries, cancellation, cleanup, provider calls, or overlap assumptions.
Keep this manifest separate from the completed-audio acceptance manifest.
Default audio remains the existing ~11.5-second fixture. start may explicitly
opt into --synthetic-seconds 12..1200 --confirm-long-synthetic. Longer audio
can increase processing/memory pressure; it does not guarantee active work
during rollout. WAV upload remains below 100 MiB and respects advertised caps.
"""
import argparse
import importlib.util
import http.client
import io
import json
import sys
import time
import uuid
import wave
from pathlib import Path

H = None
FORMAT = "choplab-import-interruption-1"


class Inconclusive(RuntimeError):
    pass


def load_harness(path):
    spec = importlib.util.spec_from_file_location("existing_durable_http", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(seconds=None):
    if seconds is None:
        return H.synthetic_audio()
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 12 <= seconds <= 1200:
        raise H.AcceptanceError("Optional synthetic duration must be an integer from 12 through 1200 seconds.")
    with wave.open(io.BytesIO(H.synthetic_audio()), "rb") as source:
        if source.getnchannels() != 2 or source.getsampwidth() != 2 or source.getframerate() != 16000:
            raise H.AcceptanceError("Existing generated fixture has unexpected PCM format.")
        pcm = source.readframes(source.getnframes())
        if not pcm:
            raise H.AcceptanceError("Existing generated fixture is empty.")
    stream = io.BytesIO()
    remaining = seconds * 16000 * 4
    with wave.open(stream, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(16000)
        while remaining:
            size = min(remaining, len(pcm))
            out.writeframesraw(pcm[:size])
            remaining -= size
    audio = stream.getvalue()
    if len(audio) > 100 * (1 << 20):
        raise H.AcceptanceError("Synthetic WAV exceeds the hosted 100-MiB bound.")
    return audio


def start(client, path, *, synthetic_seconds=None, confirm_long=False):
    if path.exists():
        raise H.AcceptanceError("Manifest already exists; no duplicate import was submitted.")
    if synthetic_seconds is not None and not confirm_long:
        raise H.AcceptanceError("Long synthetic audio requires explicit --confirm-long-synthetic.")
    if synthetic_seconds is not None:
        if isinstance(synthetic_seconds, bool) or not isinstance(synthetic_seconds, int) or not 12 <= synthetic_seconds <= 1200:
            raise H.AcceptanceError("Optional synthetic duration must be an integer from 12 through 1200 seconds.")
    H.preflight(client)
    if synthetic_seconds is not None:
        caps = client.json("GET", "/api/system/capabilities")
        limit = caps.get("max_audio_seconds")
        upload_limit = caps.get("max_upload_mb")
        if (not isinstance(limit, (int, float)) or isinstance(limit, bool)
                or not synthetic_seconds <= limit
                or not isinstance(upload_limit, (int, float)) or isinstance(upload_limit, bool)
                or not upload_limit >= 1):
            raise H.AcceptanceError("Long fixture exceeds advertised hosted audio limits or limits are unavailable.")
    audio = fixture(synthetic_seconds)
    if synthetic_seconds is not None and len(audio) > min(100, upload_limit) * (1 << 20):
        raise H.AcceptanceError("Synthetic WAV exceeds the advertised upload bound; no project was created.")
    state = {"format": FORMAT, "run_id": uuid.uuid4().hex,
             "origin_sha256": H.digest(client.base.encode()), "ids": {},
             "phase": "creating", "observed_status": None}
    with path.open("x", encoding="utf-8") as out:
        json.dump(state, out)
    project = client.json("POST", "/api/projects", {"name": "synthetic-interruption-" + state["run_id"]})
    state["ids"]["project"] = H.identifier(project["id"])
    state["phase"] = "uploading"
    H.save(path, state)
    imported = client.upload(f"/api/projects/{state['ids']['project']}/sources", audio)
    # Preserve each known ID even if the other returned identifier is invalid.
    state["ids"]["source"] = H.identifier(imported["source"]["id"])
    H.save(path, state)
    state["ids"]["job"] = H.identifier(imported["job"]["id"])
    state["phase"] = "observing"
    H.save(path, state)
    job = client.json("GET", f"/api/jobs/{state['ids']['job']}")
    if (job.get("project_id") != state["ids"]["project"] or job.get("job_type") != "import"
            or job.get("params", {}).get("source_id") != state["ids"]["source"]):
        raise H.AcceptanceError("Job/source identity does not match this synthetic project.")
    status = job.get("status")
    if status not in ("queued", "running", "completed", "failed", "interrupted", "canceled"):
        raise H.AcceptanceError("Unknown import status; active work was not established.")
    if status not in ("queued", "running"):
        state["phase"] = "inconclusive"
        state["observed_status"] = status if status in ("completed", "failed", "interrupted", "canceled") else None
        H.save(path, state)
        raise Inconclusive("Import was not observed queued/running; no in-flight replacement test was established.")
    state["phase"] = "active-observed"
    state["observed_status"] = status
    H.save(path, state)
    return state


def load(path, client):
    try:
        if path.stat().st_size > H.MAX_JSON:
            raise ValueError()
        state = json.loads(path.read_text())
        if (set(state) != {"format", "run_id", "origin_sha256", "ids", "phase", "observed_status"}
                or state["format"] != FORMAT or state["origin_sha256"] != H.digest(client.base.encode())
                or not isinstance(state["run_id"], str) or len(state["run_id"]) != 32
                or any(c not in "0123456789abcdef" for c in state["run_id"])
                or set(state["ids"]) != {"project", "source", "job"}):
            raise ValueError()
        for id in state["ids"].values():
            H.identifier(id)
        if state["phase"] == "inconclusive":
            raise Inconclusive("Start never established a queued/running job; this manifest cannot prove recovery.")
        if state["phase"] != "active-observed" or state["observed_status"] not in ("queued", "running"):
            raise ValueError()
        return state
    except (OSError, ValueError, TypeError, KeyError):
        raise H.AcceptanceError("Invalid/incomplete interruption manifest; no server mutation was attempted.") from None


def wait_ready(client, deadline):
    while time.monotonic() < deadline:
        remaining = min(client.timeout, max(.1, deadline - time.monotonic()))
        try:
            health = client.json("GET", "/api/health", timeout=remaining)
            caps = client.json("GET", "/api/system/capabilities", timeout=remaining)
            recovery = client.json("GET", "/api/system/recovery", timeout=remaining)
        except H.AcceptanceError as error:
            # Only ownership/read-only service responses are retried; auth,
            # missing routes and connection failures remain explicit failures.
            if "HTTP 409" not in str(error) and "HTTP 503" not in str(error):
                raise
        else:
            if not caps.get("hosted") or caps.get("metadata_store") != "postgres":
                raise H.AcceptanceError("Replacement must remain a protected hosted PostgreSQL service.")
            if health.get("ok") and recovery.get("ok") and not recovery.get("read_only") and not caps.get("job_recovery_error"):
                return
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise H.AcceptanceError("Ownership/readiness timeout; no interruption outcome was inferred.")


def verify(client, path):
    state = load(path, client)
    deadline = time.monotonic() + client.job_timeout
    wait_ready(client, deadline)
    p, s, j = (state["ids"][key] for key in ("project", "source", "job"))
    project = client.json("GET", f"/api/projects/{p}")
    if project.get("name") != "synthetic-interruption-" + state["run_id"] or s not in project.get("source_ids", []):
        raise H.AcceptanceError("Synthetic project/source identity was lost or changed.")
    while time.monotonic() < deadline:
        job = client.json("GET", f"/api/jobs/{j}", timeout=min(client.timeout, max(.1, deadline - time.monotonic())))
        if job.get("project_id") != p or job.get("job_type") != "import" or job.get("params", {}).get("source_id") != s:
            raise H.AcceptanceError("Observed job is not this manifest's synthetic import.")
        status = job.get("status")
        if status == "interrupted":
            source = client.json("GET", f"/api/sources/{s}")
            if (source.get("project_id") != p or source.get("analysis_status") != "interrupted"
                    or not isinstance(source.get("error"), str) or not source["error"].strip()
                    or not isinstance(job.get("error_message"), str) or not job["error_message"].strip()
                    or job.get("completed_at") is None):
                raise H.AcceptanceError("Interrupted job lacks matching linked-source interruption/retry feedback.")
            return state
        if status == "completed":
            raise Inconclusive("Import completed before interruption could be demonstrated; replacement recovery is inconclusive.")
        if status in ("failed", "canceled"):
            raise Inconclusive("Import reached another terminal outcome; interrupted-job recovery was not demonstrated.")
        if status not in ("queued", "running"):
            raise H.AcceptanceError("Unknown import status; no successful recovery was inferred.")
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise H.AcceptanceError("Import stayed queued/running past the bounded verification deadline.")


def main(argv=None):
    global H
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("start", "verify"))
    parser.add_argument("--harness-file", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-origin", required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--confirm-synthetic-test", action="store_true")
    parser.add_argument("--confirm-external-replacement", action="store_true")
    parser.add_argument("--synthetic-seconds", type=int)
    parser.add_argument("--confirm-long-synthetic", action="store_true")
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--http-timeout", type=float, default=30)
    parser.add_argument("--job-timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        H = load_harness(args.harness_file)
        if not args.confirm_synthetic_test:
            raise H.AcceptanceError("Explicit synthetic-test opt-in is required.")
        if args.phase == "verify" and not args.confirm_external_replacement:
            raise H.AcceptanceError("Verify requires independent external replacement confirmation; elapsed time/status is not proof.")
        if args.phase == "verify" and (args.synthetic_seconds is not None or args.confirm_long_synthetic):
            raise H.AcceptanceError("Long-fixture options apply only to start; verify remains read-only.")
        if args.manifest.resolve() in (args.auth_file.resolve(), args.harness_file.resolve(), Path(__file__).resolve()):
            raise H.AcceptanceError("Use a separate interruption manifest, not an authentication or script file.")
        client = H.Client(args.base_url, args.expected_origin, args.auth_file, ca=args.ca,
                          allow_loopback_http=args.allow_loopback_http,
                          timeout=args.http_timeout, job_timeout=args.job_timeout)
        state = (start(client, args.manifest, synthetic_seconds=args.synthetic_seconds,
                       confirm_long=args.confirm_long_synthetic)
                 if args.phase == "start" else verify(client, args.manifest))
        print(f"{args.phase} passed: synthetic run {state['run_id']}; job {state['ids']['job']}.")
        if args.phase == "start":
            print("Job was observed queued/running. Perform external replacement now; completion may still make this test inconclusive.")
        else:
            print("Same job and linked source are interrupted with feedback. This does not prove old-worker overlap or provider lifecycle semantics.")
        return 0
    except Inconclusive as error:
        print("INCONCLUSIVE: " + str(error))
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError,
            RuntimeError, http.client.HTTPException) as error:
        safe = str(error) if H and isinstance(error, H.AcceptanceError) else "Local script/file/response failure; sensitive exception details suppressed."
        print("Acceptance failed: " + safe)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
