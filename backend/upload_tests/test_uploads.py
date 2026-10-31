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
