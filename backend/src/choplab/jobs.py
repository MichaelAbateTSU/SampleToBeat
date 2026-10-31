"""Persistent local job system: SQLite job records + bounded thread pool. Heavy separation jobs are
serialized through their own single-worker pool so two models never run at once."""
from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable

from . import store
from .core import data_path, log

TERMINAL = {"completed", "failed", "canceled", "interrupted"}
WORKERS = int(os.environ.get("CHOPLAB_JOB_WORKERS", "1" if os.environ.get("CHOPLAB_MODE") == "hosted" else "2"))
MAX_PENDING = int(os.environ.get("CHOPLAB_MAX_PENDING_JOBS", "8" if os.environ.get("CHOPLAB_MODE") == "hosted" else "64"))
if not 1 <= WORKERS <= 8 or not 1 <= MAX_PENDING <= 256:
    raise ValueError("CHOPLAB_JOB_WORKERS must be 1-8 and CHOPLAB_MAX_PENDING_JOBS must be 1-256")
_compute = threading.BoundedSemaphore(WORKERS)
_pending = threading.BoundedSemaphore(MAX_PENDING)
_pools = {"default": ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="job"),
          "separation": ThreadPoolExecutor(max_workers=1, thread_name_prefix="sep")}
_cancel: set[str] = set()
_handlers: dict[str, tuple[Callable, str]] = {}
_state = threading.Condition(threading.RLock())
_active: set[str] = set()
_volume_id: str | None = None
recovery_error: str | None = None
INTERRUPTION = "Application stopped before this job finished. Nothing was automatically retried. Retry to run it again; saved results were retained."
RECOVERY_COMMAND = "python -m choplab.metadata recover-jobs --confirm-all-runtimes-stopped"


class Canceled(Exception):
    pass


class Busy(Exception):
    pass


@contextmanager
def runtime(*, offline_recovery: bool = False):
    """Hold the audio-volume lock until even gracefully draining workers exit."""
    global _volume_id, recovery_error
    with open(data_path("jobs.lock"), "a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise store.StoreConfigurationError(
                "Another ChopLab runtime owns this audio volume. Stop it before starting another worker.") from None
        try:
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(1)
            try:
                identity = lock.read().decode("ascii").strip()
            except UnicodeError:
                raise store.StoreConfigurationError("Invalid jobs.lock identity; restore the matching audio-volume backup.") from None
            if identity:
                try:
                    uuid.UUID(identity)
                except ValueError:
                    raise store.StoreConfigurationError("Invalid jobs.lock identity; restore the matching audio-volume backup.") from None
            else:
                identity = uuid.uuid4().hex
                lock.write(identity.encode("ascii"))
                lock.flush()
            _volume_id = identity
            recovery_error = None
            try:
                store.bind_job_volume(identity)
                recover_interrupted(confirmed_stopped=offline_recovery)
            except store.StoreConfigurationError as e:
                if offline_recovery:
                    raise
                recovery_error = f"{e} Back up matching metadata/audio, stop ALL runtimes, then run {RECOVERY_COMMAND} on the matching volume."
                log.error("Job recovery blocked; API is read-only: %s", recovery_error)
            yield
        finally:
            with _state:
                while _active:
                    _state.wait()
                _volume_id = None
                recovery_error = None
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def compute_slot(*, wait: bool = False):
    if not _compute.acquire(blocking=wait):
        raise Busy("Audio engine is busy; wait for the running job and try again.")
    try:
        yield
    finally:
        _compute.release()


class JobContext:
    def __init__(self, job_id: str):
        self.id = job_id

    def progress(self, stage: str, fraction: float | None = None) -> None:
        if self.id in _cancel:
            raise Canceled()
        store.update("job", self.id, current_stage=stage, progress=fraction)


def handler(job_type: str, pool: str = "default"):
    def deco(fn):
        _handlers[job_type] = (fn, pool)
        return fn
    return deco


def _target(job_type: str, params: dict) -> tuple[str, str, str, str, str] | None:
    if job_type in ("import", "analyze"):
        return "source", params["source_id"], "analysis_status", "analysis_job_id", "error"
    if job_type == "separate":
        return "source", params["source_id"], "separation_status", "separation_job_id", "separation_error"
    if job_type == "export":
        return "export", params["export_id"], "status", "job_id", "error"
    return None


def _submit(job_type: str, project_id: str | None, params: dict) -> dict:
    if recovery_error:
        raise Busy(recovery_error)
    target = _target(job_type, params)
    if target:
        kind, id, status, owner, error = target
        doc = store.get(kind, id)
        if doc is None:
            raise KeyError(id)
        # Analysis and separation both rewrite sample discovery for the source.
        for other in store.list_("job", project_id):
            other_target = _target(other["job_type"], other["params"])
            if other["status"] not in TERMINAL and other_target and other_target[:2] == target[:2]:
                raise Busy("This source or export already has queued/running work; wait for it to finish.")
    if not _pending.acquire(blocking=False):
        # Upload/export records already exist when the job is submitted.
        if target and doc.get(status) == "queued":
            store.update(kind, id, **{status: "failed", error: "Job queue is full; try again after a running job finishes."})
        raise Busy("Job queue is full; wait for a job to finish and try again.")
    job = None
    try:
        fn, pool = _handlers[job_type]
        job = store.put("job", {"id": store.new_id("job"), "project_id": project_id, "job_type": job_type,
                            "params": params, "status": "queued", "progress": 0.0, "current_stage": "queued",
                            "started_at": None, "completed_at": None, "error_message": None, "result": None,
                            "owner_volume": _volume_id},
                    parent=project_id)
        if target:
            store.update(kind, id, **{status: "queued", owner: job["id"], error: None})
        _active.add(job["id"])
        _pools[pool].submit(_run, job["id"], fn, params)
        return job
    except Exception as e:
        try:
            if job:
                _active.discard(job["id"])
                store.update("job", job["id"], status="failed", error_message=str(e), completed_at=store.now())
                _finish_target(job, "failed", str(e))
        finally:
            _pending.release()
        raise


