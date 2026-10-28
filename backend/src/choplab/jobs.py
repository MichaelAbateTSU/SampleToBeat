"""Persistent local job system: SQLite job records + bounded thread pool. Heavy separation jobs are
serialized through their own single-worker pool so two models never run at once."""
from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import store
from .core import log

TERMINAL = {"completed", "failed", "canceled", "interrupted"}
_pools = {"default": ThreadPoolExecutor(max_workers=2, thread_name_prefix="job"),
          "separation": ThreadPoolExecutor(max_workers=1, thread_name_prefix="sep")}
_cancel: set[str] = set()
_handlers: dict[str, Callable] = {}


class Canceled(Exception):
    pass


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


def submit(job_type: str, project_id: str | None, params: dict) -> dict:
    job = store.put("job", {"id": store.new_id("job"), "project_id": project_id, "job_type": job_type,
                            "params": params, "status": "queued", "progress": 0.0, "current_stage": "queued",
                            "started_at": None, "completed_at": None, "error_message": None, "result": None},
                    parent=project_id)
    fn, pool = _handlers[job_type]
    _pools[pool].submit(_run, job["id"], fn, params)
    return job


def _run(job_id: str, fn: Callable, params: dict) -> None:
    if job_id in _cancel:
        store.update("job", job_id, status="canceled", completed_at=store.now())
        return
    store.update("job", job_id, status="running", started_at=store.now())
    t0 = time.perf_counter()
    try:
        result = fn(JobContext(job_id), **params)
        store.update("job", job_id, status="completed", progress=1.0, current_stage="done", result=result,
                     completed_at=store.now(), duration_s=round(time.perf_counter() - t0, 2))
    except Canceled:
        store.update("job", job_id, status="canceled", completed_at=store.now())
    except Exception as e:  # surfaced to the UI; full trace in the log
        log.error("job %s failed:\n%s", job_id, traceback.format_exc())
        store.update("job", job_id, status="failed", error_message=f"{type(e).__name__}: {e}",
                     completed_at=store.now())
    finally:
        _cancel.discard(job_id)


def cancel(job_id: str) -> dict:
    job = store.get("job", job_id)
    if job and job["status"] not in TERMINAL:
        _cancel.add(job_id)
    return job


def retry(job_id: str) -> dict:
    job = store.get("job", job_id)
    return submit(job["job_type"], job["project_id"], job["params"])


def recover_interrupted() -> None:
    """Jobs left queued/running by a previous process can't still be running."""
    for job in store.list_("job"):
        if job["status"] in ("queued", "running"):
            store.update("job", job["id"], status="interrupted",
                         error_message="Application stopped while this job was running; retry it.")


def wait(job_id: str, timeout: float = 600) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = store.get("job", job_id)
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.1)
    raise TimeoutError(job_id)
