"""FastAPI service (spec 22). Heavy work runs as persistent background jobs; the UI follows them over SSE.
Run: uv run choplab (local by default; protected HTTPS hosting is opt-in)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit

from . import analysis, audio_io, bass, compose, drums, jobs, render, sampling, schemas, separation, store, transform
from .core import DATA_DIR, ENGINE_VERSION, SCHEMA_VERSION, SR, TICKS_PER_BAR, cache_key, data_path, log, parse_key
from .hosting import HOSTING, OwnerAccess

MAX_UPLOAD = int(os.environ.get("CHOPLAB_MAX_UPLOAD_MB", "100" if HOSTING else "1024")) * (1 << 20)
MIN_FREE = int(os.environ.get("CHOPLAB_MIN_FREE_MB", "2048" if HOSTING else "0")) * (1 << 20)
if MAX_UPLOAD <= 0 or MIN_FREE < 0:
    raise ValueError("Upload limit must be positive and free-space reserve nonnegative")
FRONTEND = Path(os.environ.get("CHOPLAB_FRONTEND", Path(__file__).resolve().parents[3] / "frontend" / "dist"))


@asynccontextmanager
async def lifespan(app):
    jobs.recover_interrupted()
    yield


app = FastAPI(title="ChopLab AI", version="0.1.0", lifespan=lifespan,
              description="Single-owner sample-based beat generator.")
app.add_middleware(OwnerAccess)
if HOSTING:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(HOSTING.origin).hostname, "localhost", "127.0.0.1"])


@app.exception_handler(jobs.Busy)
async def _busy(_, e):
    return JSONResponse({"detail": str(e)}, 429, headers={"Retry-After": "5"})


@app.exception_handler(ValueError)  # includes AudioError and pydantic ValidationError
async def _bad(_, e):
    return JSONResponse({"detail": str(e)}, 400)


@app.exception_handler(KeyError)  # store.update on a deleted doc
async def _gone(_, e):
    return JSONResponse({"detail": f"Not found: {e}"}, 404)


@app.exception_handler(render.MissingAsset)
async def _missing(_, e):
    return JSONResponse({"detail": str(e)}, 409)


@app.exception_handler(FileNotFoundError)
async def _nofile(_, e):
    return JSONResponse({"detail": f"A referenced audio file is missing: {e}"}, 409)


def _get(kind: str, id: str) -> dict:
    doc = store.get(kind, id)
    if doc is None:
        raise HTTPException(404, f"{kind} {id} not found")
    return doc


def _dir(*parts) -> Path:
    p = DATA_DIR.joinpath("projects", *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _save_upload(f: UploadFile, dest: Path) -> int:
    """Stream to disk under a generated name (the user's file name is metadata only)."""
    n = 0
    tmp = dest.with_suffix(".part")
    try:
        with open(tmp, "wb") as out:
            while chunk := await f.read(1 << 20):
                n += len(chunk)
                if n > MAX_UPLOAD:
                    raise HTTPException(413, f"File is larger than {MAX_UPLOAD // (1 << 20)} MiB.")
                if shutil.disk_usage(DATA_DIR).free < MIN_FREE + len(chunk):
                    raise HTTPException(507, "Storage reserve reached; delete old projects or clear the cache.")
                out.write(chunk)
        if n == 0:
            raise HTTPException(400, "Uploaded file is empty.")
        tmp.replace(dest)
        return n
    finally:
        tmp.unlink(missing_ok=True)


def _ext(name: str | None) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", name or "")
    return f".{m.group(1).lower()}" if m else ".bin"


# ---------------------------------------------------------------- data helpers

def _sources(project: dict) -> dict:
    return {sid: s for sid in project["source_ids"] if (s := store.get("source", sid))}


def _samples(project: dict) -> list[dict]:
    return [s for sid in project["source_ids"] for s in store.list_("sample", sid)]


def _referenced(project_id: str) -> set[str]:
    """Sample ids used by the project's arrangement or any of its variations (never auto-deleted)."""
    arrs = [v["arrangement"] for v in store.list_("variation", project_id)]
    p = store.get("project", project_id)
    if p and p.get("arrangement"):
        arrs.append(p["arrangement"])
    return {e["sample_id"] for a in arrs for pat in a["patterns"].values() for e in pat["events"] if e["type"] == "chop"}


def _with_slices(s: dict, src: dict | None) -> dict:
    onsets = ((src or {}).get("analysis") or {}).get("onsets", [])
    return {**s, "slices": compose.slices(s, onsets, 2) if s["beats"] >= 1 else [(0.0, s["beats"])]}


def _features_path(src: dict) -> Path:
    return _dir(src["project_id"], "sources", src["id"]) / "features.npz"


# ---------------------------------------------------------------- jobs

def _sub(ctx, lo, hi):
    return lambda stage, f=None: ctx.progress(stage, None if f is None else lo + (hi - lo) * f)


@jobs.handler("import")
def job_import(ctx, source_id: str):
    src = _get("source", source_id)
    try:
        ctx.progress("decoding", 0.02)
        orig = Path(src["original_file_path"])
        meta = audio_io.probe(orig)
        y = audio_io.decode(orig)
        internal = audio_io.write_wav(Path(src["original_file_path"]).with_name("audio.wav"), y)
        src = store.update("source", source_id, internal_audio_path=str(internal), content_hash=audio_io.file_hash(orig),
                           duration_seconds=round(len(y) / SR, 3), sample_rate=meta["sample_rate"],
                           channel_count=meta["channels"], metadata=meta, peaks=audio_io.peaks(y, 2400),
                           analysis_status="analyzing")
        return _analyze(ctx, source_id, y)
    except Exception as e:
        store.update("source", source_id, analysis_status="failed", error=str(e))
        raise


@jobs.handler("analyze")
def job_analyze(ctx, source_id: str, override: dict | None = None):
    store.update("source", source_id, analysis_status="analyzing")
    try:
        return _analyze(ctx, source_id, None, override)
    except Exception as e:
        store.update("source", source_id, analysis_status="failed", error=str(e))
        raise


def _analyze(ctx, source_id, y=None, override=None):
    src = _get("source", source_id)
    y = audio_io.load(Path(src["internal_audio_path"])) if y is None else y
    doc, f = analysis.analyze(y, _sub(ctx, 0.05, 0.7), override)
    np.savez(_features_path(src), **{k: np.asarray(v) for k, v in f.items()})
    store.update("source", source_id, analysis=doc, analysis_status="analyzed", error=None)
    n = _discover(ctx, source_id)
    return {"bpm": doc["bpm"], "key": doc["key"], "samples": n}


def _discover(ctx, source_id) -> int:
    ctx.progress("finding samples", 0.75)
    src = _get("source", source_id)
    f = dict(np.load(_features_path(src)))
    keep = _referenced(src["project_id"])
    for s in store.list_("sample", source_id):
        if s["origin"] == "auto" and s["id"] not in keep and not s.get("favorite"):
            store.delete("sample", s["id"])
    found = sampling.discover(src, src["analysis"], f, count=16, diversity=0.5)
    for s in found:
        store.put("sample", {**s, "project_id": src["project_id"]}, parent=source_id)
    return len(found)


@jobs.handler("separate", pool="separation")
def job_separate(ctx, source_id: str, model_id: str):
    store.update("source", source_id, separation_status="running")
    try:
        stems = separation.separate(_get("source", source_id), model_id, _sub(ctx, 0.0, 0.7))
        store.update("source", source_id, stems=stems, stem_model=model_id, separation_status="done")
    except Exception as e:
        store.update("source", source_id, separation_status="failed", separation_error=str(e))
        raise
    n = _discover(ctx, source_id)
    return {"stems": sorted(stems), "samples": n}


def _store_variation(project_id, v, settings, batch):
    vid = store.new_id("var")
    arr = v["arrangement"]
    path = render.write_preview(_dir(project_id, "previews") / vid, v["render"]["master"])
    return store.put("variation", {
        "id": vid, "project_id": project_id, "batch": batch, "seed": arr["seed"], "genre": arr["genre"],
        "strategy": arr["strategy"], "source_samples": [x for x in (arr["meta"]["main_sample"], arr["meta"]["secondary_sample"]) if x],
        "generation_parameters": settings, "arrangement": arr, "preview_path": str(path), "render_status": "done",
        "metrics": v["render"]["metrics"], "evaluation": v["evaluation"]}, parent=project_id)


def _inputs(project: dict):
    sources = {k: s for k, s in _sources(project).items() if s.get("analysis")}
    if not sources:
        raise ValueError("Import and analyze a song first.")
    samples = [s for s in _samples(project) if s["source_id"] in sources]
    return samples, sources, {k: s["analysis"] for k, s in sources.items()}


@jobs.handler("generate")
def job_generate(ctx, project_id: str, settings: dict):
    samples, sources, analyses = _inputs(_get("project", project_id))
    out = compose.generate(settings, samples, sources, analyses, _sub(ctx, 0.0, 0.95))
    batch = store.now()
    ids = [_store_variation(project_id, v, settings, batch)["id"] for v in out]
    return {"variation_ids": ids}


@jobs.handler("regenerate")
def job_regenerate(ctx, variation_id: str):
    old = _get("variation", variation_id)
    samples, sources, analyses = _inputs(_get("project", old["project_id"]))
    by = {s["id"]: s for s in samples}
    a = old["arrangement"]
    best = None
    for attempt in range(3):
        ctx.progress(f"regenerating (attempt {attempt + 1})", attempt / 3)
        seed = int(np.random.default_rng().integers(1, 10 ** 8))
        arr = compose.variation(old["generation_parameters"], samples, sources, analyses, a["strategy"], seed, a["meta"].get("rank", 0))
        res = render.render(arr, by, sources)
        ev = compose.evaluate(arr, by, sources, res)
        best = {"arrangement": arr, "render": res, "evaluation": ev}
        if ev["passed"]:
            break
    v = _store_variation(old["project_id"], best, old["generation_parameters"], old["batch"])
    return {"variation_ids": [v["id"]]}


@jobs.handler("export")
def job_export(ctx, project_id: str, export_id: str, request: dict):
    p = _get("project", project_id)
    if not p.get("arrangement"):
        raise ValueError("Open a variation in the studio before exporting.")
    arr = schemas.arrangement(p["arrangement"])
    samples = {s["id"]: s for s in _samples(p)}
    sources = _sources(p)
    used = {e["sample_id"] for pat in arr["patterns"].values() for e in pat["events"] if e["type"] == "chop"}
    pj = {"choplab_schema": SCHEMA_VERSION, "engine": ENGINE_VERSION, "project": {k: v for k, v in p.items() if k != "arrangement"},
          "arrangement": arr, "samples": [samples[i] for i in sorted(used) if i in samples]} if request["project_json"] else None
    try:
        out = render.export(arr, samples, sources, _dir(project_id, "exports", export_id), request["filename"], request["format"],
                            request["scope"], request["loop_start_bar"], request["loop_bars"], request["stems"], pj, ctx.progress)
    except Exception as e:
        store.update("export", export_id, status="failed", error=str(e))
        raise
    store.update("export", export_id, status="done", **out)
    return {"export_id": export_id, "files": [f["name"] for f in out["files"]]}


# ---------------------------------------------------------------- projects

@app.post("/api/projects", status_code=201)
def create_project(body: schemas.ProjectCreate):
    return store.put("project", {"id": store.new_id("prj"), "name": body.name, "schema_version": SCHEMA_VERSION,
                                 "source_ids": [], "target_bpm": None, "time_signature": "4/4", "musical_key": None,
                                 "random_seed": None, "arrangement": None,
                                 "generator_settings": schemas.GenerateSettings().model_dump(),
                                 "export_settings": schemas.ExportRequest().model_dump()})


@app.get("/api/projects")
def list_projects():
    return [{k: p.get(k) for k in ("id", "name", "created_at", "updated_at", "source_ids", "target_bpm", "musical_key")}
            | {"has_arrangement": bool(p.get("arrangement"))} for p in store.list_("project")]


@app.get("/api/projects/{id}")
def get_project(id: str):
    p = _get("project", id)
    return {**p, "sources": list(_sources(p).values())}


@app.patch("/api/projects/{id}")
def patch_project(id: str, body: schemas.ProjectPatch):
    _get("project", id)
    return store.update("project", id, **body.model_dump(exclude_none=True))


@app.delete("/api/projects/{id}", status_code=204)
def delete_project(id: str):
    p = _get("project", id)
    running = [j for j in store.list_("job", id) if j["status"] not in jobs.TERMINAL]
    if running:
        raise HTTPException(409, "Cancel or wait for this project's running jobs first.")
    for sid in p["source_ids"]:
        for s in store.list_("sample", sid):
            store.delete("sample", s["id"])
        store.delete("source", sid)
    for kind in ("variation", "export", "job"):
        for d in store.list_(kind, id):
            store.delete(kind, d["id"])
    store.delete("project", id)
    shutil.rmtree(DATA_DIR / "projects" / id, ignore_errors=True)


# ---------------------------------------------------------------- sources

@app.post("/api/projects/{id}/sources", status_code=202)
async def upload_source(id: str, file: UploadFile = File(...)):
    _get("project", id)
    sid = store.new_id("src")
    dest = _dir(id, "sources", sid) / f"original{_ext(file.filename)}"
    await _save_upload(file, dest)
    try:
        meta = audio_io.probe(dest)  # reject non-audio before queueing work
        if meta["duration"] > audio_io.MAX_SECONDS:
            raise audio_io.AudioError(f"Recording is longer than {audio_io.MAX_SECONDS} seconds; trim it first.")
    except audio_io.AudioError:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise
    src = store.put("source", {"id": sid, "project_id": id, "original_filename": (file.filename or "upload")[:200],
                               "original_file_path": str(dest), "internal_audio_path": None, "content_hash": None,
                               "analysis_status": "queued", "stems": {}, "separation_status": None}, parent=id)
    p = _get("project", id)
    store.update("project", id, source_ids=p["source_ids"] + [sid])
    job = jobs.submit("import", id, {"source_id": sid})
    return {"source": src, "job": job}


@app.post("/api/projects/{id}/demo", status_code=202)
def demo_source(id: str):
    """Adds the bundled synthetic demo song (no copyrighted material) - handy for a first run."""
    from . import demo

    _get("project", id)
    sid = store.new_id("src")
    dest = demo.write(_dir(id, "sources", sid) / "original.wav")
    src = store.put("source", {"id": sid, "project_id": id, "original_filename": "ChopLab demo (synthetic soul loop).wav",
                               "original_file_path": str(dest), "internal_audio_path": None, "content_hash": None,
                               "analysis_status": "queued", "stems": {}, "separation_status": None}, parent=id)
    p = _get("project", id)
    store.update("project", id, source_ids=p["source_ids"] + [sid])
    return {"source": src, "job": jobs.submit("import", id, {"source_id": sid})}


@app.get("/api/sources/{id}")
def get_source(id: str):
    return _get("source", id)


@app.get("/api/sources/{id}/audio")
def source_audio(id: str):
    s = _get("source", id)
    if not s.get("internal_audio_path"):
        raise HTTPException(409, "Audio is still being imported.")
    return FileResponse(s["internal_audio_path"], media_type="audio/wav")


@app.get("/api/sources/{id}/waveform")
def source_waveform(id: str):
    s = _get("source", id)
    return {"peaks": s.get("peaks") or [], "duration": s.get("duration_seconds")}


@app.get("/api/sources/{id}/analysis")
def source_analysis(id: str):
    s = _get("source", id)
    if not s.get("analysis"):
        raise HTTPException(409, f"Analysis is {s['analysis_status']}.")
    return s["analysis"]


@app.post("/api/sources/{id}/analyze", status_code=202)
def reanalyze(id: str):
    s = _get("source", id)
    return jobs.submit("analyze", s["project_id"], {"source_id": id, "override": (s.get("analysis") or {}).get("override")})


@app.patch("/api/sources/{id}/analysis", status_code=202)
def override_analysis(id: str, body: schemas.AnalysisOverride):
    """User corrections (tempo, downbeat, key); re-derives the grid and re-discovers samples."""
    s = _get("source", id)
    if not s.get("internal_audio_path"):
        raise HTTPException(409, "Audio is still being imported.")
    return jobs.submit("analyze", s["project_id"], {"source_id": id, "override": body.model_dump(exclude_none=True)})


@app.post("/api/sources/{id}/separate", status_code=202)
def separate(id: str, body: dict):
    s = _get("source", id)
    model = body.get("model_id", "demucs-fast")
    if model not in separation.ADAPTERS:
        raise HTTPException(400, f"Unknown model {model!r}")
    ok, why = separation.ADAPTERS[model].available()
    if not ok:
        raise HTTPException(409, why)
    if not s.get("analysis"):
        raise HTTPException(409, "Wait for analysis to finish first.")
    store.update("source", id, separation_status="queued")
    return jobs.submit("separate", s["project_id"], {"source_id": id, "model_id": model})


@app.get("/api/sources/{id}/stems")
def stems(id: str):
    s = _get("source", id)
    return [{"id": f"{id}.{name}", "source_id": id, "stem_type": name, "model_id": s.get("stem_model"),
             "audio_path": path, "processing_status": "done"} for name, path in (s.get("stems") or {}).items()]


@app.get("/api/stems/{stem_id}/audio")
def stem_audio(stem_id: str):
    sid, _, name = stem_id.rpartition(".")
    path = (_get("source", sid).get("stems") or {}).get(name)
    if not path:
        raise HTTPException(404, "No such stem")
    return FileResponse(path, media_type="audio/wav")


# ---------------------------------------------------------------- samples

@app.get("/api/sources/{id}/samples")
def source_samples(id: str):
    src = _get("source", id)
    return sorted((_with_slices(s, src) for s in store.list_("sample", id)), key=lambda s: -(s.get("score") or 0))


@app.get("/api/projects/{id}/samples")
def project_samples(id: str):
    p = _get("project", id)
    srcs = _sources(p)
    return [_with_slices(s, srcs.get(s["source_id"])) for s in _samples(p)]


@app.post("/api/projects/{id}/samples", status_code=201)
def create_sample(id: str, body: schemas.SampleCreate):
    p = _get("project", id)
    if body.source_id not in p["source_ids"]:
        raise HTTPException(400, "That source is not part of this project.")
    src = _get("source", body.source_id)
    if not src.get("analysis"):
        raise HTTPException(409, "Wait for analysis to finish first.")
    bad = set(body.stems) - {"mix"} - set(src.get("stems") or {})
    if bad:
        raise HTTPException(400, f"Unknown stems {sorted(bad)}")
    s = sampling.manual(src, src["analysis"], body.start, body.end, body.stems, body.name)
    return _with_slices(store.put("sample", {**s, "project_id": id}, parent=src["id"]), src)


@app.patch("/api/samples/{id}")
def patch_sample(id: str, body: schemas.SampleUpdate):
    s = _get("sample", id)
    src = _get("source", s["source_id"])
    ch = body.model_dump(exclude_none=True)
    if "start" in ch or "end" in ch:  # moving a region re-derives its length in beats
        new = sampling.manual(src, src["analysis"], ch.get("start", s["start"]), ch.get("end", s["end"]), s["stems"], s["name"])
        ch.update({k: new[k] for k in ("start", "end", "duration", "beats", "bars", "kind", "chroma", "peaks", "rms_db")}, pickup=0.0)
    if "transform" in ch:
        ch["transform"] = {**(s.get("transform") or {}), **ch["transform"]}
    return _with_slices(store.update("sample", id, **ch), src)


@app.delete("/api/samples/{id}", status_code=204)
def delete_sample(id: str):
    s = _get("sample", id)
    if id in _referenced(s["project_id"]):
        raise HTTPException(409, "This sample is used by the arrangement or a generated variation.")
    store.delete("sample", id)


def _sample_file(s: dict, bpm: float | None) -> Path:
    src = _get("source", s["source_id"])
    paths, hashes = sampling.resolve(src, s["stems"])
    t = {**transform.DEFAULT_TRANSFORM, **(s.get("transform") or {})}
    speed = (bpm / s["source_bpm"] / transform.tape_rate(t)) if bpm else 1.0  # with bpm: locked to that tempo
    with jobs.compute_slot():
        y = transform.cached_transform(paths, hashes, s["start"], s["end"], t, speed)
    out = data_path("cache", "previews", cache_key("smp", hashes, s["start"], s["end"], t, round(speed, 6)) + ".wav")
    return out if out.exists() else audio_io.write_wav(out, np.clip(y, -1, 1), subtype="PCM_16")


@app.get("/api/samples/{id}/audio")
def sample_audio(id: str, bpm: float | None = None):
    if bpm is not None and not 40 <= bpm <= 220:
        raise HTTPException(400, "bpm must be 40-220")
    return FileResponse(_sample_file(_get("sample", id), bpm), media_type="audio/wav")


@app.get("/api/samples/{id}/buffer")
def sample_buffer(id: str, bpm: float, ratio: float = 1.0, pitch: float = 0.0):
    """The exact grid-locked buffer the renderer slices chops from, so live studio playback matches export.
    Beat b of the sample sits at (pickup / speed + b * ratio * 60 / bpm) seconds, speed = bpm / (source_bpm * ratio)."""
    if not 40 <= bpm <= 220 or not 0.1 < ratio <= 8 or abs(pitch) > 24:
        raise HTTPException(400, "bpm must be 40-220, ratio 0.1-8, pitch within ±24")
    s = _get("sample", id)
    src = _get("source", s["source_id"])
    _, hashes = sampling.resolve(src, s["stems"])
    t = {**transform.DEFAULT_TRANSFORM, **(s.get("transform") or {})}
    out = data_path("cache", "previews", cache_key("buf", hashes, s["start"], s["end"], t, round(bpm, 4), round(ratio, 6), round(pitch, 4)) + ".wav")
    if not out.exists():
        with jobs.compute_slot():
            y, _ = render.sample_buffer(s, src, bpm, ratio, pitch)
            audio_io.write_wav(out, np.clip(y, -1, 1), subtype="PCM_16")
    return FileResponse(out, media_type="audio/wav")


@app.post("/api/samples/{id}/preview")
def sample_preview(id: str, body: dict | None = None):
    bpm = (body or {}).get("bpm")
    if bpm is not None and not 40 <= float(bpm) <= 220:
        raise HTTPException(400, "bpm must be 40-220")
    f = _sample_file(_get("sample", id), bpm)
    return {"url": f"/api/samples/{id}/audio" + (f"?bpm={bpm}" if bpm else ""), "file": f.name}


@app.post("/api/samples/{id}/transform")
def sample_transform(id: str, body: dict):
    upd = patch_sample(id, schemas.SampleUpdate(transform=body))
    return {**upd, "preview": sample_preview(id)}


# ---------------------------------------------------------------- generation

@app.post("/api/projects/{id}/generate", status_code=202)
def generate(id: str, body: schemas.GenerateSettings):
    p = _get("project", id)
    _inputs(p)  # fail fast with a clear message
    settings = body.model_dump()
    store.update("project", id, generator_settings=settings)
    return jobs.submit("generate", id, {"project_id": id, "settings": settings})


@app.get("/api/projects/{id}/variations")
def variations(id: str):
    _get("project", id)
    vs = sorted(store.list_("variation", id), key=lambda v: (-v["batch"], v["created_at"]))
    return [{k: v for k, v in x.items() if k != "arrangement"} | {"meta": x["arrangement"]["meta"],
            "bpm": x["arrangement"]["bpm"], "key": x["arrangement"]["key"], "bars": x["arrangement"]["bars"],
            "sections": x["arrangement"]["sections"]} for x in vs]


@app.get("/api/variations/{id}")
def get_variation(id: str):
    return _get("variation", id)


@app.get("/api/variations/{id}/audio")
def variation_audio(id: str):
    return FileResponse(_get("variation", id)["preview_path"], media_type="audio/wav")


@app.post("/api/variations/{id}/regenerate", status_code=202)
def regenerate(id: str):
    v = _get("variation", id)
    return jobs.submit("regenerate", v["project_id"], {"variation_id": id})


@app.delete("/api/variations/{id}", status_code=204)
def delete_variation(id: str):
    v = _get("variation", id)
    store.delete("variation", id)
    Path(v["preview_path"]).unlink(missing_ok=True)


@app.post("/api/variations/{id}/open-in-studio")
def open_in_studio(id: str):
    v = _get("variation", id)
    arr = schemas.arrangement(v["arrangement"])
    return store.update("project", v["project_id"], arrangement=arr, target_bpm=arr["bpm"], musical_key=arr["key"],
                        random_seed=arr["seed"], variation_id=id)


# ---------------------------------------------------------------- arrangement

@app.get("/api/projects/{id}/arrangement")
def get_arrangement(id: str):
    a = _get("project", id).get("arrangement")
    if not a:
        raise HTTPException(404, "No arrangement yet: open a variation in the studio.")
    return a


@app.put("/api/projects/{id}/arrangement")
def put_arrangement(id: str, body: dict):
    p = _get("project", id)
    arr = schemas.arrangement(body)
    samples = {s["id"] for s in _samples(p)}
    missing = {e["sample_id"] for pat in arr["patterns"].values() for e in pat["events"] if e["type"] == "chop"} - samples
    if missing:
        raise HTTPException(400, f"Arrangement references unknown samples: {sorted(missing)}")
    store.update("project", id, arrangement=arr, target_bpm=arr["bpm"], musical_key=arr.get("key"))
    return arr


@app.post("/api/projects/{id}/preview")
def preview(id: str, body: schemas.PreviewRequest):
    """Renders through the same engine as export, so what you hear is what you export."""
    p = _get("project", id)
    arr = schemas.arrangement(body.arrangement or p.get("arrangement") or {})
    with jobs.compute_slot():
        res = render.render(arr, {s["id"]: s for s in _samples(p)}, _sources(p), body.scope, body.loop_start_bar, body.loop_bars)
    d = _dir(id, "previews")
    for old in d.glob("studio_*.wav"):  # keep one studio preview on disk
        old.unlink(missing_ok=True)
    name = f"studio_{store.new_id('r')}"
    render.write_preview(d / name, res["master"])
    return {"url": f"/api/projects/{id}/preview/{name}.wav", "metrics": res["metrics"],
            "start_bar": body.loop_start_bar if body.scope == "loop" else 0}


@app.get("/api/projects/{id}/preview/{name}")
def preview_file(id: str, name: str):
    if not re.fullmatch(r"studio_r_[0-9a-f]{12}\.wav", name):
        raise HTTPException(404)
    f = DATA_DIR / "projects" / id / "previews" / name
    if not f.exists():
        raise HTTPException(404, "Preview expired; render again.")
    return FileResponse(f, media_type="audio/wav")


@app.post("/api/projects/{id}/bass-sample", status_code=201)
async def bass_sample(id: str, file: UploadFile = File(...)):
    """A custom 808 one-shot (tuned from its root note at render time)."""
    _get("project", id)
    tmp = _dir(id, "sounds") / f"upload{_ext(file.filename)}"
    await _save_upload(file, tmp)
    try:
        with jobs.compute_slot():
            y = await asyncio.to_thread(audio_io.decode, tmp)
            y = y[: SR * 4]
    finally:
        tmp.unlink(missing_ok=True)
    out = audio_io.write_wav(_dir(id, "sounds") / f"808_{store.new_id('s')}.wav", y)
    return {"sample_path": str(out)}


# ---------------------------------------------------------------- export

@app.post("/api/projects/{id}/export", status_code=202)
def export(id: str, body: schemas.ExportRequest):
    p = _get("project", id)
    if not p.get("arrangement"):
        raise HTTPException(409, "Open a variation in the studio before exporting.")
    eid = store.new_id("exp")
    req = body.model_dump()
    store.put("export", {"id": eid, "project_id": id, "request": req, "status": "queued"}, parent=id)
    store.update("project", id, export_settings=req)
    return {"export_id": eid, "job": jobs.submit("export", id, {"project_id": id, "export_id": eid, "request": req})}


@app.get("/api/projects/{id}/exports")
def list_exports(id: str):
    return store.list_("export", id)


@app.get("/api/exports/{id}")
def get_export(id: str):
    return _get("export", id)


@app.get("/api/exports/{id}/download")
def download(id: str, name: str | None = None):
    e = _get("export", id)
    files = {f["name"]: f["path"] for f in e.get("files", [])}
    if not files:
        raise HTTPException(409, "Export is not finished.")
    name = name or next(iter(files))
    if name not in files:  # only files this export produced: no path traversal
        raise HTTPException(404, "No such file in this export")
    return FileResponse(files[name], filename=name)


@app.post("/api/exports/{id}/reveal")
def reveal(id: str):
    if HOSTING:
        raise HTTPException(409, "Hosted exports are on the server; use Download to save them to your device.")
    folder = _get("export", id).get("folder")
    if not folder or not Path(folder).is_dir():
        raise HTTPException(409, "Export folder not available.")
    if sys.platform == "win32":
        os.startfile(folder)  # noqa: S606 - server-generated path, local app
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
    return {"folder": folder}


# ---------------------------------------------------------------- pattern generators (studio "randomize")

@app.post("/api/patterns/drums")
def gen_drums(body: schemas.DrumPatternRequest):
    """A fresh genre-aware groove; the studio uses it to re-roll individual lanes."""
    pats = drums.generate(body.genre, body.complexity, body.seed, body.bars)
    return {"length_ticks": body.bars * TICKS_PER_BAR, "events": pats[body.variant]}


@app.post("/api/patterns/bass")
def gen_bass(body: schemas.BassPatternRequest):
    """A key-aware 808 line over the given per-bar roots, locked to the given kick ticks."""
    kicks = [{"tick": t, "inst": "kick"} for t in body.kick_ticks]
    notes = bass.generate(body.roots, kicks, body.genre, body.seed, parse_key(body.key), body.complexity)
    return {"length_ticks": len(body.roots) * TICKS_PER_BAR, "events": notes}


# ---------------------------------------------------------------- kits

@app.get("/api/kits")
def kits():
    return {k: {i: drums.kit_path(k, i).parent.name == "custom" for i in drums.INSTRUMENTS} for k in drums.KITS}


def _kit_inst(kit, inst):
    if kit not in drums.KITS or inst not in drums.INSTRUMENTS:
        raise HTTPException(404, "Unknown kit or instrument")


@app.get("/api/kits/{kit}/{inst}/audio")
def kit_audio(kit: str, inst: str):
    _kit_inst(kit, inst)
    drums.sound(kit, inst)  # materialize the synthesized default
    return FileResponse(drums.kit_path(kit, inst), media_type="audio/wav")


@app.post("/api/kits/{kit}/{inst}", status_code=201)
async def kit_upload(kit: str, inst: str, file: UploadFile = File(...)):
    _kit_inst(kit, inst)
    tmp = data_path("kits", "_upload" + _ext(file.filename))
    await _save_upload(file, tmp)
    try:
        with jobs.compute_slot():
            await asyncio.to_thread(drums.import_sound, kit, inst, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return {"kit": kit, "inst": inst, "custom": True}


@app.delete("/api/kits/{kit}/{inst}", status_code=204)
def kit_reset(kit: str, inst: str):
    _kit_inst(kit, inst)
    drums.reset_sound(kit, inst)


# ---------------------------------------------------------------- jobs

@app.get("/api/jobs/{id}")
def get_job(id: str):
    return _get("job", id)


@app.get("/api/projects/{id}/jobs")
def project_jobs(id: str):
    return sorted(store.list_("job", id), key=lambda j: -j["created_at"])[:50]


@app.get("/api/jobs/{id}/events")
async def job_events(id: str, request: Request):
    _get("job", id)

    async def stream():
        last = None
        while not await request.is_disconnected():
            job = store.get("job", id)
            s = json.dumps(job)
            if s != last:
                yield f"data: {s}\n\n"
                last = s
            if job["status"] in jobs.TERMINAL:
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/jobs/{id}/cancel")
def cancel_job(id: str):
    _get("job", id)
    return jobs.cancel(id)


@app.post("/api/jobs/{id}/retry", status_code=202)
def retry_job(id: str):
    j = _get("job", id)
    if j["status"] not in ("failed", "canceled", "interrupted"):
        raise HTTPException(409, "Only failed, canceled or interrupted jobs can be retried.")
    return jobs.retry(id)


# ---------------------------------------------------------------- system

@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version, "engine": ENGINE_VERSION}


@app.get("/api/system/capabilities")
def capabilities():
    """Also serves as UI configuration: genres, strategies, kits and default settings come from here."""
    free = shutil.disk_usage(DATA_DIR).free
    return {"ffmpeg": audio_io.ffmpeg_exe() is not None, "sample_rate": SR, "data_dir": str(DATA_DIR),
            "hosted": HOSTING is not None, "max_upload_mb": MAX_UPLOAD // (1 << 20),
            "max_audio_seconds": audio_io.MAX_SECONDS,
            "disk_free_gb": round(free / 1e9, 1), "schema_version": SCHEMA_VERSION,
            "genres": {k: {**v, "bpm": list(v["bpm"]), "swing": list(v["swing"])} for k, v in drums.GENRES.items()},
            "strategies": compose.STRATEGIES, "kits": list(drums.KITS), "instruments": drums.INSTRUMENTS,
            "track_of": drums.TRACK_OF, "fx_defaults": transform.DEFAULT_FX, "transform_defaults": transform.DEFAULT_TRANSFORM,
            "bass_defaults": bass.DEFAULT_808,
            "generate_defaults": schemas.GenerateSettings().model_dump(), "formats": list(render.FORMATS)}


@app.get("/api/system/models")
def models():
    return [a.info() for a in separation.ADAPTERS.values()]


@app.get("/api/system/audio-devices")
def audio_devices():
    return {"output": "browser", "note": "Playback uses the browser's audio output; pick the device in your OS or browser."}


@app.delete("/api/system/cache")
def clear_cache():
    """Drops re-creatable transform/preview caches. Separated stems are kept (slow to recreate)."""
    freed = 0
    for sub in ("transforms", "previews"):
        d = DATA_DIR / "cache" / sub
        if d.exists():
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
    transform._mem.clear()
    return {"freed_mb": round(freed / 1e6, 1)}


# ---------------------------------------------------------------- frontend (built SPA)

if FRONTEND.is_dir():
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (FRONTEND / path).resolve()
        if path and f.is_file() and FRONTEND.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(FRONTEND / "index.html")


def main():
    import uvicorn

    logging_level = os.environ.get("CHOPLAB_LOG", "info")
    port = int(os.environ.get("CHOPLAB_PORT", "8765"))
    log.info("ChopLab data dir: %s", DATA_DIR)
    host = os.environ.get("CHOPLAB_HOST", "0.0.0.0" if HOSTING else "127.0.0.1")
    if not HOSTING and host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Non-loopback binding requires protected CHOPLAB_MODE=hosted")
    uvicorn.run("choplab.api:app", host=host, port=port, log_level=logging_level,
                workers=1, proxy_headers=bool(HOSTING),
                forwarded_allow_ips=os.environ.get("CHOPLAB_PROXY_IPS", "127.0.0.1"),
                limit_concurrency=32 if HOSTING else None)
