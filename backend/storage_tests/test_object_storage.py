"""Isolated synthetic adapter tests; no API imports, real credentials or network.

Run with the storage extra and pytest, with backend/src on PYTHONPATH.
"""
import hashlib
import io
import os
import shutil
import subprocess
import tracemalloc
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from choplab.object_storage import (
    CleanupError, IntegrityError, MIB, ObjectRef, PART_BYTES, READ_BYTES,
    S3ByteStorage, S3Settings, StorageError, TransferLimitError,
)


def sdk_error(code="AccessDenied"):
    return ClientError({"Error": {"Code": code, "Message": "secret credential"}}, "Storage")


class DiskS3:
    """Disk-backed synthetic transport, so memory tests don't buffer server data."""
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.heads = {}
        self.pending = {}
        self.parts = []
        self.aborted = []
        self.puts = 0
        self.fail_part = False
        self.fail_abort = False
        self.fail_complete = False
        self.last_body = None

    def file(self, key):
        return self.root / key.rsplit("/", 1)[-1]

    def head_object(self, Bucket, Key):
        if Key not in self.heads:
            raise sdk_error("404")
        return dict(self.heads[Key])

    def put_object(self, Bucket, Key, Body, ContentLength, ContentType, Metadata):
        self.puts += 1
        self.file(Key).write_bytes(Body)
        self.heads[Key] = {"ContentLength": ContentLength, "Metadata": Metadata, "ETag": '"single"'}

    def create_multipart_upload(self, Bucket, Key, ContentType, Metadata):
        self.pending[Key] = Metadata
        self.file(Key).with_suffix(".pending").write_bytes(b"")
        return {"UploadId": "synthetic-upload"}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        assert len(Body) <= PART_BYTES
        if self.fail_part:
            raise sdk_error()
        self.parts.append((PartNumber, len(Body)))
        with self.file(Key).with_suffix(".pending").open("ab") as f:
            f.write(Body)
        return {"ETag": f'"part-{PartNumber}"'}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        assert MultipartUpload["Parts"] == [
            {"PartNumber": number, "ETag": f'"part-{number}"'} for number, _ in self.parts
        ]
        self.file(Key).with_suffix(".pending").replace(self.file(Key))
        self.heads[Key] = {"ContentLength": self.file(Key).stat().st_size,
                           "Metadata": self.pending.pop(Key), "ETag": '"multipart-not-a-hash"'}
        if self.fail_complete:
            raise sdk_error("RequestTimeout")

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.aborted.append((Key, UploadId))
        if self.fail_abort:
            raise sdk_error()
        if Key not in self.pending:
            raise sdk_error("NoSuchUpload")
        del self.pending[Key]
        self.file(Key).with_suffix(".pending").unlink()

    def get_object(self, Bucket, Key):
        head = self.head_object(Bucket, Key)
        self.last_body = self.file(Key).open("rb")
        return {**head, "Body": self.last_body}

    def delete_object(self, Bucket, Key):
        self.heads.pop(Key, None)
        self.file(Key).unlink(missing_ok=True)


@pytest.fixture
def adapter(tmp_path):
    local = tmp_path / "working"
    local.mkdir()
    settings = S3Settings("https://storage.example.invalid", "private-audio", "us-east-2",
                          "synthetic-access", "synthetic-secret")
    adapter = S3ByteStorage(settings, local)
    adapter.client = DiskS3(tmp_path / "transport")
    return adapter


def synthetic(path: Path, size: int):
    with path.open("wb") as f:
        block = b"synthetic audio bytes\0" * 1024
        while size:
            piece = block[:min(size, len(block))]
            f.write(piece)
            size -= len(piece)


@pytest.mark.parametrize("size", [0, 127, PART_BYTES, PART_BYTES + 19, 4 * PART_BYTES + 3])
def test_roundtrip_atomic_idempotent_and_bounded(adapter, size):
    source = adapter.root / "original.wav"
    synthetic(source, size)
    tracemalloc.start()
    try:
        ref = adapter.publish("original.wav")
        restored = adapter.materialize(ref, "sources/restored.wav")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 3 * PART_BYTES + 2 * READ_BYTES
    assert restored.stat().st_size == size
    with restored.open("rb") as f:
        assert hashlib.file_digest(f, "sha256").hexdigest() == ref.sha256
    assert adapter.publish("original.wav") == ref
    assert adapter.client.last_body.closed
    assert not list(adapter.root.rglob("*.part"))
    adapter.delete(ref)
    adapter.delete(ref)
    assert not adapter.client.heads


@pytest.mark.parametrize("name", [
    "../escape", "/absolute", "C:/drive.wav", r"dir\file.wav", "a/../b", "a//b",
    "./file", "a/", "a/CON.wav", "a/LPT1", "a/b.", "a/a:b", "a/%2e%2e", "a/\n",
    "a/CON .wav", "a/COM\u00b9.wav",
])
def test_unsafe_local_paths_and_keys_rejected(adapter, name):
    with pytest.raises(ValueError):
        adapter.publish(name)
    with pytest.raises(ValueError):
        replace(adapter.settings, prefix=name)


