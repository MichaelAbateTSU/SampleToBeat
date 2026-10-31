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
  track: (job: Job) => void;
  update: (job: Job) => void;
}

const streams = new Map<string, EventSource | number>();

/** Live job progress over SSE (falls back to polling where EventSource is unavailable). */
export const useJobs = create<JobState>((set, get) => ({
  jobs: {},
  update: (job) => {
    const prev = get().jobs[job.id];
    set((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));
    if (TERMINAL.has(job.status) && !(prev && TERMINAL.has(prev.status))) {
      const st = streams.get(job.id);
      if (typeof st === "number") clearInterval(st);
      else st?.close();
      streams.delete(job.id);
      invalidateFor(job);
    }
  },
  track: (job) => {
    get().update(job);
    if (TERMINAL.has(job.status) || streams.has(job.id)) return;
    if (typeof EventSource !== "undefined") {
      const es = new EventSource(urls.jobEvents(job.id));
      es.onmessage = (e) => get().update(JSON.parse(e.data) as Job);
      es.onerror = () => {
        es.close();
        streams.delete(job.id);
        // reconnect by polling; the server is the source of truth
        const t = window.setInterval(async () => {
          try {
            get().update(await api.job(job.id));
          } catch {
            /* keep trying */
          }
        }, 1000);
        streams.set(job.id, t);
      };
      streams.set(job.id, es);
    } else {
      const t = window.setInterval(async () => get().update(await api.job(job.id)), 1000);
      streams.set(job.id, t);
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
