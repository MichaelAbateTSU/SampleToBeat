import asyncio
import errno
import hashlib

import pytest
from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import ClientDisconnect, Request

from choplab.uploads import _CacheParser, _CacheSpool, upload_route


def body(data=b"x" * (2 << 20), suffix=True):
    prefix = (b"--test\r\nContent-Disposition: form-data; name=\"file\"; "
              b'filename="file.wav"\r\n\r\n')
    return prefix + data + (b"\r\n--test--\r\n" if suffix else b"")


def parser(data, *, limit=4 << 20, failure=None):
    async def stream():
        for i in range(0, len(data), 65536):
            yield data[i:i+65536]
            if failure and i > 1 << 20:
                raise failure
    return _CacheParser(Headers({"content-type": "multipart/form-data; boundary=test"}),
                        stream(), upload_limit=limit)


def test_real_parser_rollover_digest_and_close():
    p = parser(body())
    async def run():
        form = await p.parse()
        f = form["file"]
        assert isinstance(f.file, _CacheSpool) and f.file._rolled
        assert f.size == 2 << 20
        assert hashlib.sha256(await f.read()).digest() == hashlib.sha256(b"x"*(2<<20)).digest()
        await form.close()
    asyncio.run(run())
    assert all(f.closed for f in p._files_to_close_on_error)


@pytest.mark.parametrize("failure", [ClientDisconnect(), asyncio.CancelledError(),
                                     OSError(errno.ENOSPC, "synthetic disk full")])
def test_partial_failure_and_cancellation_close_spools(failure):
    p = parser(body(), failure=failure)
    with pytest.raises(type(failure)):
        asyncio.run(p.parse())
    assert p._files_to_close_on_error and all(f.closed for f in p._files_to_close_on_error)


def test_truncated_file_does_not_leak():
    p = parser(body(suffix=False))
    async def run():
        form = await p.parse()
        await form.close()
    asyncio.run(run())
    assert p._files_to_close_on_error and all(f.closed for f in p._files_to_close_on_error)


def test_oversize_checked_while_parsing_and_cleanup():
    p = parser(body(), limit=1 << 20)
    with pytest.raises(HTTPException) as error:
        asyncio.run(p.parse())
    assert error.value.status_code == 413
    assert all(f.closed for f in p._files_to_close_on_error)


def test_malformed_disposition_cleanup():
    p = parser(body().replace(b'name="file"', b'namex="file"'))
    with pytest.raises(MultiPartException):
        asyncio.run(p.parse())
    assert all(f.closed for f in p._files_to_close_on_error)


def test_route_form_cleanup_on_endpoint_error():
    app = FastAPI()
    app.router.route_class = upload_route(4 << 20)
    seen = []
    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        seen.append(file.file)
        raise HTTPException(409, "synthetic endpoint failure")
    with TestClient(app) as c:
        response = c.post("/upload", files={"file": ("a.wav", b"x"*(2<<20))})
    assert response.status_code == 409
    assert seen[0].closed


def test_urlencoded_and_json_passthrough():
    app = FastAPI()
    app.router.route_class = upload_route(4 << 20)
    @app.post("/form")
    async def form(request: Request):
        return dict(await request.form())
    with TestClient(app) as c:
        assert c.post("/form", data={"x":"y"}).json() == {"x":"y"}


def test_file_api_error_mapping(monkeypatch):
    app = FastAPI()
    app.router.route_class = upload_route(4 << 20)
    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        return {"n": file.size}
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic")
    monkeypatch.setattr(_CacheSpool, "write", full)
    with TestClient(app) as c:
        response = c.post("/upload", files={"file":("a.wav", b"x")})
    assert response.status_code == 507
    assert response.json()["detail"] == "Temporary upload storage is full."


def test_no_fadvise_standard_local_file(monkeypatch):
    import choplab.uploads as uploads
    if hasattr(uploads.os, "posix_fadvise"):
        monkeypatch.delattr(uploads.os, "posix_fadvise")
    app = FastAPI()
    app.router.route_class = upload_route(1)
    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        return {"n":file.size,"custom":isinstance(file.file,_CacheSpool)}
    with TestClient(app) as c:
        assert c.post("/upload",files={"file":("a.wav",b"xx")}).json()=={"n":2,"custom":False}


