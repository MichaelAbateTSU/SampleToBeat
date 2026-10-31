import { QueryClient, useMutation, useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { create } from "zustand";
import { api, urls } from "./client";
import type { Job } from "./types";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false } },
});

export const keys = {
  projects: ["projects"] as const,
  project: (id: string) => ["project", id] as const,
  samples: (pid: string) => ["samples", pid] as const,
  variations: (pid: string) => ["variations", pid] as const,
  exports: (pid: string) => ["exports", pid] as const,
  jobs: (pid: string) => ["jobs", pid] as const,
  caps: ["capabilities"] as const,
  models: ["models"] as const,
  kits: ["kits"] as const,
};

export const TERMINAL = new Set(["completed", "failed", "canceled", "interrupted"]);

/** Refresh whatever a finished job may have changed. */
function invalidateFor(job: Job) {
  const pid = job.project_id;
  queryClient.invalidateQueries({ queryKey: keys.project(pid) });
  queryClient.invalidateQueries({ queryKey: keys.jobs(pid) });
  queryClient.invalidateQueries({ queryKey: keys.projects });
  if (["import", "analyze", "separate"].includes(job.job_type)) {
    queryClient.invalidateQueries({ queryKey: keys.samples(pid) });
  }
  if (["generate", "regenerate"].includes(job.job_type)) {
    queryClient.invalidateQueries({ queryKey: keys.variations(pid) });
  }
  if (job.job_type === "export") queryClient.invalidateQueries({ queryKey: keys.exports(pid) });
}

interface JobState {
  jobs: Record<string, Job>;
  trackingErrors: Record<string, string | undefined>;
  track: (job: Job) => void;
  update: (job: Job) => void;
}

interface Tracking {
  stream?: EventSource;
  timer: number;
  pending: boolean;
}
const streams = new Map<string, Tracking>();
const RESYNC_MS = 30_000;
const FALLBACK_MS = 1_000;

/** SSE plus visible-tab resynchronization; this is observation, not an uptime guarantee. */
export const useJobs = create<JobState>((set, get) => ({
  jobs: {},
  trackingErrors: {},
  update: (job) => {
    const prev = get().jobs[job.id];
    if (prev && TERMINAL.has(prev.status)) return;
    set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));
    if (TERMINAL.has(job.status)) {
      const st = streams.get(job.id);
      if (st) {
        clearInterval(st.timer);
        st.stream?.close();
      }
      streams.delete(job.id);
      set((s) => ({ trackingErrors: { ...s.trackingErrors, [job.id]: undefined } }));
      invalidateFor(job);
    }
  },
  track: (job) => {
    const first = !get().jobs[job.id];
    get().update(job);
    if (first && !TERMINAL.has(job.status)) invalidateFor(job);
    if (TERMINAL.has(get().jobs[job.id].status) || streams.has(job.id)) return;
    const tracking: Tracking = { timer: 0, pending: false };
    streams.set(job.id, tracking);
    const poll = async () => {
      if (streams.get(job.id) !== tracking || tracking.pending || document.visibilityState !== "visible") return;
      tracking.pending = true;
      const before = get().jobs[job.id];
      try {
        const fresh = await api.job(job.id);
        if (streams.get(job.id) !== tracking) return;
        // Don't replace a newer SSE update with a snapshot fetched earlier.
        if (get().jobs[job.id] === before || TERMINAL.has(fresh.status)) get().update(fresh);
        set((s) => ({ trackingErrors: { ...s.trackingErrors, [job.id]: undefined } }));
      } catch (error) {
        if (streams.get(job.id) === tracking) {
          const detail = error instanceof Error ? error.message : String(error);
          set((s) => ({ trackingErrors: { ...s.trackingErrors, [job.id]: `Job status could not be refreshed: ${detail}. Observation will retry while this tab is visible.` } }));
        }
      } finally {
        tracking.pending = false;
      }
    };
    tracking.timer = window.setInterval(poll, typeof EventSource !== "undefined" ? RESYNC_MS : FALLBACK_MS);
    if (typeof EventSource !== "undefined") {
      const es = new EventSource(urls.jobEvents(job.id));
      tracking.stream = es;
      es.onmessage = (e) => {
        if (streams.get(job.id) === tracking) get().update(JSON.parse(e.data) as Job);
      };
      es.onerror = () => {
        if (streams.get(job.id) !== tracking || tracking.stream !== es) return;
        es.close();
        tracking.stream = undefined;
        clearInterval(tracking.timer);
        tracking.timer = window.setInterval(poll, FALLBACK_MS);
      };
    }
  },
}));

export function useJob(id: string | null | undefined): Job | undefined {
  return useJobs((s) => (id ? s.jobs[id] : undefined));
}

export function useProjectJobs(pid: string) {
  const q = useQuery({ queryKey: keys.jobs(pid), queryFn: () => api.projectJobs(pid) });
  const live = useJobs((s) => s.jobs);
  const track = useJobs((s) => s.track);
  useEffect(() => {
    // resume tracking for jobs that were already running when the page loaded
    for (const j of q.data ?? []) if (!TERMINAL.has(j.status)) track(j);
  }, [q.data, track]);
  return (q.data ?? []).map((j) => live[j.id] ?? j);
}

export const useCapabilities = () => useQuery({ queryKey: keys.caps, queryFn: api.capabilities, staleTime: Infinity });
export const useModels = () => useQuery({ queryKey: keys.models, queryFn: api.models });
export const useProjects = () => useQuery({ queryKey: keys.projects, queryFn: api.projects });
export const useProject = (id: string) => useQuery({ queryKey: keys.project(id), queryFn: () => api.project(id) });
export const useSamples = (pid: string) => useQuery({ queryKey: keys.samples(pid), queryFn: () => api.samples(pid) });
export const useVariations = (pid: string) => useQuery({ queryKey: keys.variations(pid), queryFn: () => api.variations(pid) });
export const useExports = (pid: string) => useQuery({ queryKey: keys.exports(pid), queryFn: () => api.exports(pid) });
export const useKits = () => useQuery({ queryKey: keys.kits, queryFn: api.kits });

/** Mutation that starts a backend job and tracks it. */
export function useJobMutation<A>(fn: (a: A) => Promise<Job>) {
  const track = useJobs((s) => s.track);
  return useMutation({ mutationFn: fn, onSuccess: (job) => track(job) });
}
