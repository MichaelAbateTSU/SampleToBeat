"""Exercise the protected Docker container using synthetic audio and disposable test credentials."""
import argparse
import base64
import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

BASE = "http://127.0.0.1:8765"
AUTH = base64.b64encode(b"owner:workflow-test-password").decode()
CONTEXT = None


def request(method, path, body=None, headers=None, *, anonymous=False, raw=None):
    req_headers = {"X-Forwarded-Proto": "https"}
    if not anonymous:
        req_headers["Authorization"] = f"Basic {AUTH}"
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    req_headers.update(headers or {})
    data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=180, context=CONTEXT) as response:
        return response.status, response.headers, response.read()


def json_request(method, path, body=None):
    return json.loads(request(method, path, body)[2])


def done(job):
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = json_request("GET", f"/api/jobs/{job['id']}")
        if state["status"] == "completed":
            return state
        if state["status"] in ("failed", "canceled", "interrupted"):
            raise RuntimeError(f"Audio job failed: {state}")
        time.sleep(0.3)
    raise TimeoutError(f"Audio job exceeded five minutes: {job['id']}")


def main():
    global BASE, CONTEXT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    parser.add_argument("--after-restart", action="store_true")
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--ca", type=Path)
    parser.add_argument("--upload-limits", action="store_true")
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.hostname not in ("127.0.0.1", "localhost") or url.scheme not in ("http", "https"):
        parser.error("This smoke test uses disposable credentials and must target a loopback test container.")
    BASE = args.base_url.rstrip("/")
    CONTEXT = ssl.create_default_context(cafile=str(args.ca)) if args.ca else None
    if args.after_restart:
        project = json.loads(args.state.read_text())
        pid = project["id"]
        assert json_request("GET", f"/api/projects/{pid}")["name"] == "Container persistence check"
        assert len(request("GET", f"/api/sources/{project['source']}/audio")[2]) > 100
        assert len(json_request("GET", f"/api/projects/{pid}/exports")) == 2
        request("DELETE", f"/api/projects/{pid}")
        print("Container restart preserved project, source audio and both exports.")
        return

    assert json_request("GET", "/api/health")["ok"]
    try:
        request("GET", "/", anonymous=True)
    except urllib.error.HTTPError as e:
        assert e.code == 401
    else:
        raise AssertionError("Anonymous studio access was accepted")
    assert b'<div id="root">' in request("GET", "/")[2]
    assert b'<div id="root">' in request("GET", "/projects/a-nested-spa-path")[2]
    caps = json_request("GET", "/api/system/capabilities")
    assert caps["hosted"] and caps["ffmpeg"] and caps["max_audio_seconds"] == 300
    pid = json_request("POST", "/api/projects", {"name": "Container persistence check"})["id"]
    if args.upload_limits:
        boundary = "choplab-upload-boundary"
        prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                  "filename=\"bounds.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        body = prefix + bytes(100 * (1 << 20)) + tail
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        try:
            request("POST", f"/api/projects/{pid}/sources", headers=headers, raw=body)
        except urllib.error.HTTPError as e:
            # Exact 100 MiB reached the audio validator, not an undersized proxy limit.
            assert e.code == 400 and b"decodable audio" in e.read()
        else:
            raise AssertionError("Zero-filled data unexpectedly decoded as audio")
        extra = (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"padding\"\r\n\r\n").encode()
        body = body[:-len(tail)] + extra + bytes(2 * (1 << 20)) + tail
        try:
            request("POST", f"/api/projects/{pid}/sources", headers=headers, raw=body)
        except urllib.error.HTTPError as e:
            assert e.code == 413, f"Proxy failed to cap 102 MiB multipart body: {e.code}"
        else:
            raise AssertionError("Proxy accepted an oversized multipart body")
        request("DELETE", f"/api/projects/{pid}")
        print("Real TLS proxy accepted 100 MiB + multipart overhead and rejected 102 MiB multipart body.")
        return
    imported = json_request("POST", f"/api/projects/{pid}/demo", {})
    done(imported["job"])
    sid = imported["source"]["id"]
    status, headers, audio = request("GET", f"/api/sources/{sid}/audio", headers={"Range": "bytes=0-63"})
    assert status == 206 and len(audio) == 64 and headers["Content-Range"].startswith("bytes 0-63/")
    assert b'"status": "completed"' in request("GET", f"/api/jobs/{imported['job']['id']}/events")[2]
    split = done(json_request("POST", f"/api/sources/{sid}/separate", {"model_id": "builtin-hpss"}))
    assert set(split["result"]["stems"]) == {"harmonic", "percussive"}
    done(json_request("POST", f"/api/projects/{pid}/generate", {"bars": 8, "variations": 1}))
    variation = json_request("GET", f"/api/projects/{pid}/variations")[0]
    project = json_request("POST", f"/api/variations/{variation['id']}/open-in-studio", {})
    preview = json_request("POST", f"/api/projects/{pid}/preview",
                           {"scope": "loop", "loop_start_bar": 0, "loop_bars": 2})
    assert len(request("GET", preview["url"])[2]) > 100
    for fmt in ("wav24", "mp3"):
        exported = json_request("POST", f"/api/projects/{pid}/export",
                                {"format": fmt, "filename": "container-check", "stems": False})
        done(exported["job"])
        record = json_request("GET", f"/api/exports/{exported['export_id']}")
        file = next(f for f in record["files"] if f["kind"] == "mix")
        assert not file["check"]["silent"]
        assert len(request("GET", f"/api/exports/{record['id']}/download")[2]) > 100
    args.state.write_text(json.dumps({"id": project["id"], "source": sid}))
    print("Native container journey passed: owner login, SPA, demo import, HPSS, generation, HQ, SSE, ranges, WAV/MP3.")


if __name__ == "__main__":
    main()
