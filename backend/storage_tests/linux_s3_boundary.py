"""Real TLS/S3 protocol check, run only by the disposable Linux container harness."""
print("boundary interpreter started", flush=True)
import hashlib
import json
import resource
import time
from dataclasses import replace
from pathlib import Path

from choplab.object_storage import (
    IntegrityError, MIB, PART_BYTES, S3ByteStorage, S3Settings, StorageError, TransferLimitError,
)

ROOT = Path("/working")
RESULTS = Path("/evidence/result.json")
SIZE = 640 * MIB + 17
LIMIT = 512 * MIB
CGROUP = Path("/sys/fs/cgroup")
CHECKS = []


def memory():
    events = dict(line.split() for line in (CGROUP / "memory.events").read_text().splitlines())
    return {
        "max_bytes": int((CGROUP / "memory.max").read_text()),
        "swap_max_bytes": int((CGROUP / "memory.swap.max").read_text()),
        "peak_bytes": int((CGROUP / "memory.peak").read_text()),
        "current_bytes": int((CGROUP / "memory.current").read_text()),
        "events": {key: int(value) for key, value in events.items()},
        "process_max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }


def save(status):
    RESULTS.write_text(json.dumps({
        "status": status, "object_bytes": SIZE, "part_bytes": PART_BYTES,
        "read_bytes": MIB, "cgroup": memory(), "checks": CHECKS,
        "service": "disposable MinIO over certificate-verified TLS; not Neon or the app",
    }, indent=2))


def check(label, operation):
    print("starting:", label, flush=True)
    before = time.monotonic()
    operation()
    CHECKS.append({"check": label, "seconds": round(time.monotonic() - before, 3),
                   "memory": memory()})
    save("running")


def fails(error, operation):
    try:
        operation()
    except error as exc:
        assert "synthetic-secret" not in str(exc)
        return
    raise AssertionError(f"Expected explicit {error.__name__}")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    print("imports complete", memory(), flush=True)
    ROOT.mkdir(exist_ok=True)
    assert memory()["max_bytes"] == LIMIT
    assert memory()["swap_max_bytes"] == 0
    settings = S3Settings(
        "https://s3:9000", "synthetic-audio", "us-east-1",
        "synthetic-access", "synthetic-secret", ca_bundle="/certs/public.crt",
    )
    save("initializing SDK")
    print("initializing SDK", flush=True)
    adapter = S3ByteStorage(settings, ROOT)
    print("SDK initialized", memory(), flush=True)
    adapter.client.create_bucket(Bucket=settings.bucket)
    source = ROOT / "large.wav"

    def generate():
        remaining = SIZE
        block = bytes(range(256)) * (MIB // 256)
        with source.open("wb") as stream:
            while remaining:
                stream.write(block[:min(remaining, len(block))])
                remaining -= min(remaining, len(block))
    check("generate 640 MiB plus 17 bytes without buffering the object", generate)
    expected = digest(source)
    ref_box = []
    parts = []
    def record_part(params, **_):
        parts.append(params["PartNumber"])
    adapter.client.meta.events.register("before-parameter-build.s3.UploadPart", record_part)
    check("real multipart publish", lambda: ref_box.append(adapter.publish("large.wav")))
    ref = ref_box[0]
    assert ref.size == SIZE and ref.sha256 == expected
    assert parts == list(range(1, 82))
    check("real materialization and independent checksum",
          lambda: adapter.materialize(ref, "restored.wav"))
    assert digest(ROOT / "restored.wav") == expected
    part_count = len(parts)
    check("idempotent publish skips transfer", lambda: adapter.publish("large.wav"))
    assert len(parts) == part_count

    def rewrite_same_bytes():
        from boto3.s3.transfer import TransferConfig
        adapter.client.upload_file(
            str(source), settings.bucket, ref.key, ExtraArgs={"Metadata": {"sha256": ref.sha256}},
            Config=TransferConfig(multipart_threshold=16 * MIB,
                                  multipart_chunksize=16 * MIB, use_threads=False),
        )
        head = adapter.client.head_object(Bucket=settings.bucket, Key=ref.key)
        assert head["ETag"] != ref.etag
        adapter.materialize(ref, "changed-layout.wav")
        assert digest(ROOT / "changed-layout.wav") == expected
    check("identical bytes with different multipart ETag retain valid earlier reference",
          rewrite_same_bytes)

    # A different large file gets a new key; make the server reject its second part.
    with source.open("r+b") as stream:
        stream.write(b"different synthetic input")
    uploads = []
    adapter.on_multipart_started = lambda key, uid: uploads.append((key, uid))
    def reject_second(params, **_):
        if params["PartNumber"] == 2:
            params["PartNumber"] = 10001  # Actual invalid S3 request, not a mock response.
    adapter.client.meta.events.register("before-parameter-build.s3.UploadPart", reject_second)
    check("real server rejects part; adapter aborts incomplete transfer",
          lambda: fails(StorageError, lambda: adapter.publish("large.wav")))
    adapter.client.meta.events.unregister("before-parameter-build.s3.UploadPart", reject_second)
    assert uploads and not list(adapter.incomplete_uploads())
    failed_key, uid = uploads[0]
    assert adapter.client.list_objects_v2(Bucket=settings.bucket, Prefix=failed_key)["KeyCount"] == 0
    check("repeat abort of absent upload is idempotent", lambda: adapter.abort_upload(failed_key, uid))

    limited = S3ByteStorage(replace(settings, max_bytes=512 * MIB), ROOT)
    check("object byte limit is explicit before upload",
          lambda: fails(TransferLimitError, lambda: limited.publish("large.wav")))
    assert not list(adapter.incomplete_uploads())
    (ROOT / "preserved.wav").write_bytes(b"preserved existing bytes")
    disk_limited = S3ByteStorage(replace(settings, min_free_bytes=1 << 60), ROOT)
    check("disk reserve failure preserves destination and removes partial file",
          lambda: fails(TransferLimitError, lambda: disk_limited.materialize(ref, "preserved.wav")))
    assert (ROOT / "preserved.wav").read_bytes() == b"preserved existing bytes"
    assert not list(ROOT.glob("*.part"))

    untrusted = S3ByteStorage(replace(settings, ca_bundle=None), ROOT)
    check("untrusted TLS certificate rejected",
          lambda: fails(StorageError, lambda: untrusted.materialize(ref, "preserved.wav")))
    invalid = S3ByteStorage(replace(settings, secret_key="wrong-synthetic-secret"), ROOT)
    check("server authentication failure is explicit with no cached fallback",
          lambda: fails(StorageError, lambda: invalid.materialize(ref, "preserved.wav")))
    assert (ROOT / "preserved.wav").read_bytes() == b"preserved existing bytes"

    # Real server accepts changed bytes with unchanged claimed hash; download must detect it.
    adapter.client.put_object(Bucket=settings.bucket, Key=ref.key, Body=b"corrupt",
                              Metadata={"sha256": ref.sha256})
    head = adapter.client.head_object(Bucket=settings.bucket, Key=ref.key)
    corrupt_ref = replace(ref, size=7, etag=head["ETag"])
    check("real remote corruption detected by byte checksum",
          lambda: fails(IntegrityError, lambda: adapter.materialize(corrupt_ref, "preserved.wav")))
    assert not list(ROOT.glob("*.part"))
    check("exact object deletion and repeated deletion", lambda: (adapter.delete(ref), adapter.delete(ref)))
    check("missing object failure does not use existing local file",
          lambda: fails(StorageError, lambda: adapter.materialize(ref, "preserved.wav")))
    assert adapter.client.list_objects_v2(Bucket=settings.bucket)["KeyCount"] == 0
    assert not list(adapter.incomplete_uploads())
    assert memory()["events"]["oom"] == 0 and memory()["events"]["oom_kill"] == 0
    assert memory()["process_max_rss_bytes"] < LIMIT
    save("passed")
    print(RESULTS.read_text(), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        save("failed")
        raise
