"""Session-only opted-in full-length completed-audio HTTP acceptance.

Default delegates unchanged to the existing short harness. Long phases require
duration/transfer opt-in; verify also requires independently performed external
replacement confirmation. No provider operations or automatic retries.
Source downloads are streamed, not decoded; their complete RIFF/frame boundary
and SHA-256 are checked. Other assets retain the short harness's 64-MiB bound.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import socket
import struct
import sys
import time
from pathlib import Path
from types import SimpleNamespace

FORMAT = "choplab-long-completed-1"
H = None


def load_script(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guards(seconds, max_source_mib, download_timeout):
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 12 <= seconds <= 1200:
        raise H.AcceptanceError("Long synthetic duration must be an integer from 12 through 1200.")
    if (isinstance(max_source_mib, bool) or not isinstance(max_source_mib, int)
            or not 1 <= max_source_mib <= 512
            or max_source_mib * (1 << 20) < seconds * 48000 * 8 + 65536):
        raise H.AcceptanceError("Source byte guard must fit full 48-kHz stereo float audio plus header, at most 512 MiB.")
    if (isinstance(download_timeout, bool) or not isinstance(download_timeout, (int, float))
            or not math.isfinite(download_timeout) or not 1 <= download_timeout <= 1800):
        raise H.AcceptanceError("Download deadline must be finite and from 1 through 1800 seconds.")


def wav_boundary(prefix, seconds):
    if len(prefix) < 12 or prefix[:4] != b"RIFF" or prefix[8:12] != b"WAVE":
        raise H.AcceptanceError("Full source must be a standard RIFF WAV.")
    total = struct.unpack_from("<I", prefix, 4)[0] + 8
    pos, fmt = 12, None
    while pos + 8 <= len(prefix):
        tag, size = struct.unpack_from("<4sI", prefix, pos)
        begin = pos + 8
        if tag == b"data":
            if fmt != (3, 2, 48000, 384000, 8, 32) or size != seconds * 48000 * 8:
                raise H.AcceptanceError("Decoded source format/frame duration does not match the full synthetic input.")
            if begin + size != total:
                raise H.AcceptanceError("Source WAV has inconsistent RIFF/data framing.")
            return total
        if begin + size > len(prefix):
            return None
        if tag == b"fmt ":
            if size < 16 or fmt is not None:
                raise H.AcceptanceError("Source WAV has invalid format framing.")
            fmt = struct.unpack_from("<HHIIHH", prefix, begin)
        pos = begin + size + (size % 2)
    return None


def client_type(harness):
    class LongClient(harness.Client):
        def configure_long(self, seconds, max_source_mib, download_timeout):
            guards(seconds, max_source_mib, download_timeout)
            self.seconds = seconds
            self.max_source_bytes = max_source_mib * (1 << 20)
            self.download_timeout = download_timeout
            self.source_id = None
            self.source_bytes = None

        def checksum(self, path, *, headers=None, expected_status=200):
            if self.source_id is None or path != f"/api/sources/{self.source_id}/audio" or expected_status != 200:
                return super().checksum(path, headers=headers, expected_status=expected_status)
            sha, count, prefix, expected = hashlib.sha256(), 0, bytearray(), None
            deadline = time.monotonic() + self.download_timeout
            try:
                with self.open("GET", path, headers=headers) as response:
                    if response.status != 200:
                        raise harness.AcceptanceError("Full-source download returned an unexpected HTTP status.")
                    declared = response.headers.get("Content-Length")
                    if declared is not None and (not declared.isdigit() or not 0 < int(declared) <= self.max_source_bytes):
                        raise harness.AcceptanceError("Full-source Content-Length exceeds the explicit byte guard.")
                    while True:
                        chunk = response.read1(1 << 20)
                        if time.monotonic() >= deadline:
                            raise harness.AcceptanceError("Full-source download exceeded the explicit deadline.")
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > self.max_source_bytes:
                            raise harness.AcceptanceError("Full-source download exceeded the explicit byte guard.")
                        if expected is None:
                            prefix.extend(chunk[:max(0, 65536 - len(prefix))])
                            expected = wav_boundary(prefix, self.seconds)
                            if expected is None and len(prefix) >= 65536:
                                raise harness.AcceptanceError("Source WAV header exceeds its bounded framing guard.")
                            if expected is not None and expected > self.max_source_bytes:
                                raise harness.AcceptanceError("Source WAV framing exceeds the explicit byte guard.")
                        sha.update(chunk)
                    if expected is None or count != expected or (declared is not None and count != int(declared)):
                        raise harness.AcceptanceError("Full-source download is truncated or has inconsistent framing/length.")
                self.source_bytes = count
                return sha.hexdigest()
            except (TimeoutError, socket.timeout):
                raise harness.AcceptanceError("Full-source download timed out; checksum was not accepted.") from None
    return LongClient


def linked(client, manifest):
    ids = manifest["ids"]
    project = client.json("GET", f"/api/projects/{ids['project']}")
    source = client.json("GET", f"/api/sources/{ids['source']}")
    job = client.json("GET", f"/api/jobs/{ids['import_job']}")
    if (project.get("name") != "synthetic-" + manifest["run_id"]
            or ids["source"] not in project.get("source_ids", [])
            or source.get("project_id") != ids["project"]
            or source.get("analysis_status") != "analyzed"
            or source.get("duration_seconds") != client.seconds
            or job.get("status") != "completed" or job.get("job_type") != "import"
            or job.get("project_id") != ids["project"]
            or job.get("params", {}).get("source_id") != ids["source"]):
        raise H.AcceptanceError("Completed full-length import/source/project linkage or exact duration was not established.")


def binding(client):
    result = {"format": FORMAT, "seconds": client.seconds,
            "upload_bytes": 44 + client.seconds * 16000 * 4,
            "max_source_bytes": client.max_source_bytes,
            "download_timeout": client.download_timeout}
    if getattr(client, "external_input", None):
        result["upload_bytes"] = client.external_input["size"]
        result["external_input_sha256"] = client.external_input["sha256"]
        result["external_manifest_sha256"] = H.digest(json.dumps(client.external_input, sort_keys=True,
                                                                separators=(",", ":")).encode())
    return result


def load_manifest(path, client):
    try:
        if path.stat().st_size > H.MAX_JSON:
            raise ValueError()
        data = json.loads(path.read_text())
        if data.pop("long_test") != binding(client):
            raise ValueError()
        source_bytes = data.pop("source_bytes")
        if (isinstance(source_bytes, bool) or not isinstance(source_bytes, int)
                or not client.seconds * 48000 * 8 < source_bytes <= client.max_source_bytes):
            raise ValueError()
        # Reuse every original manifest identity/checksum validation unchanged.
        class View:
            def stat(self):
                return path.stat()
            def read_text(self):
                return json.dumps(data)
        return H.load_manifest(View(), client), source_bytes
    except (OSError, ValueError, TypeError, KeyError):
        raise H.AcceptanceError("Long manifest is invalid or does not match the explicit duration/transfer guards.") from None


def prepare(client, path, fixture, *, external=None):
    guards(client.seconds, client.max_source_bytes // (1 << 20), client.download_timeout)
    if path.exists():
        raise H.AcceptanceError("Manifest already exists; no ambiguous prepare was retried.")
    H.preflight(client)
    if client.json("GET", "/api/projects") != []:
        raise H.AcceptanceError("Prepare requires an EMPTY disposable project library.")
    caps = client.json("GET", "/api/system/capabilities")
    limit, upload = caps.get("max_audio_seconds"), caps.get("max_upload_mb")
    if (isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit)
            or limit < client.seconds or isinstance(upload, bool)
            or not isinstance(upload, (int, float)) or not math.isfinite(upload)
            or upload * (1 << 20) < binding(client)["upload_bytes"] + (4096 if external is None else 0)):
        raise H.AcceptanceError("Advertised duration/upload caps do not permit the full synthetic test; no mutation attempted.")
    audio = fixture(client.seconds) if external is None else b"external-verified-fixture"
    if external is None and (len(audio) != binding(client)["upload_bytes"] or len(audio) + 4096 > 100 * (1 << 20)):
        raise H.AcceptanceError("Synthetic fixture violates exact framing/upload guard; no mutation attempted.")
    old_audio, old_save, old_checksums = H.synthetic_audio, H.save, H.checksums
    old_digest, old_upload = H.digest, client.upload
    if external is not None:
        H.digest = lambda data: external["sha256"] if data is audio else old_digest(data)
        client.upload = lambda target, _: external["upload"](client, target)
    def save(target, manifest):
        if target != path:
            raise H.AcceptanceError("Unexpected synthetic manifest target.")
        # Never expose successful completion until the additional linkage and
        # full-length framing checks have also passed.
        old_save(target, {**manifest, "complete": False, "long_test": binding(client),
                          "source_bytes": client.source_bytes})
    def checksums(c, manifest):
        client.source_id = manifest["ids"]["source"]
        linked(client, manifest)
        return old_checksums(c, manifest)
    H.synthetic_audio, H.save, H.checksums = lambda: audio, save, checksums
    try:
        manifest = H.prepare(client, path)
        linked(client, manifest)
        result = {**manifest, "long_test": binding(client), "source_bytes": client.source_bytes}
        old_save(path, result)
        return result
    finally:
        H.synthetic_audio, H.save, H.checksums = old_audio, old_save, old_checksums
        H.digest, client.upload = old_digest, old_upload


def verify(client, path):
    manifest, source_bytes = load_manifest(path, client)
    client.source_id = manifest["ids"]["source"]
    H.preflight(client)
    linked(client, manifest)
    for key in ("generate_job", "export_job"):
        if client.json("GET", f"/api/jobs/{manifest['ids'][key]}").get("status") != "completed":
            raise H.AcceptanceError("Previously completed job history regressed.")
    if H.checksums(client, manifest) != manifest["sha256"] or client.source_bytes != source_bytes:
        raise H.AcceptanceError("Full-length durable checksum or exact source-byte count changed after replacement.")
    return manifest


def main(argv=None):
    global H
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "verify"))
    parser.add_argument("--harness-file", type=Path, required=True)
    parser.add_argument("--synthetic-seconds", type=int)
    parser.add_argument("--confirm-long-synthetic", action="store_true")
    parser.add_argument("--confirm-external-replacement", action="store_true")
    parser.add_argument("--max-source-mib", type=int)
    parser.add_argument("--download-timeout", type=float)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-origin", required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--confirm-synthetic-test", action="store_true")
    parser.add_argument("--http-timeout", type=float, default=30)
    parser.add_argument("--job-timeout", type=float, default=300)
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--allow-loopback-http", action="store_true")
    args = parser.parse_args(argv)
    try:
        H = load_script(args.harness_file, "long_existing_harness")
        if args.synthetic_seconds is None:
            if (args.confirm_long_synthetic or args.confirm_external_replacement
                    or args.max_source_mib is not None or args.download_timeout is not None):
                raise H.AcceptanceError("Long-only options require an explicit synthetic duration.")
            flags = [args.phase, "--base-url", args.base_url, "--expected-origin", args.expected_origin,
                     "--auth-file", str(args.auth_file), "--manifest", str(args.manifest),
                     "--http-timeout", str(args.http_timeout), "--job-timeout", str(args.job_timeout)]
            if args.confirm_synthetic_test:
                flags.append("--confirm-synthetic-test")
            if args.ca:
                flags.extend(("--ca", str(args.ca)))
            if args.allow_loopback_http:
                flags.append("--allow-loopback-http")
            return H.main(flags)
        if not args.confirm_synthetic_test or not args.confirm_long_synthetic:
            raise H.AcceptanceError("Explicit synthetic-test and long-test confirmations required for both long phases.")
        guards(args.synthetic_seconds, args.max_source_mib, args.download_timeout)
        if args.phase == "verify" and not args.confirm_external_replacement:
            raise H.AcceptanceError("Verify requires independent external-replacement confirmation.")
        if args.manifest.resolve() in (args.auth_file.resolve(), args.harness_file.resolve(), Path(__file__).resolve()):
            raise H.AcceptanceError("Use a separate long manifest, not credentials or script files.")
        client = client_type(H)(args.base_url, args.expected_origin, args.auth_file,
                               allow_loopback_http=args.allow_loopback_http, ca=args.ca,
                               timeout=args.http_timeout, job_timeout=args.job_timeout)
        client.configure_long(args.synthetic_seconds, args.max_source_mib, args.download_timeout)
        addon = load_script(Path(__file__).with_name("verify_import_interruption.py"), "long_fixture")
        addon.H = SimpleNamespace(AcceptanceError=H.AcceptanceError, synthetic_audio=H.synthetic_audio)
        result = prepare(client, args.manifest, addon.fixture) if args.phase == "prepare" else verify(client, args.manifest)
        print(f"{args.phase} passed: synthetic run {result['run_id']}; {args.synthetic_seconds} seconds; full source SHA-256 verified.")
        print("No provider replacement/overlap or memory/quota feasibility was inferred. No automatic retry or cleanup.")
        return 0
    except Exception as error:
        safe = str(error) if H and isinstance(error, H.AcceptanceError) else "Local/response failure; sensitive details suppressed."
        print("Long acceptance failed: " + safe)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
