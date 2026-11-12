"""HTTP API. Heavy work runs as persistent background jobs that the UI follows over SSE.

Every project belongs to a user (a guest session until they register); every resource endpoint checks
ownership, and plan limits are enforced here rather than in the UI.
Run: uv run choplab   (CHOPLAB_SELF_HOSTED=1 unlocks everything for a single local user)."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import numpy as np
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from . import (accounts, analysis, audio_io, bass, billing, compose, drums, jobs, render, sampling, schemas, separation,
               store, transform)
from .core import DATA_DIR, ENGINE_VERSION, SCHEMA_VERSION, SR, cache_key, data_path, log, resolve_data

FRONTEND = Path(os.environ.get("CHOPLAB_FRONTEND", Path(__file__).resolve().parents[3] / "frontend" / "dist"))
GENERATION_JOBS = {"beat", "generate", "regenerate"}
CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@asynccontextmanager
async def lifespan(app):
    jobs.recover_interrupted()
    yield


_docs = os.environ.get("CHOPLAB_DOCS") == "1"
app = FastAPI(title="ChopLab", version="1.0.0", lifespan=lifespan, docs_url="/api/docs" if _docs else None,
              redoc_url=None, openapi_url="/api/openapi.json" if _docs else None,
              description="Turn any sample into a full instrumental beat.")


@app.middleware("http")
async def _guard(request: Request, call_next):
    """Cross-site request protection (on top of SameSite cookies) and security headers."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path != "/api/billing/webhook":
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host") and origin != accounts.base_url():
            return JSONResponse({"detail": "Cross-site requests are not allowed."}, 403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if not _docs:
        resp.headers.setdefault("Content-Security-Policy", CSP)
    if accounts.cookie_secure():
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


@app.exception_handler(ValueError)  # includes AudioError, AuthError and pydantic ValidationError
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
    log.error("missing audio file: %s", e)
    return JSONResponse({"detail": "A referenced audio file is missing."}, 409)


@app.exception_handler(accounts.Paywall)
async def _paywall(_, e):
    return JSONResponse({"detail": str(e), "code": "upgrade_required", "feature": e.feature, "plan_needed": e.plan_needed}, 402)


@app.exception_handler(accounts.SignupRequired)
async def _signup(_, e):
    return JSONResponse({"detail": str(e), "code": "signup_required"}, 401)


@app.exception_handler(billing.BillingError)
async def _billing(_, e):
    return JSONResponse({"detail": str(e), "code": "billing_unavailable"}, 503)


# ---------------------------------------------------------------- who is calling

def _ip(request: Request) -> str:
    if os.environ.get("CHOPLAB_TRUST_PROXY") == "1":  # only behind a proxy that sets the header itself
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.client.host if request.client else "unknown"


def _limit(request: Request, what: str, limit: int, window_s: float) -> None:
    if not accounts.rate_limit(f"{what}:{_ip(request)}", limit, window_s):
        raise HTTPException(429, "Too many attempts. Wait a minute and try again.")


def _set_session(response: Response, user_id: str) -> None:
    response.set_cookie(accounts.SESSION_COOKIE, accounts.start_session(user_id), max_age=accounts.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=accounts.cookie_secure(), path="/")


def viewer(request: Request) -> dict | None:
    return accounts.session_user(request.cookies.get(accounts.SESSION_COOKIE))


def known_user(request: Request) -> dict:
    user = viewer(request)
    if user is None:
        raise HTTPException(401, "Your session has expired. Reload the page.")
    return user


def any_user(request: Request, response: Response) -> dict:
    """The signed-in user, or a fresh guest: visitors can make a beat before creating an account."""
    user = viewer(request)
    if user is None:
        _limit(request, "guest", 30, 3600)
        user = accounts.create_guest()
        _set_session(response, user["id"])
    return user


Viewer = Annotated[dict | None, Depends(viewer)]
User = Annotated[dict, Depends(known_user)]
AnyUser = Annotated[dict, Depends(any_user)]


def _registered(user: dict, why: str) -> None:
    if user.get("guest") and not accounts.self_hosted():
        raise accounts.SignupRequired(why)


def _get(kind: str, id: str) -> dict:
    doc = store.get(kind, id)
    if doc is None:
        raise HTTPException(404, f"{kind} {id} not found")
    return doc


def _own(kind: str, id: str, user: dict) -> dict:
    """The document, if it belongs to one of this user's projects. Foreign ids look exactly like missing ones."""
    doc = store.get(kind, id)
    proj = doc if kind == "project" else (store.get("project", doc.get("project_id") or "") if doc else None)
    if doc is None or proj is None or proj.get("user_id") != user["id"]:
        raise HTTPException(404, f"{kind} {id} not found")
    return doc


def _dir(*parts) -> Path:
    p = DATA_DIR.joinpath("projects", *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _save_upload(f: UploadFile, dest: Path, max_mb: int) -> int:
    """Stream to disk under a generated name (the user's file name is metadata only)."""
    n = 0
    tmp = dest.with_suffix(".part")
    try:
        with open(tmp, "wb") as out:
            while chunk := await f.read(1 << 20):
                n += len(chunk)
                if n > max_mb << 20:
                    raise accounts.Paywall(f"This file is larger than the {max_mb} MB your plan allows.", "max_upload_mb")
                out.write(chunk)
        if n == 0:
            raise HTTPException(400, "Uploaded file is empty.")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return n


def _ext(name: str | None) -> str:
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", name or "")
    return f".{m.group(1).lower()}" if m else ".bin"


# ---------------------------------------------------------------- what the client may see (no server paths)

def _pub_source(s: dict) -> dict:
    hide = {"original_file_path", "internal_audio_path", "_parent"}
    return {**{k: v for k, v in s.items() if k not in hide}, "stems": sorted(s.get("stems") or {}),
            "has_audio": bool(s.get("internal_audio_path"))}


def _pub_variation(v: dict, full: bool = True) -> dict:
    out = {k: x for k, x in v.items() if k not in ("preview_path", "_parent") and (full or k != "arrangement")}
    a = v["arrangement"]
    return out | {"meta": a["meta"], "bpm": a["bpm"], "key": a.get("key"), "bars": a["bars"], "sections": a["sections"]}


def _pub_export(e: dict) -> dict:
    return {**{k: v for k, v in e.items() if k not in ("folder", "_parent")},
            "files": [{k: v for k, v in f.items() if k != "path"} for f in e.get("files", [])]}


def _pub_project(p: dict) -> dict:
    return {k: v for k, v in p.items() if k != "_parent"}


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


class _Span:
    """A job context whose 0..1 progress maps onto a slice of the parent job (for multi-step pipelines)."""

    def __init__(self, ctx, lo: float, hi: float):
        self.id, self.progress = ctx.id, _sub(ctx, lo, hi)


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


def _open(v: dict) -> dict:
    """Makes a variation the project's working arrangement (what the studio edits and export renders)."""
    arr = schemas.arrangement(v["arrangement"])
    return store.update("project", v["project_id"], arrangement=arr, target_bpm=arr["bpm"], musical_key=arr["key"],
                        random_seed=arr["seed"], variation_id=v["id"], genre=arr["genre"])


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
def job_generate(ctx, project_id: str, settings: dict, user_id: str | None = None, open_best: bool = False):
    samples, sources, analyses = _inputs(_get("project", project_id))
    out = compose.generate(settings, samples, sources, analyses, _sub(ctx, 0.0, 0.95))
    batch = store.now()
    stored = [_store_variation(project_id, v, settings, batch) for v in out]
    opened = None
    if open_best and stored:  # one-click flow: the first variation that passed every check is ready to export
        best = next((v for v in stored if v["evaluation"]["passed"]), stored[0])
        opened = _open(best)["variation_id"]
    if user_id:
        accounts.count_generation(user_id)  # only finished work counts against the plan
    return {"variation_ids": [v["id"] for v in stored], "opened": opened}


@jobs.handler("beat")
def job_beat(ctx, project_id: str, source_id: str, settings: dict, user_id: str | None = None):
    """Sample in, beat out: import -> analyze -> find samples -> compose and render variations."""
    info = job_import(_Span(ctx, 0.0, 0.4), source_id)
    out = job_generate(_Span(ctx, 0.4, 1.0), project_id, settings, user_id, open_best=True)
    return {**info, **out}


@jobs.handler("regenerate")
def job_regenerate(ctx, variation_id: str, user_id: str | None = None):
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
    if user_id:
        accounts.count_generation(user_id)
    return {"variation_ids": [v["id"]]}


@jobs.handler("export")
def job_export(ctx, project_id: str, export_id: str, request: dict, license_text: str | None = None):
    p = _get("project", project_id)
    if not p.get("arrangement"):
        raise ValueError("Open a variation in the studio before exporting.")
    arr = schemas.arrangement(p["arrangement"])
    samples = {s["id"]: s for s in _samples(p)}
    sources = _sources(p)
    used = {e["sample_id"] for pat in arr["patterns"].values() for e in pat["events"] if e["type"] == "chop"}
    pj = {"choplab_schema": SCHEMA_VERSION, "engine": ENGINE_VERSION,
          "project": {k: v for k, v in p.items() if k not in ("arrangement", "user_id", "_parent")},
          "arrangement": arr, "samples": [samples[i] for i in sorted(used) if i in samples]} if request.get("project_json") else None
    try:
        out = render.export(arr, samples, sources, _dir(project_id, "exports", export_id), request["filename"], request["format"],
                            request["scope"], request["loop_start_bar"], request["loop_bars"], request["stems"], pj, ctx.progress,
                            midi_file=bool(request.get("midi")), license_text=license_text)
    except Exception as e:
        store.update("export", export_id, status="failed", error=str(e))
        raise
    store.update("export", export_id, status="done", **out)
    return {"export_id": export_id, "files": [f["name"] for f in out["files"]]}


# ---------------------------------------------------------------- accounts

def _me(user: dict | None) -> dict:
    return {"user": accounts.public(user) if user else None,
            "plan": accounts.plan(user) if user else accounts.PLANS[accounts.default_plan()],
            "usage": accounts.usage(user) if user else None,
            "self_hosted": accounts.self_hosted(), "billing": billing.mode()}


@app.get("/api/me")
def me(user: Viewer):
    return _me(user)


@app.get("/api/plans")
def plans():
    return {"plans": accounts.PLANS, "order": accounts.PLAN_ORDER, "guest_generations": accounts.GUEST_GENERATIONS,
            "billing": billing.mode()}


def _adopt(guest: dict | None, user: dict) -> None:
    """Logging in from a guest session keeps whatever the guest just made."""
    if not guest or not guest.get("guest") or guest["id"] == user["id"]:
        return
    for p in store.list_("project", guest["id"]):
        store.put("project", {**p, "user_id": user["id"]}, parent=user["id"])
    accounts.end_all_sessions(guest["id"])
    store.delete("user", guest["id"])


@app.post("/api/auth/register", status_code=201)
def register(body: schemas.Credentials, request: Request, response: Response, current: Viewer):
    _limit(request, "register", 10, 60)
    user = accounts.register(current, body.email, body.password)
    if current is None or current["id"] != user["id"]:
        _set_session(response, user["id"])
    return _me(user)


@app.post("/api/auth/login")
def login(body: schemas.Credentials, request: Request, response: Response, current: Viewer):
    _limit(request, "login", 10, 60)
    user = accounts.authenticate(body.email, body.password)
    _adopt(current, user)
    accounts.end_session(request.cookies.get(accounts.SESSION_COOKIE))
    _set_session(response, user["id"])
    return _me(user)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    accounts.end_session(request.cookies.get(accounts.SESSION_COOKIE))
    response.delete_cookie(accounts.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.post("/api/auth/forgot")
def forgot(body: schemas.EmailOnly, request: Request):
    _limit(request, "forgot", 10, 60)
    accounts.request_reset(body.email)
    return {"ok": True}  # same answer whether or not the address has an account


@app.post("/api/auth/reset")
def reset(body: schemas.ResetPassword, request: Request, response: Response):
    _limit(request, "reset", 10, 60)
    user = accounts.reset_password(body.token, body.password)
    _set_session(response, user["id"])
    return _me(user)


@app.delete("/api/account", status_code=204)
def delete_account(user: User, response: Response):
    """Deletes the account and everything it owns (uploads, beats, exports), and stops billing."""
    billing.cancel_subscription(user)
    for p in store.list_("project", user["id"]):
        _delete_project(p, force=True)
    accounts.end_all_sessions(user["id"])
    if user.get("email"):
        store.delete("email", accounts._email_key(user["email"]))
    store.delete("user", user["id"])
    response.delete_cookie(accounts.SESSION_COOKIE, path="/")


# ---------------------------------------------------------------- billing

@app.post("/api/billing/checkout")
def checkout(body: schemas.Checkout, user: User):
    _registered(user, "Create an account before subscribing.")
    return {"url": billing.checkout_url(user, body.plan, body.interval)}


@app.post("/api/billing/portal")
def portal(user: User):
    return {"url": billing.portal_url(user)}


@app.post("/api/billing/webhook")
async def webhook(request: Request):
    payload = await request.body()
    try:
        event = billing.verify(payload, request.headers.get("stripe-signature"))
    except ValueError as e:
        raise HTTPException(400, f"Invalid webhook: {e}")
    return {"received": True, "result": billing.handle(event)}


@app.get("/api/billing/mock-complete", include_in_schema=False)
def mock_complete(plan: str, user: User):
    """Development stand-in for Stripe Checkout / the portal. Disabled unless CHOPLAB_BILLING=mock."""
    if billing.mode() != "mock" or plan not in accounts.PLANS:
        raise HTTPException(404)
    billing.set_plan(user["id"], plan, subscription_status="active" if plan != "free" else "canceled")
    return RedirectResponse("/app/account?upgraded=1" if plan != "free" else "/app/account", 303)


# ---------------------------------------------------------------- projects

def _new_project(user: dict, name: str) -> dict:
    limit = accounts.plan(user)["projects"]
    if len(store.list_("project", user["id"])) >= limit:
        raise accounts.Paywall(f"Your plan keeps up to {limit} projects. Delete one or upgrade for more room.", "projects")
    return store.put("project", {"id": store.new_id("prj"), "user_id": user["id"], "name": name[:80] or "Untitled beat",
                                 "schema_version": SCHEMA_VERSION, "source_ids": [], "target_bpm": None,
                                 "time_signature": "4/4", "musical_key": None, "random_seed": None, "arrangement": None,
                                 "generator_settings": schemas.GenerateSettings().model_dump(),
                                 "export_settings": schemas.ExportRequest().model_dump()}, parent=user["id"])


@app.post("/api/projects", status_code=201)
def create_project(body: schemas.ProjectCreate, user: AnyUser):
    return _pub_project(_new_project(user, body.name))


@app.get("/api/projects")
def list_projects(user: Viewer):
    if user is None:
        return []
    ps = sorted(store.list_("project", user["id"]), key=lambda p: -p["updated_at"])
    return [{k: p.get(k) for k in ("id", "name", "created_at", "updated_at", "source_ids", "target_bpm", "musical_key",
                                   "variation_id", "genre")} | {"has_arrangement": bool(p.get("arrangement"))} for p in ps]


@app.get("/api/projects/{id}")
def get_project(id: str, user: User):
    p = _own("project", id, user)
    return {**_pub_project(p), "sources": [_pub_source(s) for s in _sources(p).values()]}


@app.patch("/api/projects/{id}")
def patch_project(id: str, body: schemas.ProjectPatch, user: User):
    _own("project", id, user)
    return _pub_project(store.update("project", id, **body.model_dump(exclude_none=True)))


def _delete_project(p: dict, force: bool = False) -> None:
    running = [j for j in store.list_("job", p["id"]) if j["status"] not in jobs.TERMINAL]
    if running and not force:
        raise HTTPException(409, "Cancel or wait for this project's running jobs first.")
    for j in running:
        jobs.cancel(j["id"])
    for sid in p["source_ids"]:
        for s in store.list_("sample", sid):
            store.delete("sample", s["id"])
        store.delete("source", sid)
    for kind in ("variation", "export", "job"):
        for d in store.list_(kind, p["id"]):
            store.delete(kind, d["id"])
    store.delete("project", p["id"])
    shutil.rmtree(DATA_DIR / "projects" / p["id"], ignore_errors=True)


@app.delete("/api/projects/{id}", status_code=204)
def delete_project(id: str, user: User):
    _delete_project(_own("project", id, user))


# ---------------------------------------------------------------- sources

async def _add_source(project: dict, user: dict, file: UploadFile | None) -> dict:
    """Stores an upload (or the built-in demo when file is None) as a new source of the project."""
    pid, sid = project["id"], store.new_id("src")
    plan = accounts.plan(user)
    if file is None:
        from . import demo

        dest = demo.write(_dir(pid, "sources", sid) / "original.wav")
        filename = "ChopLab demo (synthetic soul loop).wav"
    else:
        dest = _dir(pid, "sources", sid) / f"original{_ext(file.filename)}"
        try:
            await _save_upload(file, dest, plan["max_upload_mb"])
            meta = audio_io.probe(dest)  # reject non-audio before queueing work
            if meta["duration"] > plan["max_source_minutes"] * 60:
                raise accounts.Paywall(f"Your plan handles audio up to {plan['max_source_minutes']} minutes long. "
                                       "Trim the file or upgrade.", "max_source_minutes")
        except Exception:
            shutil.rmtree(dest.parent, ignore_errors=True)
            raise
        filename = (file.filename or "upload")[:200]
    src = store.put("source", {"id": sid, "project_id": pid, "original_filename": filename,
                               "original_file_path": str(dest), "internal_audio_path": None, "content_hash": None,
                               "analysis_status": "queued", "stems": {}, "separation_status": None}, parent=pid)
    store.update("project", pid, source_ids=_get("project", pid)["source_ids"] + [sid])
    return src


@app.post("/api/projects/{id}/sources", status_code=202)
async def upload_source(id: str, user: User, file: UploadFile = File(...)):
    src = await _add_source(_own("project", id, user), user, file)
    return {"source": _pub_source(src), "job": jobs.submit("import", id, {"source_id": src["id"]})}


@app.post("/api/projects/{id}/demo", status_code=202)
async def demo_source(id: str, user: User):
    """Adds the bundled synthetic demo song (no copyrighted material) - handy for a first run."""
    src = await _add_source(_own("project", id, user), user, None)
    return {"source": _pub_source(src), "job": jobs.submit("import", id, {"source_id": src["id"]})}


@app.get("/api/sources/{id}")
def get_source(id: str, user: User):
    return _pub_source(_own("source", id, user))


@app.get("/api/sources/{id}/audio")
def source_audio(id: str, user: User):
    s = _own("source", id, user)
    if not s.get("internal_audio_path"):
        raise HTTPException(409, "Audio is still being imported.")
    return FileResponse(s["internal_audio_path"], media_type="audio/wav")


@app.get("/api/sources/{id}/waveform")
def source_waveform(id: str, user: User):
    s = _own("source", id, user)
    return {"peaks": s.get("peaks") or [], "duration": s.get("duration_seconds")}


@app.get("/api/sources/{id}/analysis")
def source_analysis(id: str, user: User):
    s = _own("source", id, user)
    if not s.get("analysis"):
        raise HTTPException(409, f"Analysis is {s['analysis_status']}.")
    return s["analysis"]


@app.post("/api/sources/{id}/analyze", status_code=202)
def reanalyze(id: str, user: User):
    s = _own("source", id, user)
    return jobs.submit("analyze", s["project_id"], {"source_id": id, "override": (s.get("analysis") or {}).get("override")})


@app.patch("/api/sources/{id}/analysis", status_code=202)
def override_analysis(id: str, body: schemas.AnalysisOverride, user: User):
    """User corrections (tempo, downbeat, key); re-derives the grid and re-discovers samples."""
    s = _own("source", id, user)
    if not s.get("internal_audio_path"):
        raise HTTPException(409, "Audio is still being imported.")
    return jobs.submit("analyze", s["project_id"], {"source_id": id, "override": body.model_dump(exclude_none=True)})


@app.post("/api/sources/{id}/separate", status_code=202)
def separate(id: str, body: schemas.SeparateRequest, user: User):
    s = _own("source", id, user)
    accounts.require(user, "separation")
    if body.model_id not in separation.ADAPTERS:
        raise HTTPException(400, f"Unknown model {body.model_id!r}")
    ok, why = separation.ADAPTERS[body.model_id].available()
    if not ok:
        raise HTTPException(409, why)
    if not s.get("analysis"):
        raise HTTPException(409, "Wait for analysis to finish first.")
    store.update("source", id, separation_status="queued")
    return jobs.submit("separate", s["project_id"], {"source_id": id, "model_id": body.model_id})


@app.get("/api/sources/{id}/stems")
def stems(id: str, user: User):
    s = _own("source", id, user)
    return [{"id": f"{id}.{name}", "source_id": id, "stem_type": name, "model_id": s.get("stem_model"),
             "processing_status": "done"} for name in (s.get("stems") or {})]


@app.get("/api/stems/{stem_id}/audio")
def stem_audio(stem_id: str, user: User):
    sid, _, name = stem_id.rpartition(".")
    path = (_own("source", sid, user).get("stems") or {}).get(name)
    if not path:
        raise HTTPException(404, "No such stem")
    return FileResponse(path, media_type="audio/wav")


# ---------------------------------------------------------------- samples

@app.get("/api/sources/{id}/samples")
def source_samples(id: str, user: User):
    src = _own("source", id, user)
    return sorted((_with_slices(s, src) for s in store.list_("sample", id)), key=lambda s: -(s.get("score") or 0))


@app.get("/api/projects/{id}/samples")
def project_samples(id: str, user: User):
    p = _own("project", id, user)
    srcs = _sources(p)
    return sorted((_with_slices(s, srcs.get(s["source_id"])) for s in _samples(p)), key=lambda s: -(s.get("score") or 0))


@app.post("/api/projects/{id}/samples", status_code=201)
def create_sample(id: str, body: schemas.SampleCreate, user: User):
    p = _own("project", id, user)
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
def patch_sample(id: str, body: schemas.SampleUpdate, user: User):
    s = _own("sample", id, user)
    src = _get("source", s["source_id"])
    ch = body.model_dump(exclude_none=True)
    if "start" in ch or "end" in ch:  # moving a region re-derives its length in beats
        new = sampling.manual(src, src["analysis"], ch.get("start", s["start"]), ch.get("end", s["end"]), s["stems"], s["name"])
        ch.update({k: new[k] for k in ("start", "end", "duration", "beats", "bars", "kind", "chroma", "peaks", "rms_db")}, pickup=0.0)
    if "transform" in ch:
        ch["transform"] = {**(s.get("transform") or {}), **ch["transform"]}
    return _with_slices(store.update("sample", id, **ch), src)


@app.delete("/api/samples/{id}", status_code=204)
def delete_sample(id: str, user: User):
    s = _own("sample", id, user)
    if id in _referenced(s["project_id"]):
        raise HTTPException(409, "This sample is used by the arrangement or a generated variation.")
    store.delete("sample", id)


def _sample_file(s: dict, bpm: float | None) -> Path:
    src = _get("source", s["source_id"])
    paths, hashes = sampling.resolve(src, s["stems"])
    t = {**transform.DEFAULT_TRANSFORM, **(s.get("transform") or {})}
    speed = (bpm / s["source_bpm"] / transform.tape_rate(t)) if bpm else 1.0  # with bpm: locked to that tempo
    y = transform.cached_transform(paths, hashes, s["start"], s["end"], t, speed)
    out = data_path("cache", "previews", cache_key("smp", hashes, s["start"], s["end"], t, round(speed, 6)) + ".wav")
    return out if out.exists() else audio_io.write_wav(out, np.clip(y, -1, 1), subtype="PCM_16")


@app.get("/api/samples/{id}/audio")
def sample_audio(id: str, user: User, bpm: float | None = None):
    if bpm is not None and not 40 <= bpm <= 220:
        raise HTTPException(400, "bpm must be 40-220")
    return FileResponse(_sample_file(_own("sample", id, user), bpm), media_type="audio/wav")


@app.post("/api/samples/{id}/transform")
def sample_transform(id: str, body: dict, user: User):
    """Pitch / reverse / fades for a sample; returns the updated sample and where to hear it."""
    upd = patch_sample(id, schemas.SampleUpdate(transform=body), user)
    return {**upd, "preview": {"url": f"/api/samples/{id}/audio"}}


# ---------------------------------------------------------------- generation

_active: dict[str, set[str]] = {}  # user id -> generation jobs still running (per process)


def _submit_generation(user: dict, job_type: str, project_id: str, params: dict) -> dict:
    """Plan check, one generation at a time per user, and metering (counted when the job completes)."""
    accounts.check_generation(user)
    mine = _active.setdefault(user["id"], set())
    mine -= {j for j in mine if (store.get("job", j) or {"status": "failed"})["status"] in jobs.TERMINAL}
    if mine and not accounts.self_hosted():
        raise HTTPException(409, "A beat is already being made. Wait for it to finish.")
    job = jobs.submit(job_type, project_id, {**params, "user_id": user["id"]})
    mine.add(job["id"])
    return job


@app.post("/api/beats", status_code=202)
async def make_beat(user: AnyUser, file: UploadFile | None = File(None), settings: str = Form("{}"),
                    name: str | None = Form(None)):
    """The whole product in one call: upload a sample (or omit the file for the demo loop) and get a job that
    ends with rendered variations, the best one already opened in the studio."""
    try:
        st = schemas.GenerateSettings.model_validate(json.loads(settings or "{}")).model_dump()
    except json.JSONDecodeError:
        raise HTTPException(400, "settings must be a JSON object")
    accounts.check_generation(user)
    title = name or re.sub(r"\.[A-Za-z0-9]{1,5}$", "", (file.filename if file else None) or "Demo loop")
    project = _new_project(user, f"{title} flip"[:80])
    try:
        src = await _add_source(project, user, file)
        store.update("project", project["id"], generator_settings=st)
        job = _submit_generation(user, "beat", project["id"],
                                 {"project_id": project["id"], "source_id": src["id"], "settings": st})
    except Exception:
        _delete_project(_get("project", project["id"]), force=True)
        raise
    return {"project": _pub_project(_get("project", project["id"])), "source": _pub_source(src), "job": job}


@app.post("/api/projects/{id}/generate", status_code=202)
def generate(id: str, body: schemas.GenerateSettings, user: User):
    p = _own("project", id, user)
    _inputs(p)  # fail fast with a clear message
    settings = body.model_dump()
    store.update("project", id, generator_settings=settings)
    return _submit_generation(user, "generate", id, {"project_id": id, "settings": settings})


@app.get("/api/projects/{id}/variations")
def variations(id: str, user: User):
    _own("project", id, user)
    vs = sorted(store.list_("variation", id), key=lambda v: (-v["batch"], v["created_at"]))
    return [_pub_variation(v, full=False) for v in vs]


@app.get("/api/variations/{id}")
def get_variation(id: str, user: User):
    return _pub_variation(_own("variation", id, user))


@app.get("/api/variations/{id}/audio")
def variation_audio(id: str, user: User):
    return FileResponse(_own("variation", id, user)["preview_path"], media_type="audio/wav")


@app.post("/api/variations/{id}/regenerate", status_code=202)
def regenerate(id: str, user: User):
    v = _own("variation", id, user)
    return _submit_generation(user, "regenerate", v["project_id"], {"variation_id": id})


@app.delete("/api/variations/{id}", status_code=204)
def delete_variation(id: str, user: User):
    v = _own("variation", id, user)
    store.delete("variation", id)
    Path(v["preview_path"]).unlink(missing_ok=True)


@app.post("/api/variations/{id}/open-in-studio")
def open_in_studio(id: str, user: User):
    return _pub_project(_open(_own("variation", id, user)))


# ---------------------------------------------------------------- arrangement

def _check_assets(project_id: str, arr: dict) -> None:
    """An arrangement may only point at audio inside its own project (never another user's files)."""
    p = (arr.get("bass") or {}).get("sample_path")
    if p and (DATA_DIR / "projects" / project_id / "sounds").resolve() not in resolve_data(p).resolve().parents:
        raise ValueError("808 sample must be a file uploaded to this project")


@app.get("/api/projects/{id}/arrangement")
def get_arrangement(id: str, user: User):
    a = _own("project", id, user).get("arrangement")
    if not a:
        raise HTTPException(404, "No arrangement yet: open a variation in the studio.")
    return a


@app.put("/api/projects/{id}/arrangement")
def put_arrangement(id: str, body: dict, user: User):
    p = _own("project", id, user)
    arr = schemas.arrangement(body)
    _check_assets(id, arr)
    samples = {s["id"] for s in _samples(p)}
    missing = {e["sample_id"] for pat in arr["patterns"].values() for e in pat["events"] if e["type"] == "chop"} - samples
    if missing:
        raise HTTPException(400, f"Arrangement references unknown samples: {sorted(missing)}")
    store.update("project", id, arrangement=arr, target_bpm=arr["bpm"], musical_key=arr.get("key"))
    return arr


@app.post("/api/projects/{id}/preview")
def preview(id: str, body: schemas.PreviewRequest, user: User):
    """Renders through the same engine as export, so what you hear is what you export."""
    p = _own("project", id, user)
    arr = schemas.arrangement(body.arrangement or p.get("arrangement") or {})
    _check_assets(id, arr)
    res = render.render(arr, {s["id"]: s for s in _samples(p)}, _sources(p), body.scope, body.loop_start_bar, body.loop_bars)
    d = _dir(id, "previews")
    for old in d.glob("studio_*.wav"):  # keep one studio preview on disk
        old.unlink(missing_ok=True)
    name = f"studio_{store.new_id('r')}"
    render.write_preview(d / name, res["master"])
    return {"url": f"/api/projects/{id}/preview/{name}.wav", "metrics": res["metrics"],
            "start_bar": body.loop_start_bar if body.scope == "loop" else 0}


@app.get("/api/projects/{id}/preview/{name}")
def preview_file(id: str, name: str, user: User):
    _own("project", id, user)
    if not re.fullmatch(r"studio_r_[0-9a-f]{12}\.wav", name):
        raise HTTPException(404)
    f = DATA_DIR / "projects" / id / "previews" / name
    if not f.exists():
        raise HTTPException(404, "Preview expired; render again.")
    return FileResponse(f, media_type="audio/wav")


@app.post("/api/projects/{id}/bass-sample", status_code=201)
async def bass_sample(id: str, user: User, file: UploadFile = File(...)):
    """A custom 808 one-shot (tuned from its root note at render time)."""
    _own("project", id, user)
    tmp = _dir(id, "sounds") / f"upload{_ext(file.filename)}"
    await _save_upload(file, tmp, 20)
    try:
        y = audio_io.decode(tmp)[: SR * 4]
    finally:
        tmp.unlink(missing_ok=True)
    out = audio_io.write_wav(_dir(id, "sounds") / f"808_{store.new_id('s')}.wav", y)
    return {"sample_path": str(out.relative_to(DATA_DIR))}


# ---------------------------------------------------------------- export

@app.post("/api/projects/{id}/export", status_code=202)
def export(id: str, body: schemas.ExportRequest, user: User):
    p = _own("project", id, user)
    if not p.get("arrangement"):
        raise HTTPException(409, "Open a variation in the studio before exporting.")
    _registered(user, "Create a free account to download your beat.")
    accounts.require(user, "formats", body.format)
    for flag, feature in ((body.stems, "stems"), (body.midi, "midi"), (body.project_json, "project_file")):
        if flag:
            accounts.require(user, feature)
    eid = store.new_id("exp")
    req = body.model_dump()
    store.put("export", {"id": eid, "project_id": id, "request": req, "status": "queued"}, parent=id)
    store.update("project", id, export_settings=req)
    job = jobs.submit("export", id, {"project_id": id, "export_id": eid, "request": req,
                                     "license_text": accounts.license_text(user, p["name"])})
    return {"export_id": eid, "job": job}


@app.get("/api/projects/{id}/exports")
def list_exports(id: str, user: User):
    _own("project", id, user)
    return [_pub_export(e) for e in store.list_("export", id)]


@app.get("/api/exports/{id}")
def get_export(id: str, user: User):
    return _pub_export(_own("export", id, user))


@app.get("/api/exports/{id}/download")
def download(id: str, user: User, name: str | None = None):
    e = _own("export", id, user)
    files = {f["name"]: f["path"] for f in e.get("files", [])}
    if not files:
        raise HTTPException(409, "Export is not finished.")
    name = name or next(iter(files))
    if name not in files:  # only files this export produced: no path traversal
        raise HTTPException(404, "No such file in this export")
    return FileResponse(files[name], filename=name)


# ---------------------------------------------------------------- kits (built-in sounds, read-only)

@app.get("/api/kits/{kit}/{inst}/audio")
def kit_audio(kit: str, inst: str):
    if kit not in drums.KITS or inst not in drums.INSTRUMENTS:
        raise HTTPException(404, "Unknown kit or instrument")
    drums.sound(kit, inst)  # materialize the synthesized default
    return FileResponse(drums.kit_path(kit, inst), media_type="audio/wav")


# ---------------------------------------------------------------- jobs

@app.get("/api/jobs/{id}")
def get_job(id: str, user: User):
    return _own("job", id, user)


@app.get("/api/projects/{id}/jobs")
def project_jobs(id: str, user: User):
    _own("project", id, user)
    return sorted(store.list_("job", id), key=lambda j: -j["created_at"])[:50]


@app.get("/api/jobs/{id}/events")
async def job_events(id: str, request: Request, user: User):
    _own("job", id, user)

    async def stream():
        last = None
        while not await request.is_disconnected():
            job = store.get("job", id)
            if job is None:
                return
            s = json.dumps(job)
            if s != last:
                yield f"data: {s}\n\n"
                last = s
            if job["status"] in jobs.TERMINAL:
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{id}/cancel")
def cancel_job(id: str, user: User):
    _own("job", id, user)
    return jobs.cancel(id)


@app.post("/api/jobs/{id}/retry", status_code=202)
def retry_job(id: str, user: User):
    j = _own("job", id, user)
    if j["status"] not in ("failed", "canceled", "interrupted"):
        raise HTTPException(409, "Only failed, canceled or interrupted jobs can be retried.")
    if j["job_type"] in GENERATION_JOBS:
        accounts.check_generation(user)
    return jobs.retry(id)


# ---------------------------------------------------------------- system

@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version, "engine": ENGINE_VERSION}


@app.get("/api/system/capabilities")
def capabilities():
    """Also serves as UI configuration: genres, strategies, kits and default settings come from here."""
    return {"ffmpeg": audio_io.ffmpeg_exe() is not None, "sample_rate": SR, "schema_version": SCHEMA_VERSION,
            "genres": {k: {**v, "bpm": list(v["bpm"]), "swing": list(v["swing"])} for k, v in drums.GENRES.items()},
            "strategies": compose.STRATEGIES, "kits": {k: v["label"] for k, v in drums.KITS.items()},
            "instruments": drums.INSTRUMENTS, "track_of": drums.TRACK_OF, "fx_defaults": transform.DEFAULT_FX,
            "transform_defaults": transform.DEFAULT_TRANSFORM, "bass_defaults": bass.DEFAULT_808,
            "generate_defaults": schemas.GenerateSettings().model_dump(), "formats": list(render.FORMATS),
            "separation_models": [a.info() for a in separation.ADAPTERS.values()]}


# ---------------------------------------------------------------- frontend (built SPA)

if FRONTEND.is_dir():
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (FRONTEND / path).resolve()
        if path and f.is_file() and FRONTEND.resolve() in f.parents:
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "public, max-age=300"
            return FileResponse(f, headers={"Cache-Control": cache})
        return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})


def main():
    import uvicorn

    host = os.environ.get("CHOPLAB_HOST", "127.0.0.1")
    port = int(os.environ.get("CHOPLAB_PORT", os.environ.get("PORT", "8765")))
    log.info("ChopLab data dir: %s (self-hosted=%s, billing=%s)", DATA_DIR, accounts.self_hosted(), billing.mode())
    uvicorn.run("choplab.api:app", host=host, port=port, log_level=os.environ.get("CHOPLAB_LOG", "info"),
                proxy_headers=os.environ.get("CHOPLAB_TRUST_PROXY") == "1")
