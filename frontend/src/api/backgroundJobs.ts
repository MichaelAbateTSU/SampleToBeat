import { useEffect, useState } from "react";
import { api } from "./client";
import { TERMINAL, useJobs } from "./hooks";

export const BACKGROUND_JOB_MS = 30_000;
export const BACKGROUND_JOB_FAILURES = 3;

/** Genuine bounded job observation, not a health/provider keepalive. */
export function useBackgroundJobs(hosted: boolean) {
  const [errors, setErrors] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!hosted) return;
    let alive = true;
    const pending = new Set<string>();
    const failures = new Map<string, number>();
    const poll = async (returning = false) => {
      for (const job of Object.values(useJobs.getState().jobs)) {
        if (TERMINAL.has(job.status) || pending.has(job.id)) continue;
        if ((failures.get(job.id) ?? 0) >= BACKGROUND_JOB_FAILURES && !returning) continue;
        pending.add(job.id);
        void (async () => {
          try {
            const fresh = await api.job(job.id);
            if (!alive || TERMINAL.has(useJobs.getState().jobs[job.id]?.status ?? "completed")) return;
            // A snapshot must not overwrite progress received through SSE after it started.
            if (useJobs.getState().jobs[job.id] === job || TERMINAL.has(fresh.status)) {
              useJobs.getState().update(fresh);
            }
            failures.delete(job.id);
            setErrors((previous) => {
              const next = { ...previous };
              delete next[job.id];
              return next;
            });
          } catch (error) {
            if (!alive || TERMINAL.has(useJobs.getState().jobs[job.id]?.status ?? "completed")) return;
            const count = (failures.get(job.id) ?? 0) + 1;
            failures.set(job.id, count);
            const detail = error instanceof Error ? error.message : String(error);
            setErrors((previous) => ({
              ...previous,
              [job.id]: `Job ${job.id} status could not be refreshed: ${detail}. ${
                count >= BACKGROUND_JOB_FAILURES
                  ? "Background observation paused after repeated failures; return to this tab to retry."
                  : "Background observation will retry."
              }`,
            }));
          } finally {
            pending.delete(job.id);
          }
        })();
      }
    };
    const visibility = () => {
      if (document.visibilityState === "visible") {
        failures.clear();
        void poll(true);
      }
    };
    const unsubscribe = useJobs.subscribe((state) => {
      setErrors((previous) => {
        const next = Object.fromEntries(Object.entries(previous).filter(([id]) =>
          state.jobs[id] && !TERMINAL.has(state.jobs[id].status)));
        return Object.keys(next).length === Object.keys(previous).length ? previous : next;
      });
    });
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") void poll();
    }, BACKGROUND_JOB_MS);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      alive = false;
      clearInterval(timer);
      unsubscribe();
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [hosted]);
  return hosted ? Object.values(errors).join(" ") : "";
}
