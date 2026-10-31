import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { keys, queryClient, useJobs } from "./hooks";
import type { Job } from "./types";

class MockEventSource {
  static instances: MockEventSource[] = [];
  close = vi.fn();
  onmessage?: (event: { data: string }) => void;
  onerror?: () => void;
  constructor() { MockEventSource.instances.push(this); }
  message(job: Job) { this.onmessage?.({ data: JSON.stringify(job) }); }
}

const job: Job = {
  id: "observed", project_id: "p", job_type: "export", status: "running",
  progress: 0.2, current_stage: "rendering", error_message: null, result: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
  vi.stubGlobal("EventSource", MockEventSource);
  MockEventSource.instances = [];
  useJobs.setState({ jobs: {}, trackingErrors: {} });
});

afterEach(() => {
  for (const job of Object.values(useJobs.getState().jobs)) {
    if (job.status === "running" || job.status === "queued") {
      useJobs.getState().update({ ...job, status: "canceled" });
    }
  }
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("refreshes queued retry records immediately and closes tracking when finished", () => {
  const close = vi.fn();
  vi.stubGlobal("EventSource", class { close = close; });
  const invalidate = vi.mocked(queryClient.invalidateQueries);
  const job: Job = {
    id: "queued-retry", project_id: "p", job_type: "export", status: "queued",
    progress: 0, current_stage: "queued", error_message: null, result: null,
  };
  useJobs.getState().track(job);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.jobs("p") });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.exports("p") });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.project("p") });
  useJobs.getState().update({ ...job, status: "completed" });
  expect(close).toHaveBeenCalledOnce();
});

it("resynchronizes every 30 seconds without replacing SSE or duplicating tracking", async () => {
  const poll = vi.spyOn(api, "job").mockResolvedValue({ ...job, progress: 0.4 });
  useJobs.getState().track(job);
  useJobs.getState().track(job);
  expect(MockEventSource.instances).toHaveLength(1);
  expect(vi.getTimerCount()).toBe(1);
  await vi.advanceTimersByTimeAsync(29_999);
  expect(poll).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(poll).toHaveBeenCalledOnce();
  expect(useJobs.getState().jobs[job.id].progress).toBe(0.4);
  MockEventSource.instances[0].message({ ...job, progress: 0.6 });
  expect(useJobs.getState().jobs[job.id].progress).toBe(0.6);
  expect(MockEventSource.instances[0].close).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(30_000);
  expect(poll).toHaveBeenCalledTimes(2);
});

it("uses a single one-second fallback after SSE errors, without overlapping polls", async () => {
  const pending = deferred<Job>();
  const poll = vi.spyOn(api, "job").mockReturnValueOnce(pending.promise).mockResolvedValue(job);
  useJobs.getState().track(job);
  const es = MockEventSource.instances[0];
  es.onerror?.();
  es.onerror?.();
  useJobs.getState().track(job);
  expect(es.close).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(1);
  await vi.advanceTimersByTimeAsync(5_000);
  expect(poll).toHaveBeenCalledOnce();
  pending.resolve(job);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(poll).toHaveBeenCalledTimes(2);
});

it("polls safely when EventSource is unavailable", async () => {
  vi.stubGlobal("EventSource", undefined);
  const poll = vi.spyOn(api, "job").mockResolvedValue({ ...job, status: "completed" });
  useJobs.getState().track(job);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(poll).toHaveBeenCalledOnce();
  expect(useJobs.getState().jobs[job.id].status).toBe("completed");
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(poll).toHaveBeenCalledOnce();
});

it.each([true, false])("does not poll while hidden and resumes visible-tab observation (SSE: %s)", async (sse) => {
  if (!sse) vi.stubGlobal("EventSource", undefined);
  const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  const poll = vi.spyOn(api, "job").mockResolvedValue(job);
  useJobs.getState().track(job);
  await vi.advanceTimersByTimeAsync(120_000);
  expect(poll).not.toHaveBeenCalled();
  visibility.mockReturnValue("visible");
  await vi.advanceTimersByTimeAsync(sse ? 30_000 : 1_000);
  expect(poll).toHaveBeenCalledOnce();
});