def test_namespace_and_byte_limits(adapter):
    synthetic(adapter.root / "source", 12)
    adapter.settings = replace(adapter.settings, max_bytes=11)
    with pytest.raises(TransferLimitError):
        adapter.publish("source")
    assert not adapter.client.heads and not adapter.client.pending
    ref = ObjectRef("other/objects/" + "a" * 64, 1, "a" * 64, '"etag"')
    for operation in [lambda: adapter.materialize(ref, "x"), lambda: adapter.delete(ref)]:
        with pytest.raises(ValueError):
            operation()


@pytest.mark.parametrize("endpoint", [
    "http://example.invalid", "https://user:secret@example.invalid",
    "https://example.invalid/path", "https://example.invalid?secret=x",
    "https://example.invalid#fragment",
])
def test_tls_and_private_configuration(adapter, endpoint):
    with pytest.raises(ValueError):
        replace(adapter.settings, endpoint=endpoint)
    assert "synthetic-access" not in repr(adapter.settings)
    assert "synthetic-secret" not in repr(adapter.settings)


def test_real_sdk_configuration_and_streaming_read(tmp_path):
    settings = S3Settings("https://example.invalid", "private-audio", "us-east-2", "fake", "fake")
    adapter = S3ByteStorage(settings, tmp_path)
    config = adapter.client.meta.config
    assert config.signature_version == "s3v4"
    assert config.s3["addressing_style"] == "path"
    assert config.retries["total_max_attempts"] == 3
    assert config.request_checksum_calculation == "when_required"
    data = b"synthetic round trip"
    ref = ObjectRef("choplab/objects/" + hashlib.sha256(data).hexdigest(), len(data),
                    hashlib.sha256(data).hexdigest(), '"etag"')
    raw = io.BytesIO(data)
    with Stubber(adapter.client) as stub:
        stub.add_response("get_object", {
            "ContentLength": len(data), "Metadata": {"sha256": ref.sha256},
            "ETag": ref.etag, "Body": StreamingBody(raw, len(data)),
        }, {"Bucket": settings.bucket, "Key": ref.key})
        assert adapter.materialize(ref, "restored.wav").read_bytes() == data
        stub.assert_no_pending_responses()
    assert raw.closed


def test_real_sdk_multipart_request_shapes(tmp_path):
    settings = S3Settings("https://example.invalid", "private-audio", "us-east-2", "fake", "fake")
    adapter = S3ByteStorage(settings, tmp_path)
    data = b"s" * PART_BYTES + b"tail"
    (tmp_path / "source").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    key = "choplab/objects/" + digest
    args = {"Bucket": "private-audio", "Key": key}
    with Stubber(adapter.client) as stub:
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404,
                              expected_params=args)
        stub.add_response("create_multipart_upload", {"UploadId": "id"},
                          {**args, "ContentType": "application/octet-stream",
                           "Metadata": {"sha256": digest}})
        for number, chunk in enumerate([data[:PART_BYTES], data[PART_BYTES:]], 1):
            stub.add_response("upload_part", {"ETag": f'"part-{number}"'},
                              {**args, "UploadId": "id", "PartNumber": number, "Body": chunk})
        stub.add_response("complete_multipart_upload", {"ETag": '"complete"'},
                          {**args, "UploadId": "id", "MultipartUpload": {"Parts": [
                              {"PartNumber": 1, "ETag": '"part-1"'},
                              {"PartNumber": 2, "ETag": '"part-2"'},
                          ]}})
        stub.add_response("head_object", {"ContentLength": len(data),
                                          "Metadata": {"sha256": digest}, "ETag": '"complete"'}, args)
        assert adapter.publish("source") == ObjectRef(key, len(data), digest, '"complete"')
        stub.assert_no_pending_responses()


def test_part_failure_aborts_and_sanitizes(adapter):
    synthetic(adapter.root / "source", PART_BYTES + 1)
    adapter.client.fail_part = True
    recorded = []
    adapter.on_multipart_started = lambda key, uid: recorded.append((key, uid))
    with pytest.raises(StorageError, match="Object storage publish failed") as caught:
        adapter.publish("source")
    assert "secret" not in str(caught.value)
    assert not adapter.client.pending
    assert adapter.client.aborted == recorded


def test_abort_failure_is_explicit_and_recoverable(adapter):
    synthetic(adapter.root / "source", PART_BYTES + 1)
    adapter.client.fail_part = adapter.client.fail_abort = True
    with pytest.raises(CleanupError) as caught:
        adapter.publish("source")
    assert caught.value.upload_id == "synthetic-upload"
    adapter.client.fail_abort = False
    adapter.abort_upload(caught.value.key, caught.value.upload_id)
    adapter.abort_upload(caught.value.key, caught.value.upload_id)
    assert not adapter.client.pending


