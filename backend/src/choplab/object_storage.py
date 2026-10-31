"""Optional, synchronous S3 byte storage; not wired into API or job persistence.

Install the ``storage`` extra. Construct explicitly with branch-bound credentials
and a private existing bucket; there is no environment discovery or local fallback.
Call blocking methods off the API event loop. Local paths are root-relative and
the root must be private to the process (not writable by untrusted users).

Publish a closed, stable file, then commit its ObjectRef in metadata. Downloads
verify SHA-256 before atomically replacing the destination; DSP still uses files.
Keys are content-addressed within a caller-selected namespace. Same-byte retries
are idempotent, but objects can be shared: deletion requires metadata reference
accounting, writer fencing and a durable outbox. A failed completion/verification
may leave a committed orphan; never delete it blindly. Persist pending upload IDs
via on_multipart_started before sending parts for crash recovery. Recovery must
fence writers before aborting stale uploads. No cross-resource transaction,
multi-instance cache locking, bucket creation, ACL changes or public URLs here.

Neon reference: https://neon.com/docs/storage/s3-compatibility
Core GET/HEAD/PUT/DELETE and multipart are documented; versioning and lifecycle
are not enforced. Live endpoint behavior still requires isolated acceptance tests.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

MIB = 1 << 20
PART_BYTES = 8 * MIB
READ_BYTES = MIB
MAX_OBJECT_BYTES = 10 * (1 << 30)


class StorageError(RuntimeError):
    """Safe operational error; raw SDK messages may contain credentials/URLs."""


class IntegrityError(StorageError):
    pass


class TransferLimitError(StorageError):
    pass


class CleanupError(StorageError):
    def __init__(self, key: str, upload_id: str):
        super().__init__("Multipart transfer failed and abort failed; recovery is required")
        self.key = key
        self.upload_id = upload_id


def _safe_name(value: str, *, local: bool = False) -> str:
    parts = value.split("/")
    pattern = r"[\w-][\w .-]*" if local else r"[A-Za-z0-9_-][A-Za-z0-9_.-]*"
    if (not value or len(value.encode("utf-8")) > 900
            or any(not re.fullmatch(pattern, p)
                   or p.endswith((".", " "))
                   or p.split(".")[0].rstrip(" ").upper() in
                   {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                    *(f"LPT{i}" for i in range(10)),
                    *(f"{device}{i}" for device in ("COM", "LPT") for i in "\u00b9\u00b2\u00b3")}
                   for p in parts)):
        raise ValueError("Expected a safe, slash-separated relative name")
    return value


@dataclass(frozen=True)
class ObjectRef:
    """Persist all fields; ETag is diagnostic, SHA-256 identifies the bytes."""
    key: str
    size: int
    sha256: str
    etag: str

    def __post_init__(self):
        _safe_name(self.key)
        if (not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
                or not 0 <= self.size <= MAX_OBJECT_BYTES or not self.etag):
            raise ValueError("Invalid object reference")


class ByteStorage(Protocol):
    def publish(self, relative_path: str) -> ObjectRef: ...
    def materialize(self, ref: ObjectRef, relative_path: str) -> Path: ...
    def delete(self, ref: ObjectRef) -> None: ...


@dataclass(frozen=True)
class S3Settings:
    endpoint: str
    bucket: str
    region: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    prefix: str = "choplab"
    max_bytes: int = 1024 * MIB
    min_free_bytes: int = 0
    ca_bundle: str | None = None

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ("", "/")):
            raise ValueError("Storage endpoint must be an HTTPS origin without credentials")
        if (not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket)
                or not self.region or not self.access_key or not self.secret_key):
            raise ValueError("Explicit bucket, region and storage credentials are required")
        _safe_name(self.prefix)
        if len(self.prefix) > 800 or not 0 < self.max_bytes <= MAX_OBJECT_BYTES or self.min_free_bytes < 0:
            raise ValueError("Invalid storage limits")
        if self.ca_bundle is not None and not Path(self.ca_bundle).is_file():
            raise ValueError("Storage CA bundle must be an existing certificate file")


class S3ByteStorage:
    def __init__(self, settings: S3Settings, root: Path, *,
                 on_multipart_started: Callable[[str, str], None] | None = None):
        try:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:
            raise StorageError("S3 storage requires the choplab[storage] extra") from None
        self.settings = settings
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Storage working root must be a directory")
        self.on_multipart_started = on_multipart_started
        self._sdk_errors = (BotoCoreError, ClientError)
        self._client_error = ClientError
        self.client = boto3.client(
            "s3", endpoint_url=settings.endpoint, region_name=settings.region,
            aws_access_key_id=settings.access_key, aws_secret_access_key=settings.secret_key,
            verify=settings.ca_bundle or True, config=Config(
                signature_version="s3v4", s3={"addressing_style": "path"},
                connect_timeout=10, read_timeout=60,
                retries={"mode": "standard", "total_max_attempts": 3},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    @contextmanager
    def _operation(self, name: str):
        try:
            yield
        except self._sdk_errors:
            raise StorageError(f"Object storage {name} failed") from None
        except OSError:
            raise StorageError(f"Local storage {name} failed") from None

    def _path(self, relative_path: str) -> Path:
        p = self.root.joinpath(*_safe_name(relative_path, local=True).split("/"))
        for part in (p, *p.parents):
            if part == self.root:
                break
            if part.is_symlink() or part.is_junction():
                raise ValueError("Symlinks and junctions are not allowed in storage paths")
        if not p.resolve().is_relative_to(self.root):
            raise ValueError("Local path escapes the working root")
        return p

    def _key(self, digest: str) -> str:
        return f"{self.settings.prefix}/objects/{digest}"

    def _validate_ref(self, ref: ObjectRef):
        if ref.key != self._key(ref.sha256):
            raise ValueError("Object reference is outside this storage namespace")
        if ref.size > self.settings.max_bytes:
            raise TransferLimitError("Object exceeds configured byte limit")

    def _fingerprint(self, stream) -> tuple[int, str]:
        size, digest = 0, hashlib.sha256()
        while chunk := stream.read(READ_BYTES):
            size += len(chunk)
            if size > self.settings.max_bytes:
                raise TransferLimitError("File exceeds configured byte limit")
            digest.update(chunk)
        return size, digest.hexdigest()

    def _head(self, key: str):
        try:
            return self.client.head_object(Bucket=self.settings.bucket, Key=key)
        except self._client_error as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    @staticmethod
    def _matches(head: dict, size: int, digest: str):
        if head["ContentLength"] != size or head.get("Metadata", {}).get("sha256") != digest:
            raise IntegrityError("Object size or SHA-256 metadata does not match")

    def publish(self, relative_path: str) -> ObjectRef:
        """Bounded two-pass upload; a stable closed file is a caller prerequisite."""
        with self._operation("publish"), self._path(relative_path).open("rb") as stream:
            size, digest = self._fingerprint(stream)
            key = self._key(digest)
            head = self._head(key)
            if head is not None:
                self._matches(head, size, digest)
                return ObjectRef(key, size, digest, head["ETag"])
            stream.seek(0)
            args = {"Bucket": self.settings.bucket, "Key": key}
            metadata = {"sha256": digest}
            if size <= PART_BYTES:
                body = stream.read(PART_BYTES + 1)
                if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
                    raise IntegrityError("Source changed during upload")
                self.client.put_object(**args, Body=body, ContentLength=size,
                                       ContentType="application/octet-stream", Metadata=metadata)
            else:
                upload = self.client.create_multipart_upload(
                    **args, ContentType="application/octet-stream", Metadata=metadata)
                upload_id = upload["UploadId"]
                try:
                    if self.on_multipart_started:
                        self.on_multipart_started(key, upload_id)
                    parts, sent, check = [], 0, hashlib.sha256()
                    while chunk := stream.read(PART_BYTES):
                        sent += len(chunk)
                        if sent > size:
                            raise IntegrityError("Source grew during upload")
                        check.update(chunk)
                        number = len(parts) + 1
                        part = self.client.upload_part(**args, UploadId=upload_id,
                                                       PartNumber=number, Body=chunk)
                        parts.append({"ETag": part["ETag"], "PartNumber": number})
                    if sent != size or check.hexdigest() != digest:
                        raise IntegrityError("Source changed during upload")
                    self.client.complete_multipart_upload(
                        **args, UploadId=upload_id, MultipartUpload={"Parts": parts})
                except BaseException:
                    try:
                        self.abort_upload(key, upload_id)
                    except StorageError:
                        raise CleanupError(key, upload_id) from None
                    raise
            head = self._head(key)
            if head is None:
                raise IntegrityError("Uploaded object is not visible")
            self._matches(head, size, digest)
            return ObjectRef(key, size, digest, head["ETag"])

    def materialize(self, ref: ObjectRef, relative_path: str) -> Path:
        """Never exposes a partial download or trusts an existing local cache file."""
        self._validate_ref(ref)
        dest = self._path(relative_path)
        with self._operation("download"):
            response = self.client.get_object(Bucket=self.settings.bucket, Key=ref.key)
            body = response["Body"]
            tmp = None
            try:
                self._matches(response, ref.size, ref.sha256)
                dest.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix=".storage-", suffix=".part", dir=dest.parent)
                tmp = Path(name)
                with os.fdopen(fd, "wb") as out:
                    size, digest = 0, hashlib.sha256()
                    while chunk := body.read(READ_BYTES):
                        size += len(chunk)
                        if size > ref.size:
                            raise IntegrityError("Download exceeds declared size")
                        if shutil.disk_usage(dest.parent).free < self.settings.min_free_bytes + len(chunk):
                            raise TransferLimitError("Local storage reserve reached")
                        out.write(chunk)
                        digest.update(chunk)
                    if size != ref.size or digest.hexdigest() != ref.sha256:
                        raise IntegrityError("Downloaded bytes do not match the object reference")
                    out.flush()
                    os.fsync(out.fileno())
                self._path(relative_path)
                tmp.replace(dest)
                return dest
            finally:
                try:
                    body.close()
                finally:
                    if tmp is not None:
                        tmp.unlink(missing_ok=True)

    def delete(self, ref: ObjectRef) -> None:
        """Idempotent exact-key deletion; caller must fence writes and check references."""
        self._validate_ref(ref)
        with self._operation("delete"):
            self.client.delete_object(Bucket=self.settings.bucket, Key=ref.key)

    def abort_upload(self, key: str, upload_id: str) -> None:
        if not key.startswith(f"{self.settings.prefix}/objects/"):
            raise ValueError("Upload is outside this storage namespace")
        _safe_name(key)
        with self._operation("abort"):
            try:
                self.client.abort_multipart_upload(
                    Bucket=self.settings.bucket, Key=key, UploadId=upload_id)
            except self._client_error as e:
                if e.response["Error"]["Code"] != "NoSuchUpload":
                    raise

    def incomplete_uploads(self) -> Iterator[dict]:
        """Paginated recovery inventory; do not abort live writers by age alone."""
        with self._operation("list incomplete uploads"):
            pages = self.client.get_paginator("list_multipart_uploads").paginate(
                Bucket=self.settings.bucket, Prefix=f"{self.settings.prefix}/objects/")
            for page in pages:
                yield from page.get("Uploads", [])
