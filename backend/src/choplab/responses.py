"""Audio downloads retain normal range semantics without retaining large page-cache copies."""
from __future__ import annotations

import os

from anyio import CancelScope
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse


class AudioFileResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        if not hasattr(os, "posix_fadvise") or not hasattr(os, "POSIX_FADV_DONTNEED"):
            await super().__call__(scope, receive, send)
            return
        # Keep the inode open through existing response/background cleanup, even if it unlinks the path.
        with open(self.path, "rb") as file:
            async def bounded_send(message):
                await send(message)
                if message["type"] == "http.response.body" and message.get("body"):
                    await run_in_threadpool(os.posix_fadvise, file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            try:
                await super().__call__(scope, receive, bounded_send)
            finally:
                def release():
                    os.fsync(file.fileno())
                    os.posix_fadvise(file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                with CancelScope(shield=True):
                    await run_in_threadpool(release)
