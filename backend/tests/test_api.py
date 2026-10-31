"""End-to-end through HTTP: the same calls the UI makes, import -> export."""
import io

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from choplab import jobs
from choplab.api import app


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


def done(c, job):
    j = jobs.wait(job["id"], timeout=300)
    assert j["status"] == "completed", j.get("error_message")
    return c.get(f"/api/jobs/{job['id']}").json()


def wav(c, url):
    r = c.get(url)
    assert r.status_code == 200, r.text
    y, sr = sf.read(io.BytesIO(r.content))
    assert sr == 48000 and len(y) and np.isfinite(y).all()
    return y


def test_full_workflow(c):
    assert c.get("/api/health").json()["ok"] is True
    pid = c.post("/api/projects", json={"name": "API test"}).json()["id"]

    r = c.post(f"/api/projects/{pid}/demo").json()
    done(c, r["job"])
    sid = r["source"]["id"]
    an = c.get(f"/api/sources/{sid}/analysis").json()
    assert abs(an["bpm"] - 92) < 0.5 and an["key"] == "A minor"
    assert len(c.get(f"/api/sources/{sid}/waveform").json()["peaks"]) > 100
    wav(c, f"/api/sources/{sid}/audio")
    seek = c.get(f"/api/sources/{sid}/audio", headers={"Range": "bytes=0-63"})
    assert seek.status_code == 206 and len(seek.content) == 64
    assert seek.headers["content-range"].startswith("bytes 0-63/")
    events = c.get(f"/api/jobs/{r['job']['id']}/events")
    assert events.headers["content-type"].startswith("text/event-stream")
    assert '"status": "completed"' in events.text

    samples = c.get(f"/api/projects/{pid}/samples").json()
    assert len(samples) >= 6 and all(s["explanation"] for s in samples)
    wav(c, f"/api/samples/{samples[0]['id']}/audio?bpm=100")
    assert c.get(f"/api/samples/{samples[0]['id']}/audio?bpm=999").status_code == 400

    # manual sample, edit, transform
    m = c.post(f"/api/projects/{pid}/samples", json={"source_id": sid, "start": 1.0, "end": 3.6}).json()
    assert m["origin"] == "manual"
    m = c.patch(f"/api/samples/{m['id']}", json={"name": "my chop", "favorite": True}).json()
    assert m["name"] == "my chop" and m["favorite"]
    t = c.post(f"/api/samples/{m['id']}/transform", json={"pitch_semitones": -3, "reverse": True}).json()
    assert t["transform"]["pitch_semitones"] == -3 and t["transform"]["reverse"]
    wav(c, t["preview"]["url"])
    for bad_t in ({"semitones": 1}, {"gain_db": 1e9}, {"fade_in_ms": -1}, {"stretch_mode": "warp"}):
        assert c.post(f"/api/samples/{m['id']}/transform", json=bad_t).status_code == 400, bad_t
    bad = c.post(f"/api/projects/{pid}/samples", json={"source_id": sid, "start": 1, "end": 2, "stems": ["vocals"]})
    assert bad.status_code == 400

    # user override re-runs analysis and keeps the user's choices
    j = c.patch(f"/api/sources/{sid}/analysis", json={"key": "C major"}).json()
    done(c, j)
    an2 = c.get(f"/api/sources/{sid}/analysis").json()
    assert an2["key"] == "C major" and an2["detected_key"] == "A minor"
    assert any(s["id"] == m["id"] for s in c.get(f"/api/projects/{pid}/samples").json())  # manual samples survive

    # generate -> variations -> studio
    done(c, c.post(f"/api/projects/{pid}/generate", json={"variations": 2, "bars": 8}).json())
    vs = c.get(f"/api/projects/{pid}/variations").json()
    assert len(vs) == 2 and all("arrangement" not in v for v in vs)
    wav(c, f"/api/variations/{vs[0]['id']}/audio")
    proj = c.post(f"/api/variations/{vs[0]['id']}/open-in-studio").json()
    arr = proj["arrangement"]
    assert c.get(f"/api/projects/{pid}/arrangement").json() == arr

    # studio edit: mute a track, save, render a loop preview
    arr["tracks"][0]["muted"] = True
    assert c.put(f"/api/projects/{pid}/arrangement", json=arr).json()["tracks"][0]["muted"] is True
    arr_bad = {**arr, "bpm": 999}
    assert c.put(f"/api/projects/{pid}/arrangement", json=arr_bad).status_code == 400
    pv = c.post(f"/api/projects/{pid}/preview", json={"scope": "loop", "loop_start_bar": 0, "loop_bars": 2})
    assert pv.status_code == 200, pv.text
    pv = pv.json()
    y = wav(c, pv["url"])
    assert abs(len(y) / 48000 - 2 * 4 * 60 / arr["bpm"]) < 0.01
    assert c.get(f"/api/projects/{pid}/preview/..%2F..%2Fstore.db").status_code == 404

    # a sample the arrangement uses can't be deleted out from under it
    used = next(e["sample_id"] for p in arr["patterns"].values() for e in p["events"] if e["type"] == "chop")
    assert c.delete(f"/api/samples/{used}").status_code == 409

    # export with stems
    r = c.post(f"/api/projects/{pid}/export", json={"format": "wav24", "stems": True, "filename": "con"}).json()
    done(c, r["job"])
    e = c.get(f"/api/exports/{r['export_id']}").json()
    kinds = {f["kind"] for f in e["files"]}
    assert e["status"] == "done" and {"mix", "stem", "zip", "project"} <= kinds
    assert not next(f for f in e["files"] if f["kind"] == "mix")["check"]["silent"]
    master = next(f["name"] for f in e["files"] if f["kind"] == "mix")
    y = wav(c, f"/api/exports/{r['export_id']}/download?name={master}")
    seek = c.get(f"/api/exports/{r['export_id']}/download?name={master}", headers={"Range": "bytes=0-63"})
    assert seek.status_code == 206 and len(seek.content) == 64
    assert np.abs(y).max() <= 10 ** (-0.99 / 20)
    assert c.get(f"/api/exports/{r['export_id']}/download?name=../../store.db").status_code == 404

    # bad input is rejected cleanly
    up = c.post(f"/api/projects/{pid}/sources", files={"file": ("x.mp3", b"not audio at all", "audio/mpeg")})
    assert up.status_code == 400
    assert c.post(f"/api/projects/{pid}/sources", files={"file": ("x.wav", b"", "audio/wav")}).status_code == 400
    assert c.get("/api/projects/nope").status_code == 404

    jl = c.get(f"/api/projects/{pid}/jobs").json()
    assert jl and all(j["status"] == "completed" for j in jl)
    assert c.get("/api/system/capabilities").status_code == 200

    assert c.delete(f"/api/projects/{pid}").status_code == 204
    assert c.get(f"/api/projects/{pid}").status_code == 404