it("a terminal resynchronization closes healthy SSE and stops all further requests", async () => {
  const poll = vi.spyOn(api, "job").mockResolvedValue({ ...job, status: "completed" });
  useJobs.getState().track(job);
  const es = MockEventSource.instances[0];
  await vi.advanceTimersByTimeAsync(30_000);
  expect(es.close).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(120_000);
  expect(poll).toHaveBeenCalledOnce();
});

it("fallback polling retries reported errors without changing the last known status", async () => {
  vi.stubGlobal("EventSource", undefined);
  const poll = vi.spyOn(api, "job").mockRejectedValueOnce(new Error("Offline")).mockResolvedValue({ ...job, status: "failed" });
  useJobs.getState().track(job);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(useJobs.getState().trackingErrors[job.id]).toContain("Offline");
  expect(useJobs.getState().jobs[job.id].status).toBe("running");
  await vi.advanceTimersByTimeAsync(1_000);
  expect(poll).toHaveBeenCalledTimes(2);
  expect(useJobs.getState().jobs[job.id].status).toBe("failed");
  expect(useJobs.getState().trackingErrors[job.id]).toBeUndefined();
});

it.each(["completed", "failed", "canceled", "interrupted"] as const)(
  "stops timers and ignores late callbacks after %s", async (status) => {
    const pending = deferred<Job>();
    const poll = vi.spyOn(api, "job").mockReturnValue(pending.promise);
    useJobs.getState().track(job);
    const es = MockEventSource.instances[0];
    await vi.advanceTimersByTimeAsync(30_000);
    es.message({ ...job, status });
    pending.resolve(job);
    await vi.advanceTimersByTimeAsync(0);
    es.onerror?.();
    es.message(job);
    useJobs.getState().track(job);
    expect(useJobs.getState().jobs[job.id].status).toBe(status);
    expect(MockEventSource.instances).toHaveLength(1);
    expect(es.close).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(poll).toHaveBeenCalledOnce();
  },
);

it("does not let an in-flight snapshot replace newer SSE progress", async () => {
  const pending = deferred<Job>();
  vi.spyOn(api, "job").mockReturnValue(pending.promise);
  useJobs.getState().track(job);
  await vi.advanceTimersByTimeAsync(30_000);
  MockEventSource.instances[0].message({ ...job, progress: 0.9 });
  pending.resolve({ ...job, progress: 0.3 });
  await vi.advanceTimersByTimeAsync(0);
  expect(useJobs.getState().jobs[job.id].progress).toBe(0.9);
});

it("surfaces polling errors without false completion and clears them after recovery", async () => {
  vi.spyOn(api, "job").mockRejectedValueOnce(new Error("Network unavailable")).mockResolvedValue(job);
  useJobs.getState().track(job);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(useJobs.getState().jobs[job.id].status).toBe("running");
  expect(useJobs.getState().trackingErrors[job.id]).toContain("Network unavailable");
  await vi.advanceTimersByTimeAsync(30_000);
  expect(useJobs.getState().trackingErrors[job.id]).toBeUndefined();
});

it("ignores a late poll rejection after a terminal SSE update", async () => {
  const pending = deferred<Job>();
  vi.spyOn(api, "job").mockReturnValue(pending.promise);
  useJobs.getState().track(job);
  await vi.advanceTimersByTimeAsync(30_000);
  MockEventSource.instances[0].message({ ...job, status: "completed" });
  pending.reject(new Error("Late failure"));
  await vi.advanceTimersByTimeAsync(0);
  expect(useJobs.getState().trackingErrors[job.id]).toBeUndefined();
  expect(useJobs.getState().jobs[job.id].status).toBe("completed");
});

it("creates no requests or timers when tracking an already terminal job", async () => {
  const poll = vi.spyOn(api, "job");
  useJobs.getState().track({ ...job, status: "interrupted" });
  await vi.advanceTimersByTimeAsync(120_000);
  expect(poll).not.toHaveBeenCalled();
  expect(MockEventSource.instances).toHaveLength(0);
});