def submit(job_type: str, project_id: str | None, params: dict) -> dict:
    with _state:
        return _submit(job_type, project_id, params)


def _finish_target(job: dict, terminal: str, message: str) -> None:
    target = _target(job["job_type"], job["params"])
    if not target:
        return
    kind, id, status, owner, error = target
    with _state:
        doc = store.get(kind, id)
        if doc and doc.get(owner) == job["id"] and doc.get(status) in ("queued", "analyzing", "running"):
            store.update(kind, id, **{status: terminal, error: message})


def _run(job_id: str, fn: Callable, params: dict) -> None:
    try:
        with compute_slot(wait=True):
            if job_id in _cancel:
                raise Canceled()
            store.update("job", job_id, status="running", started_at=store.now())
            t0 = time.perf_counter()
            result = fn(JobContext(job_id), **params)
            store.update("job", job_id, status="completed", progress=1.0, current_stage="done", result=result,
                         completed_at=store.now(), duration_s=round(time.perf_counter() - t0, 2))
    except Canceled:
        store.update("job", job_id, status="canceled", completed_at=store.now())
        _finish_target(store.get("job", job_id), "canceled", "Job canceled. Retry to run it again; saved results were retained.")
    except Exception as e:  # surfaced to the UI; full trace in the log
        log.error("job %s failed:\n%s", job_id, traceback.format_exc())
        store.update("job", job_id, status="failed", error_message=f"{type(e).__name__}: {e}",
                     completed_at=store.now())
        _finish_target(store.get("job", job_id), "failed", f"{type(e).__name__}: {e}")
    finally:
        _cancel.discard(job_id)
        _pending.release()
        with _state:
            _active.discard(job_id)
            _state.notify_all()


def cancel(job_id: str) -> dict:
    job = store.get("job", job_id)
    if job and job["status"] not in TERMINAL:
        _cancel.add(job_id)
    return job


def retry(job_id: str) -> dict:
    with _state:
        job = store.get("job", job_id)
        if not job:
            raise KeyError(job_id)
        if job["status"] not in ("failed", "canceled", "interrupted"):
            raise ValueError("Only failed, canceled or interrupted jobs can be retried.")
        target = _target(job["job_type"], job["params"])
        if target:
            kind, id, status, owner, _ = target
            doc = store.get(kind, id)
            if doc and doc.get(owner) not in (None, job_id):
                raise Busy("Newer work owns this source or export. Use its latest job or start a new operation instead.")
            if doc and doc.get(owner) is None and doc.get(status) in ("analyzed", "done", "completed"):
                raise Busy("Saved results have no verifiable job owner. Start a new operation instead of retrying older work.")
            newer = [j for j in store.list_("job", job["project_id"])
                     if j["id"] != job_id and (t := _target(j["job_type"], j["params"]))
                     and t[:2] == target[:2]
                     and ((doc or {}).get(owner) != job_id or t[3] != owner)
                     and j["created_at"] >= job["created_at"]]
            if newer:
                raise Busy("Newer work exists for this source or export. Retry its latest job instead.")
        return _submit(job["job_type"], job["project_id"], job["params"])


def recover_interrupted(*, confirmed_stopped: bool = False) -> None:
    """Only the exclusively locked runtime may recover its previous work."""
    if _volume_id is None:
        raise store.StoreConfigurationError("Restart recovery requires the exclusive jobs.runtime audio-volume lock.")
    with _state:
        if _active:
            raise Busy("Current-process jobs are still active; refusing restart recovery.")
        all_jobs = store.list_("job")
        pending = [j for j in all_jobs if j["status"] in ("queued", "running")]
        if not confirmed_stopped and store.backend_name() == "postgres" and any(j.get("owner_volume") != _volume_id for j in pending):
            raise store.StoreConfigurationError(
                "Unowned or foreign PostgreSQL jobs may still be running. Stop all runtimes and resolve their "
                "ownership offline before recovery; refusing to guess that another process stopped.")
        for job in pending:
            store.update("job", job["id"], status="interrupted", current_stage="interrupted",
                         completed_at=store.now(), error_message=INTERRUPTION)
        # Also repair records created just before submit, or a previous recovery
        # that persisted the job status but stopped before updating its target.
        for kind in ("source", "export"):
            for doc in store.list_(kind):
                targets = [("analysis_status", "error"), ("separation_status", "separation_error")] if kind == "source" else [("status", "error")]
                for status, error in targets:
                    if doc.get(status) not in ("queued", "analyzing", "running"):
                        continue
                    store.update(kind, doc["id"], **{status: "interrupted", error: INTERRUPTION})


def wait(job_id: str, timeout: float = 600) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = store.get("job", job_id)
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.1)
    raise TimeoutError(job_id)