def test_completion_ack_loss_does_not_delete_committed_object(adapter):
    synthetic(adapter.root / "source", PART_BYTES + 1)
    adapter.client.fail_complete = True
    with pytest.raises(StorageError):
        adapter.publish("source")
    assert adapter.client.heads and not adapter.client.pending
    ref = adapter.publish("source")
    assert adapter.materialize(ref, "retry").stat().st_size == ref.size


def test_journal_failure_aborts_before_any_part(adapter):
    synthetic(adapter.root / "source", PART_BYTES + 1)
    adapter.on_multipart_started = Mock(side_effect=RuntimeError("journal unavailable"))
    with pytest.raises(RuntimeError, match="journal unavailable"):
        adapter.publish("source")
    assert not adapter.client.parts and not adapter.client.pending


@pytest.mark.parametrize("damage", ["bytes", "truncate", "oversize", "metadata", "etag", "disconnect", "disk"])
def test_download_failure_preserves_destination_and_cleans_partial(adapter, damage, monkeypatch):
    (adapter.root / "source").write_bytes(b"synthetic original bytes")
    ref = adapter.publish("source")
    dest = adapter.root / "restored"
    dest.write_bytes(b"existing valid cache")
    server = adapter.client.file(ref.key)
    if damage == "bytes":
        server.write_bytes(b"x" * ref.size)
    elif damage == "truncate":
        server.write_bytes(b"short")
    elif damage == "oversize":
        server.write_bytes(b"x" * (ref.size + 1))
    elif damage == "metadata":
        adapter.client.heads[ref.key]["Metadata"] = {"sha256": "a" * 64}
    elif damage == "etag":
        adapter.client.heads[ref.key]["ETag"] = '"other"'
    elif damage == "disconnect":
        original = adapter.client.get_object
        def broken(**args):
            response = original(**args)
            response["Body"] = Mock(read=Mock(side_effect=OSError("disconnected")),
                                    close=adapter.client.last_body.close)
            return response
        adapter.client.get_object = broken
    else:
        monkeypatch.setattr(shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(100, 100, 0))
    with pytest.raises(StorageError):
        adapter.materialize(ref, "restored")
    assert dest.read_bytes() == b"existing valid cache"
    assert adapter.client.last_body.closed
    assert not list(adapter.root.rglob("*.part"))


def test_missing_and_permission_errors_are_not_local_fallback(adapter):
    (adapter.root / "source").write_bytes(b"synthetic")
    ref = adapter.publish("source")
    adapter.client.delete_object(Bucket=adapter.settings.bucket, Key=ref.key)
    (adapter.root / "cached").write_bytes(b"cached")
    with pytest.raises(StorageError):
        adapter.materialize(ref, "cached")
    adapter.client.head_object = Mock(side_effect=sdk_error())
    with pytest.raises(StorageError):
        adapter.publish("source")
    adapter.client.delete_object = Mock(side_effect=sdk_error())
    with pytest.raises(StorageError):
        adapter.delete(ref)


def test_source_mutation_aborts(adapter):
    synthetic(adapter.root / "source", PART_BYTES + 1)
    adapter.on_multipart_started = lambda *_: (adapter.root / "source").write_bytes(b"changed")
    with pytest.raises(IntegrityError):
        adapter.publish("source")
    assert adapter.client.aborted and not adapter.client.pending and not adapter.client.heads


def test_existing_export_names_remain_supported(adapter):
    name = "exports/My beat \u00e9.wav"
    source = adapter.root.joinpath(*name.split("/"))
    source.parent.mkdir()
    source.write_bytes(b"synthetic audio")
    ref = adapter.publish(name)
    assert adapter.materialize(ref, "restore/Mon beat \u00e9.wav").read_bytes() == b"synthetic audio"


def test_junction_or_symlink_escape_rejected(adapter, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source").write_bytes(b"outside")
    link = adapter.root / "linked"
    if os.name == "nt":
        subprocess.run([os.environ["ComSpec"], "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            adapter.publish("linked/source")
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_existing_object_mismatch_is_not_overwritten(adapter):
    (adapter.root / "source").write_bytes(b"synthetic")
    ref = adapter.publish("source")
    adapter.client.heads[ref.key]["ContentLength"] += 1
    with pytest.raises(IntegrityError):
        adapter.publish("source")
    assert adapter.client.puts == 1


def test_recovery_pagination_is_scoped(adapter):
    paginator = Mock()
    paginator.paginate.return_value = [
        {"Uploads": [{"Key": "choplab/objects/a", "UploadId": "1"}]}, {},
        {"Uploads": [{"Key": "choplab/objects/b", "UploadId": "2"}]},
    ]
    adapter.client.get_paginator = Mock(return_value=paginator)
    assert [u["UploadId"] for u in adapter.incomplete_uploads()] == ["1", "2"]
    paginator.paginate.assert_called_once_with(Bucket="private-audio", Prefix="choplab/objects/")
    with pytest.raises(ValueError):
        adapter.abort_upload("another/objects/a", "1")
