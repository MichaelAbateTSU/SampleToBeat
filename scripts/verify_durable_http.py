"""Two-phase acceptance for an explicitly disposable, empty hosted library.

No provider operations, deletes, cleanup calls, or automatic job retries are used.
Prepare is followed by an EXTERNAL service replacement/cache loss, then verify.
This harness cannot prove the provider actually replaced its volume or guarantee
uptime, background execution, quota reclamation, or full-workload memory headroom.

Supply a private JSON auth file with {"username": "...", "password": "..."}.
Never put credentials in the URL, arguments, manifest, or logs. Example:

python scripts/verify_durable_http.py prepare --base-url https://TEST.onrender.com
  --expected-origin https://TEST.onrender.com --auth-file PRIVATE.json
  --manifest acceptance.json --confirm-synthetic-test
python scripts/verify_durable_http.py verify [same arguments]

The origin must stay identical between phases. TLS verification stays enabled.
Only loopback CI may additionally pass --allow-loopback-http [--ca TRUSTED.pem].
Prepare refuses a nonempty project library. Keep the private manifest outside
the service's disposable cache. It contains only format/completion markers,
synthetic IDs and SHA-256 checksums; verification never mutates the server.
An incomplete prepare is reported explicitly and never resumed by guessing.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import socket
import ssl
import stat
import struct
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from urllib.parse import urlencode, urlsplit


class AcceptanceError(RuntimeError):
    """Messages must not include credentials, response bodies or raw URLs."""


ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON = 2 << 20
MAX_ASSET = 64 << 20
FORMAT = "choplab-http-acceptance-1"
CHECK_NAMES = {"metadata_" + name for name in ("project", "source", "analysis", "waveform", "variation", "export")}
CHECK_NAMES |= {"original_upload", "source_audio", "variation_audio", "studio_audio", "export_mix", "export_project", "source_range"}
PATH_FIELDS = {"original_file_path", "internal_audio_path", "features_path",
               "preview_path", "sample_path", "audio_path", "path", "folder"}
VOLATILE = {"created_at", "updated_at", "completed_at"}


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise AcceptanceError("Server/manifest returned an invalid synthetic identifier.")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def metadata_digest(doc):
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items()
                    if k not in PATH_FIELDS | VOLATILE | {"stems"}}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    return digest(json.dumps(stable(doc), sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AcceptanceError("HTTP redirect rejected; authentication was not forwarded. Use the exact service origin.")


class Client:
    def __init__(self, base, expected, auth_file, *, allow_loopback_http=False,
                 ca=None, timeout=30, job_timeout=300):
        url = urlsplit(base)
        if (base != expected or base != base.rstrip("/") or url.path or url.query
                or url.fragment or url.username or url.password or not url.hostname):
            raise AcceptanceError("Use the same explicit, exact origin without credentials, path, query or trailing slash.")
        loopback = url.hostname in ("localhost", "127.0.0.1", "::1")
        if url.scheme != "https" and not (url.scheme == "http" and loopback and allow_loopback_http):
            raise AcceptanceError("Verified HTTPS is required; HTTP is permitted only for explicitly opted-in loopback CI.")
        try:
            if Path(auth_file).stat().st_size > 4096 or (os.name != "nt" and stat.S_IMODE(Path(auth_file).stat().st_mode) & 0o077):
                raise ValueError()
            auth = json.loads(Path(auth_file).read_text())
            username, password = auth["username"], auth["password"]
            if (set(auth) != {"username", "password"} or not isinstance(username, str)
                    or not isinstance(password, str) or not username or ":" in username
                    or not password or any(c in username + password for c in "\r\n")):
                raise ValueError()
            context = ssl.create_default_context(cafile=str(ca) if ca else None)
        except (OSError, ValueError, KeyError, TypeError):
            raise AcceptanceError("Invalid private authentication file (use mode 600 on Unix) or trusted CA; no credentials were logged.") from None
        if not 1 <= timeout <= 120 or not 1 <= job_timeout <= 1800:
            raise AcceptanceError("HTTP timeout must be 1-120 seconds; job timeout must be 1-1800 seconds.")
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.base, self.timeout, self.job_timeout = base, timeout, job_timeout
        self.headers = {"Authorization": f"Basic {token}"}
        if url.scheme == "http":
            self.headers["X-Forwarded-Proto"] = "https"
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=context))

    def open(self, method, path, *, body=None, raw=None, headers=None, anonymous=False, timeout=None):
        if method not in ("GET", "POST", "PUT") or not path.startswith("/api/") or path.startswith("//"):
            raise AcceptanceError("Unsupported acceptance operation; destructive requests are forbidden.")
        if method != "GET":
            patterns = (r"/api/projects", r"/api/projects/[A-Za-z0-9_-]+/(sources|generate|preview|export)",
                        r"/api/variations/[A-Za-z0-9_-]+/open-in-studio") if method == "POST" else (
                            r"/api/projects/[A-Za-z0-9_-]+/arrangement",)
            if not any(re.fullmatch(pattern, path) for pattern in patterns):
                raise AcceptanceError("Mutation outside the synthetic project journey is forbidden.")
        request_headers = {} if anonymous else dict(self.headers)
        request_headers.update(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode()
            request_headers["Content-Type"] = "application/json"
        try:
            return self.opener.open(urllib.request.Request(self.base + path, data=data,
                                    headers=request_headers, method=method),
                                    timeout=timeout or self.timeout)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code in (401, 403):
                raise AcceptanceError(f"Private authentication rejected (HTTP {code}); response body was not logged.") from None
            if code in (409, 503):
                raise AcceptanceError(f"Ownership/read-only, unfinished asset, or database/storage failure (HTTP {code}); check the service recovery UI before retrying.") from None
            if code == 404:
                raise AcceptanceError("Required synthetic metadata or asset is missing (HTTP 404).") from None
            raise AcceptanceError(f"Acceptance request failed (HTTP {code}); no response body was logged.") from None
        except (TimeoutError, socket.timeout):
            raise AcceptanceError("HTTP request timed out; no successful state was inferred.") from None
        except urllib.error.URLError:
            raise AcceptanceError("HTTP connection/TLS failed; verify origin, readiness and trusted certificates.") from None

    def json(self, method, path, body=None, *, timeout=None):
        try:
            with self.open(method, path, body=body, timeout=timeout) as response:
                data = response.read(MAX_JSON + 1)
            if len(data) > MAX_JSON:
                raise AcceptanceError("Metadata response exceeded the 2-MiB safety limit.")
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise AcceptanceError("Server returned invalid JSON; no successful state was inferred.") from None
        except (TimeoutError, socket.timeout):
            raise AcceptanceError("Metadata response timed out.") from None

    def checksum(self, path, *, headers=None, expected_status=200):
        sha = hashlib.sha256()
        count = 0
        deadline = time.monotonic() + self.job_timeout
        try:
            with self.open("GET", path, headers=headers) as response:
                if response.status != expected_status:
                    raise AcceptanceError("Download did not honor the required HTTP/range status.")
                if expected_status == 206 and response.headers.get("Content-Range", "").split("/")[0] != "bytes 0-63":
                    raise AcceptanceError("Download returned an invalid byte-range boundary.")
                while chunk := response.read1(1 << 20):
                    count += len(chunk)
                    if count > MAX_ASSET or time.monotonic() > deadline:
                        raise AcceptanceError("Download exceeded the 64-MiB/observation-time safety bound.")
                    sha.update(chunk)
            if not count or (expected_status == 206 and count != 64):
                raise AcceptanceError("Required audio/download is empty or has an invalid range length.")
            return sha.hexdigest()
        except (TimeoutError, socket.timeout):
            raise AcceptanceError("Asset download timed out; checksum was not accepted.") from None

    def wait(self, job):
        id = identifier(job["id"])
        deadline = time.monotonic() + self.job_timeout
        while time.monotonic() < deadline:
            state = self.json("GET", f"/api/jobs/{id}", timeout=min(self.timeout, max(0.1, deadline - time.monotonic())))
            if state.get("status") == "completed":
                return state
            if state.get("status") in ("failed", "canceled", "interrupted"):
                raise AcceptanceError(f"Synthetic job {id} ended {state['status']}; no automatic retry was submitted.")
            if state.get("status") not in ("queued", "running"):
                raise AcceptanceError("Unknown job status; no successful state was inferred.")
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        raise AcceptanceError(f"Synthetic job {id} exceeded the observation timeout.")

    def upload(self, path, audio):
        boundary = "synthetic-" + uuid.uuid4().hex
        prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                  'filename="synthetic.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
        with self.open("POST", path, raw=prefix + audio + f"\r\n--{boundary}--\r\n".encode(),
                       headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}) as response:
            data = response.read(MAX_JSON + 1)
        if len(data) > MAX_JSON:
            raise AcceptanceError("Upload metadata exceeded its safety limit.")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise AcceptanceError("Upload returned invalid JSON.") from None


def synthetic_audio():
    """Generated 4-bar, 92-BPM chord/beat fixture, about 11.5 seconds."""
    rate, beat = 16000, 60 / 92
    chords = ((220, 261.63, 329.63), (174.61, 220, 261.63),
              (130.81, 164.81, 196), (196, 246.94, 293.66))
    samples = bytearray()
    for index in range(int((16 * beat + 1) * rate)):
        t = index / rate
        if t >= 16 * beat:
            value = 0
        else:
            frequencies = chords[int(t / (4 * beat))]
            pad = sum(math.sin(2 * math.pi * f * t) for f in frequencies) * 0.09
            within = t % (beat / 2)
            note = frequencies[int(t / (beat / 2)) % 3] * 2
            arp = math.sin(2 * math.pi * note * within) * math.exp(-within / .1) * .2
            pulse = math.sin(2 * math.pi * 55 * (t % beat)) * math.exp(-(t % beat) / .04) * .2
            value = int((pad + arp + pulse) * 30000)
        samples.extend(struct.pack("<hh", value, value))
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples)
    return stream.getvalue()


def preflight(client):
    if not client.json("GET", "/api/health").get("ok"):
        raise AcceptanceError("Service health is not ready.")
    caps = client.json("GET", "/api/system/capabilities")
    if not caps.get("hosted") or caps.get("metadata_store") != "postgres":
        raise AcceptanceError("Acceptance requires a protected hosted PostgreSQL service.")
    if caps.get("job_recovery_error"):
        raise AcceptanceError("Service is read-only/ownership recovery is blocked; resolve the recovery UI before acceptance.")
    recovery = client.json("GET", "/api/system/recovery")
    if not recovery.get("ok") or recovery.get("read_only"):
        raise AcceptanceError("Service has not acquired safe processing ownership.")
    try:
        with client.open("GET", "/api/projects", anonymous=True):
            pass
    except AcceptanceError as error:
        if "HTTP 401" not in str(error):
            raise
    else:
        raise AcceptanceError("Anonymous library access was accepted; refusing to create synthetic data.")


def save(path, manifest):
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", delete=False) as out:
            tmp = Path(out.name)
            json.dump(manifest, out, sort_keys=True, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        tmp.replace(path)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def checksums(client, manifest):
    ids = manifest["ids"]
    p, s, v, e = (ids[key] for key in ("project", "source", "variation", "export"))
    result = {}
    source = client.json("GET", f"/api/sources/{s}")
    if source.get("content_hash") != digest(synthetic_audio()):
        raise AcceptanceError("Original upload identity was lost or changed.")
    result["original_upload"] = source["content_hash"]
    for name, path in {
        "project": f"/api/projects/{p}", "source": f"/api/sources/{s}",
        "analysis": f"/api/sources/{s}/analysis", "waveform": f"/api/sources/{s}/waveform",
        "variation": f"/api/variations/{v}", "export": f"/api/exports/{e}",
    }.items():
        result["metadata_" + name] = metadata_digest(client.json("GET", path))
    assets = {"source_audio": f"/api/sources/{s}/audio",
              "variation_audio": f"/api/variations/{v}/audio",
              "studio_audio": f"/api/projects/{p}/preview/{ids['preview']}.wav",
              "export_mix": f"/api/exports/{e}/download?{urlencode({'name': 'synthetic.wav'})}",
              "export_project": f"/api/exports/{e}/download?{urlencode({'name': 'synthetic.choplab.json'})}"}
    for name, path in assets.items():
        try:
            result[name] = client.checksum(path)
        except AcceptanceError as error:
            raise AcceptanceError(f"Required asset {name}: {error}") from None
    result["source_range"] = client.checksum(assets["source_audio"], headers={"Range": "bytes=0-63"}, expected_status=206)
    return result


def prepare(client, path):
    if path.exists():
        raise AcceptanceError("Manifest already exists; use verify for a completed run. Incomplete runs require operator review, not automatic resume.")
    preflight(client)
    if client.json("GET", "/api/projects") != []:
        raise AcceptanceError("Prepare requires an EMPTY disposable project library; no existing project was modified.")
    manifest = {"format": FORMAT, "complete": False, "run_id": uuid.uuid4().hex,
                "origin_sha256": digest(client.base.encode()), "ids": {}, "sha256": {}}
    with path.open("x", encoding="utf-8") as out:
        json.dump(manifest, out)
    def record(key, value):
        manifest["ids"][key] = identifier(value)
        save(path, manifest)
        return value
    p = record("project", client.json("POST", "/api/projects", {"name": "synthetic-" + manifest["run_id"]})["id"])
    imported = client.upload(f"/api/projects/{p}/sources", synthetic_audio())
    s = record("source", imported["source"]["id"])
    record("import_job", imported["job"]["id"])
    client.wait(imported["job"])
    source = client.json("GET", f"/api/sources/{s}")
    if source.get("analysis_status") != "analyzed" or len(source.get("_assets", {})) < 3:
        raise AcceptanceError("Source analysis/original/decoded/features were not durably published.")
    generated = client.json("POST", f"/api/projects/{p}/generate",
                            {"bars": 4, "variations": 1, "seed": 4817, "structure": "loop"})
    record("generate_job", generated["id"])
    v = record("variation", client.wait(generated)["result"]["variation_ids"][0])
    opened = client.json("POST", f"/api/variations/{v}/open-in-studio", {})
    # Only project-scoped state is changed; global custom kits are untouched.
    arr = opened["arrangement"]
    client.json("PUT", f"/api/projects/{p}/arrangement", arr)
    preview = client.json("POST", f"/api/projects/{p}/preview", {"scope": "loop", "loop_bars": 1})
    match = re.fullmatch(rf"/api/projects/{re.escape(p)}/preview/(studio_r_[0-9a-f]{{12}})\.wav", preview["url"])
    if not match:
        raise AcceptanceError("Server returned a preview outside the synthetic project.")
    record("preview", match[1])
    exported = client.json("POST", f"/api/projects/{p}/export",
                           {"filename": "synthetic", "format": "wav16", "scope": "loop",
                            "loop_bars": 1, "project_json": True, "stems": False})
    record("export", exported["export_id"])
    record("export_job", exported["job"]["id"])
    client.wait(exported["job"])
    manifest["sha256"] = checksums(client, manifest)
    manifest["complete"] = True
    save(path, manifest)
    return manifest


def load_manifest(path, client):
    try:
        if path.stat().st_size > MAX_JSON:
            raise ValueError()
        manifest = json.loads(path.read_text())
        if (set(manifest) != {"format", "complete", "run_id", "origin_sha256", "ids", "sha256"}
                or manifest["format"] != FORMAT or manifest["complete"] is not True
                or not re.fullmatch("[0-9a-f]{32}", manifest["run_id"])
                or manifest["origin_sha256"] != digest(client.base.encode())
                or set(manifest["ids"]) != {"project", "source", "variation", "preview", "export",
                                           "import_job", "generate_job", "export_job"}
                or set(manifest["sha256"]) != CHECK_NAMES):
            raise ValueError()
        for value in manifest["ids"].values():
            identifier(value)
        if not all(isinstance(value, str) and SHA.fullmatch(value) for value in manifest["sha256"].values()):
            raise ValueError()
        return manifest
    except (OSError, ValueError, TypeError, KeyError):
        raise AcceptanceError("Manifest is invalid, incomplete, or belongs to another origin; no server mutation was attempted.") from None


def verify(client, path):
    manifest = load_manifest(path, client)
    preflight(client)
    p = client.json("GET", f"/api/projects/{manifest['ids']['project']}")
    if p.get("name") != "synthetic-" + manifest["run_id"]:
        raise AcceptanceError("Project identity is not this synthetic run; no other project was touched.")
    for name in ("import_job", "generate_job", "export_job"):
        job = client.json("GET", f"/api/jobs/{manifest['ids'][name]}")
        if job.get("status") != "completed":
            raise AcceptanceError("Previously completed synthetic job history was lost or regressed.")
    observed = checksums(client, manifest)
    if observed != manifest["sha256"]:
        changed = sorted(set(observed) | set(manifest["sha256"]))
        changed = [key for key in changed if observed.get(key) != manifest["sha256"].get(key)]
        raise AcceptanceError("Durable checksum mismatch: " + ", ".join(changed))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phase", choices=("prepare", "verify"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-origin", required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--confirm-synthetic-test", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--http-timeout", type=float, default=30)
    parser.add_argument("--job-timeout", type=float, default=300)
    args = parser.parse_args(argv)
    try:
        if not args.confirm_synthetic_test:
            raise AcceptanceError("Explicit --confirm-synthetic-test opt-in is required for both phases.")
        if args.manifest.resolve() == args.auth_file.resolve():
            raise AcceptanceError("Manifest must not overwrite the private authentication file.")
        client = Client(args.base_url, args.expected_origin, args.auth_file,
                        allow_loopback_http=args.allow_loopback_http, ca=args.ca,
                        timeout=args.http_timeout, job_timeout=args.job_timeout)
        manifest = prepare(client, args.manifest) if args.phase == "prepare" else verify(client, args.manifest)
        print(f"{args.phase} passed: synthetic run {manifest['run_id']}; {len(manifest['sha256'])} SHA-256 checks matched.")
        if args.phase == "prepare":
            print("Replace the service/cache externally, then run verify. No provider operation or cleanup was performed.")
        return 0
    except (AcceptanceError, OSError, KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        message = str(error) if isinstance(error, AcceptanceError) else "Local file or response-shape failure; no successful acceptance was inferred."
        print("Acceptance failed: " + message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