def test_multiple_file_parts_and_field_limits():
    data = body(b"a").replace(b"--test--\r\n", b"") + (
        b'--test\r\nContent-Disposition: form-data; name="other"; filename="b"\r\n\r\n'
        b"b\r\n--test--\r\n")
    p = parser(data)
    async def run():
        form = await p.parse()
        assert await form["file"].read() == b"a"
        assert await form["other"].read() == b"b"
        await form.close()
    asyncio.run(run())
    assert all(f.closed for f in p._files_to_close_on_error)


def test_cache_window_calls_real_helper_in_worker(monkeypatch):
    import choplab.uploads as uploads
    calls = []
    original = uploads._release_file_cache
    def record(stream, **kwargs):
        import threading
        calls.append((kwargs.get("written", False), threading.current_thread().name))
        return original(stream, **kwargs)
    monkeypatch.setattr(uploads, "_release_file_cache", record)
    p = parser(body(b"x" * (16 << 20)), limit=32 << 20)
    async def run():
        form = await p.parse()
        f = form["file"]
        while await f.read(1 << 20):
            pass
        await form.close()
    asyncio.run(run())
    assert any(written for written, _ in calls)
    assert any(not written for written, _ in calls)
    assert all(name != "MainThread" for _, name in calls)


def test_actual_api_hooks_all_three_multipart_routes(monkeypatch):
    import os
    # Harness creates synthetic hosted settings only; no product startup or DB.
    os.environ.setdefault("TEST_TOKEN", "test-only-" * 8)
    from upload_tests import server
    api = server.api
    routes = {r.path: r for r in api.app.routes if hasattr(r, "endpoint")}
    for path in ("/api/projects/{id}/sources", "/api/projects/{id}/bass-sample",
                 "/api/kits/{kit}/{inst}"):
        assert type(routes[path]).__name__ == "UploadRoute"
        import inspect
        assert "file" in inspect.signature(routes[path].endpoint).parameters


def test_actual_save_limit_and_reserve_remove_part(monkeypatch, tmp_path):
    import io
    from upload_tests import server
    from fastapi import HTTPException as FastAPIHTTPException
    from starlette.datastructures import UploadFile as StarletteUploadFile
    api = server.api
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "MAX_UPLOAD", 1 << 20)
    path = tmp_path / "file.bin"
    f = StarletteUploadFile(io.BytesIO(b"x" * ((1 << 20) + 1)))
    with pytest.raises(FastAPIHTTPException) as exc:
        asyncio.run(api._save_upload(f, path))
    assert exc.value.status_code == 413
    assert not path.exists() and not path.with_suffix(".part").exists()
    monkeypatch.setattr(api, "MIN_FREE", 1 << 62)
    f = StarletteUploadFile(io.BytesIO(b"x"))
    with pytest.raises(FastAPIHTTPException) as exc:
        asyncio.run(api._save_upload(f, path))
    assert exc.value.status_code == 507
    assert not path.exists() and not path.with_suffix(".part").exists()


def test_field_limit_preserved():
    data = b'--test\r\nContent-Disposition: form-data; name="field"\r\n\r\nxx\r\n--test--\r\n'
    p = parser(data)
    p.max_part_size = 1
    with pytest.raises(MultiPartException):
        asyncio.run(p.parse())


def test_other_spool_oserror_is_logged_server_error(monkeypatch, caplog):
    app = FastAPI()
    app.router.route_class = upload_route(4 << 20)
    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        return {"n": file.size}
    def failed(*args, **kwargs):
        raise OSError(errno.EIO, "synthetic")
    monkeypatch.setattr(_CacheSpool, "write", failed)
    with TestClient(app) as c:
        response = c.post("/upload", files={"file":("a.wav",b"x")})
    assert response.status_code == 500
    assert "Multipart upload spool failed" in caplog.text


def test_endpoint_cancellation_closes_form():
    import concurrent.futures
    app = FastAPI()
    app.router.route_class = upload_route(4 << 20)
    seen = []
    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        seen.append(file.file)
        raise asyncio.CancelledError()
    with TestClient(app) as c:
        with pytest.raises(concurrent.futures.CancelledError):
            c.post("/upload",files={"file":("a.wav",b"x"*(2<<20))})
    assert seen and seen[0].closed
