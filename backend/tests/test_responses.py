import os

import pytest
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.routing import Route
from starlette.testclient import TestClient

from choplab.responses import AudioFileResponse


@pytest.mark.parametrize("remove", [False, True])
def test_audio_ranges_release_open_inode_after_background_cleanup(tmp_path, monkeypatch, remove):
    if remove and os.name == "nt":
        pytest.skip("Open-inode unlink semantics are exercised on native Linux.")
    source = tmp_path / "audio.wav"
    source.write_bytes(b"0123456789")
    calls = []
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(("sync", os.fstat(fd).st_size)))
    monkeypatch.setattr(os, "posix_fadvise", lambda fd, *args: calls.append(("release", os.fstat(fd).st_size)),
                        raising=False)

    async def audio(request):
        task = BackgroundTask(source.unlink) if remove else None
        return AudioFileResponse(source, media_type="audio/wav", background=task)

    with TestClient(Starlette(routes=[Route("/", audio)])) as client:
        response = client.get("/", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert calls == [("release", 10), ("sync", 10), ("release", 10)]
