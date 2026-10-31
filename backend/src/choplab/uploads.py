"""Route-scoped multipart spools with bounded Linux page-cache residency."""
from __future__ import annotations

import errno
import os
import tempfile
from contextlib import aclosing

from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request

from .core import log
from .object_storage import _release_file_cache

_CACHE_WINDOW = 8 << 20


class _CacheSpool(tempfile.SpooledTemporaryFile):
    def __init__(self, *, max_size: int, limit: int):
        super().__init__(max_size=max_size, mode="w+b")
        self._limit = limit
        self._written = 0
        self._pending_write = 0
        self._pending_read = 0

    def write(self, data):
        if self._written + len(data) > self._limit:
            raise HTTPException(413, f"File is larger than {self._limit // (1 << 20)} MiB.")
        n = super().write(data)
        self._written += n
        self._pending_write += n
        if self._rolled and self._pending_write >= _CACHE_WINDOW:
            _release_file_cache(self, written=True)
            self._pending_write = 0
        return n

    def seek(self, offset, whence=0):
        if self._rolled and self._pending_write:
            _release_file_cache(self, written=True)
            self._pending_write = 0
        return super().seek(offset, whence)

    def read(self, size=-1):
        data = super().read(size)
        self._pending_read += len(data)
        if self._rolled and (self._pending_read >= _CACHE_WINDOW or not data):
            _release_file_cache(self)
            self._pending_read = 0
        return data


class _CacheParser(MultiPartParser):
    def __init__(self, *args, upload_limit: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.upload_limit = upload_limit

    def on_headers_finished(self):
        super().on_headers_finished()
        upload = self._current_part.file
        if upload is not None:
            # Replace only this parser's newly-created, still-empty spool.
            # Keep Starlette's own UploadFile/threadpool and error ownership.
            original = upload.file
            spool = _CacheSpool(max_size=self.spool_max_size, limit=self.upload_limit)
            self._files_to_close_on_error[-1] = spool
            upload.file = spool
            original.close()

    async def parse(self):
        form = await super().parse()
        retained = {id(value.file) for _, value in form.multi_items()
                    if isinstance(value, UploadFile)}
        # Some truncated bodies finalize without raising but never finish a part.
        # Such files do not belong to FormData and must not escape its cleanup.
        for spool in self._files_to_close_on_error:
            if id(spool) not in retained:
                await run_in_threadpool(spool.close)
        return form


class _UploadRequest(Request):
    def __init__(self, *args, upload_limit: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.upload_limit = upload_limit

    async def _get_form(self, *, max_files=1000, max_fields=1000, max_part_size=1 << 20):
        content_type = self.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "multipart/form-data":
            return await super()._get_form(max_files=max_files, max_fields=max_fields,
                                           max_part_size=max_part_size)
        if self._form is None:
            try:
                async with aclosing(self.stream()) as stream:
                    self._form = await _CacheParser(
                        self.headers, stream, upload_limit=self.upload_limit,
                        max_files=max_files, max_fields=max_fields,
                        max_part_size=max_part_size,
                    ).parse()
            except MultiPartException as exc:
                raise HTTPException(400, exc.message) from exc
            except OSError as exc:
                if exc.errno == errno.ENOSPC:
                    log.warning("Multipart upload spool exhausted temporary storage")
                    raise HTTPException(507, "Temporary upload storage is full.") from exc
                log.exception("Multipart upload spool failed")
                raise HTTPException(500, "Temporary upload storage failed.") from exc
        return self._form


def upload_route(max_upload: int) -> type[APIRoute]:
    """Install before route declarations; authentication middleware remains outermost.

    Starlette's parser callback and Request form hooks are deliberately confined
    here and covered against the installed parser, never globally monkeypatched.
    Non-Linux/local callers can continue using standard APIRoute unchanged.
    """
    class UploadRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()

            async def handle(request: Request):
                if hasattr(os, "posix_fadvise"):
                    request = _UploadRequest(request.scope, request.receive,
                                             upload_limit=max_upload)
                return await handler(request)

            return handle

    return UploadRoute