def test_studio_helpers(c):
    pid = c.post("/api/projects", json={"name": "helpers"}).json()["id"]
    r = c.post(f"/api/projects/{pid}/demo").json()
    done(c, r["job"])
    s = c.get(f"/api/projects/{pid}/samples").json()[0]

    # chop buffer: same timing contract as the renderer (beat b at pickup/speed + b*ratio*60/bpm)
    y = wav(c, f"/api/samples/{s['id']}/buffer?bpm=120&ratio=1&pitch=-2")
    speed = 120 / s["source_bpm"]
    assert abs(len(y) / 48000 - s["duration"] / speed) < 0.05
    assert np.abs(y).max() > 0.01
    assert c.get(f"/api/samples/{s['id']}/buffer?bpm=500").status_code in (400, 422)

    d = c.post("/api/patterns/drums", json={"genre": "trap", "seed": 3, "bars": 2, "variant": "fill"}).json()
    assert d["length_ticks"] == 3840 and {e["inst"] for e in d["events"]} >= {"kick", "snare", "hat"}
    assert d == c.post("/api/patterns/drums", json={"genre": "trap", "seed": 3, "bars": 2, "variant": "fill"}).json()

    b = c.post("/api/patterns/bass", json={"genre": "drill", "roots": [9, 5], "kick_ticks": [0, 720, 1920],
                                           "key": "A minor", "seed": 1}).json()
    assert b["length_ticks"] == 3840 and b["events"]
    assert all(e["midi"] % 12 in {9, 11, 0, 2, 4, 5, 7} for e in b["events"])  # diatonic to A minor
    assert c.delete(f"/api/projects/{pid}").status_code == 204


def test_hpss_separation_is_a_real_split(c):
    import librosa

    pid = c.post("/api/projects", json={"name": "separation"}).json()["id"]
    r = c.post(f"/api/projects/{pid}/demo").json()
    done(c, r["job"])
    sid = r["source"]["id"]
    assert c.post(f"/api/sources/{sid}/separate", json={"model_id": "nope"}).status_code == 400

    res = done(c, c.post(f"/api/sources/{sid}/separate", json={"model_id": "builtin-hpss"}).json())
    assert set(res["result"]["stems"]) == {"harmonic", "percussive"}
    stems = {s["stem_type"]: s for s in c.get(f"/api/sources/{sid}/stems").json()}
    assert set(stems) == {"harmonic", "percussive"} and stems["harmonic"]["model_id"] == "builtin-hpss"

    mix = wav(c, f"/api/sources/{sid}/audio")
    h, p = (wav(c, f"/api/stems/{stems[k]['id']}/audio") for k in ("harmonic", "percussive"))
    assert len(h) == len(p) == len(mix)
    assert np.sqrt((h ** 2).mean()) > 0.01 and np.sqrt((p ** 2).mean()) > 0.001
    # an actual inference check: the percussive stem is noisier/more transient than the harmonic one
    flat = lambda y: float(np.median(librosa.feature.spectral_flatness(y=y.mean(axis=1).astype(np.float32))))
    assert flat(p) > 2 * flat(h)
    assert c.get(f"/api/stems/{sid}.vocals/audio").status_code == 404

    # discovery re-ran with the stems available
    assert any(s.get("stem") not in (None, "mix") for s in c.get(f"/api/sources/{sid}/samples").json())
    assert c.delete(f"/api/projects/{pid}").status_code == 204
