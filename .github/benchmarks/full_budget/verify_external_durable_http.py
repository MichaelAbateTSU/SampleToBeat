"""Session-only independent WAV acceptance; no fixture generation/provider ops.

Requires a separately verified fixture manifest, explicit exact-limit opt-in,
private auth, byte/decoded/transfer guards and external replacement confirmation.
Only prepare reads/uploads the fixture; verify uses its bound manifest identity.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import time
import uuid
import wave

MAX_INPUT = 1 << 30


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


L = load(Path(__file__).with_name("verify_long_durable_http.py"), "external_long")
H = None


def fixture_manifest(path):
    try:
        if path.stat().st_size > 4096:
            raise ValueError()
        data = json.loads(path.read_text())
        if (set(data) != {"format", "verified", "size", "sha256", "frames", "sample_rate", "channels", "sample_width"}
                or data["format"] != "choplab-independent-wav-1" or data["verified"] is not True
                or not H.SHA.fullmatch(data["sha256"])):
            raise ValueError()
        for key in ("size", "frames", "sample_rate", "channels", "sample_width"):
            if isinstance(data[key], bool) or not isinstance(data[key], int) or data[key] <= 0:
                raise ValueError()
        if data["size"] > MAX_INPUT or data["channels"] > 8 or data["sample_width"] > 4:
            raise ValueError()
        return data
    except (OSError, ValueError, TypeError, KeyError):
        raise H.AcceptanceError("Independent fixture manifest is invalid or unverified.") from None


def validate_guards(data, max_input_bytes, max_source_mib, timeout, exact=True):
    seconds = data["frames"] / data["sample_rate"]
    if exact and (data["size"] != MAX_INPUT or data["frames"] != 1200 * data["sample_rate"]):
        raise H.AcceptanceError("Exact-limit mode requires independently verified 1-GiB and 1200-second framing.")
    if seconds != int(seconds):
        raise H.AcceptanceError("Fixture duration must be an exact integer number of seconds.")
    L.guards(int(seconds), max_source_mib, timeout)
    if isinstance(max_input_bytes, bool) or not isinstance(max_input_bytes, int) or not data["size"] <= max_input_bytes <= MAX_INPUT:
        raise H.AcceptanceError("Explicit input-byte cap must fit the verified file and cannot exceed 1 GiB.")
    return int(seconds)


def verified_stream(path, data):
    """Open once; hash incrementally and retain the same descriptor for upload."""
    stream = path.open("rb")
    try:
        before = os.fstat(stream.fileno())
        if before.st_size != data["size"]:
            raise H.AcceptanceError("Fixture file size does not match its independent manifest.")
        sha = hashlib.sha256()
        while chunk := stream.read(1 << 20):
            sha.update(chunk)
        if sha.hexdigest() != data["sha256"]:
            raise H.AcceptanceError("Fixture SHA-256 does not match its independent manifest.")
        stream.seek(0)
        with wave.open(stream, "rb") as wav:
            if (wav.getnframes(), wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (
                    data["frames"], data["sample_rate"], data["channels"], data["sample_width"], "NONE"):
                raise H.AcceptanceError("Fixture WAV format/frame count differs from independent verification.")
        stream.seek(0)
        return stream, before
    except Exception:
        stream.close()
        raise


def upload(client, target, stream, before, data):
    boundary = "synthetic-" + uuid.uuid4().hex
    prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.wav"\r\n'
              'Content-Type: audio/wav\r\n\r\n').encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    if len(prefix) + len(suffix) > 4096:
        raise H.AcceptanceError("Multipart framing exceeds its explicit 4096-byte allowance.")
    deadline = time.monotonic() + client.job_timeout
    def chunks():
        current = os.fstat(stream.fileno())
        if (current.st_size, current.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise H.AcceptanceError("Fixture changed before upload; no automatic retry.")
        yield prefix
        stream.seek(0)
        size, sha = 0, hashlib.sha256()
        while chunk := stream.read(1 << 20):
            if time.monotonic() >= deadline:
                raise H.AcceptanceError("External upload exceeded the explicit job/transfer deadline.")
            size += len(chunk)
            if size > data["size"]:
                raise H.AcceptanceError("Fixture grew during upload; commit may be ambiguous.")
            sha.update(chunk)
            yield chunk
        if size != data["size"] or sha.hexdigest() != data["sha256"]:
            raise H.AcceptanceError("Fixture changed during upload; commit may be ambiguous.")
        if time.monotonic() >= deadline:
            raise H.AcceptanceError("External upload exceeded the explicit job/transfer deadline.")
        yield suffix
    try:
        with client.open("POST", target, raw=chunks(),
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                  "Content-Length": str(len(prefix) + data["size"] + len(suffix))}) as response:
            return client._read_metadata(response, client.timeout)
    except Exception:
        raise H.AcceptanceError("External upload failed; its commit state may be unknown. Inspect the synthetic manifest/project; no retry.") from None


def main(argv=None):
    global H
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "verify"))
    for name in ("harness-file", "auth-file", "manifest", "fixture-manifest"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--fixture-file", type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-origin", required=True)
    parser.add_argument("--confirm-synthetic-test", action="store_true")
    parser.add_argument("--confirm-exact-limit-test", action="store_true")
    parser.add_argument("--confirm-external-replacement", action="store_true")
    parser.add_argument("--max-input-bytes", type=int, required=True)
    parser.add_argument("--max-source-mib", type=int, required=True)
    parser.add_argument("--download-timeout", type=float, required=True)
    parser.add_argument("--http-timeout", type=float, default=120)
    parser.add_argument("--job-timeout", type=float, default=1800)
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--ca", type=Path)
    args = parser.parse_args(argv)
    stream = None
    try:
        H = load(args.harness_file, "external_existing")
        L.H = H
        if not args.confirm_synthetic_test or not args.confirm_exact_limit_test:
            raise H.AcceptanceError("Synthetic and exact-limit confirmations required for both phases.")
        data = fixture_manifest(args.fixture_manifest)
        seconds = validate_guards(data, args.max_input_bytes, args.max_source_mib, args.download_timeout)
        protected = [args.auth_file, args.harness_file, args.fixture_manifest, Path(__file__)]
        if args.fixture_file:
            protected.append(args.fixture_file)
        if args.manifest.resolve() in [path.resolve() for path in protected]:
            raise H.AcceptanceError("Acceptance manifest must be separate from auth, scripts and independent fixture.")
        if args.phase == "verify" and not args.confirm_external_replacement:
            raise H.AcceptanceError("Independent external-replacement confirmation required.")
        if args.phase == "prepare":
            if args.fixture_file is None:
                raise H.AcceptanceError("Prepare requires the independently generated fixture file.")
            stream, before = verified_stream(args.fixture_file, data)
        client = L.client_type(H)(args.base_url, args.expected_origin, args.auth_file,
                                  allow_loopback_http=args.allow_loopback_http, ca=args.ca,
                                  timeout=args.http_timeout, job_timeout=args.job_timeout)
        client.configure_long(seconds, args.max_source_mib, args.download_timeout)
        client.external_input = data
        if args.phase == "prepare":
            # L.prepare checks advertised duration/upload caps and EMPTY library
            # before creating its manifest/project. No regenerated fixture.
            result = L.prepare(client, args.manifest, None, external={
                "sha256": data["sha256"], "upload": lambda c, target: upload(c, target, stream, before, data)})
        else:
            result = L.verify(client, args.manifest)
        print(f"{args.phase} passed: synthetic run {result['run_id']}; independent exact-limit WAV and full decoded checksum verified.")
        return 0
    except Exception as error:
        message = str(error) if H and isinstance(error, H.AcceptanceError) else "Local/response failure; details suppressed."
        print("External acceptance failed: " + message)
        return 1
    finally:
        if stream is not None:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
