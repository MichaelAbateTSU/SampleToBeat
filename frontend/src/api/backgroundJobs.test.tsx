import { act, renderHook } from "@testing-library/react";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { queryClient, useJobs } from "./hooks";
import type { Job } from "./types";
import { BACKGROUND_JOB_MS, useBackgroundJobs } from "./backgroundJobs";

const job: Job = {
  id: "j", project_id: "p", job_type: "export", status: "running",
  progress: .2, current_stage: "rendering", error_message: null, result: null,
};
async function tick(ms = BACKGROUND_JOB_MS) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}
function visible(value: boolean) {
  act(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: value ? "visible" : "hidden" });
    focusManager.setFocused(value);
    document.dispatchEvent(new Event("visibilitychange"));
  });
}
beforeEach(() => {
  vi.useFakeTimers();
  visible(false);
  vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
  useJobs.setState({ jobs: {}, trackingErrors: {} });
});
afterEach(() => {
  vi.restoreAllMocks();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

it("has no recurring requests without active jobs and preserves local behavior", async () => {
  const request = vi.spyOn(api, "job").mockResolvedValue(job);
  const hook = renderHook(() => useBackgroundJobs(true));
  await tick(10 * BACKGROUND_JOB_MS);
  expect(request).not.toHaveBeenCalled();
  hook.unmount();
  useJobs.setState({ jobs: { j: job } });
  renderHook(() => useBackgroundJobs(false));
  await tick(10 * BACKGROUND_JOB_MS);
  expect(request).not.toHaveBeenCalled();
});

it("observes hidden jobs independently and stops after terminal SSE/store updates", async () => {
  const other = { ...job, id: "k" };
  useJobs.setState({ jobs: { j: job, k: other } });
  const request = vi.spyOn(api, "job").mockImplementation(async (id) => id === "j" ? { ...job, status: "completed" } : other);
  renderHook(() => useBackgroundJobs(true));
  await tick();
  expect(request).toHaveBeenCalledTimes(2);
  expect(useJobs.getState().jobs.j.status).toBe("completed");
  await tick();
  expect(request).toHaveBeenCalledTimes(3);
  act(() => useJobs.getState().update({ ...other, status: "completed" }));
  await tick(20 * BACKGROUND_JOB_MS);
  expect(request).toHaveBeenCalledTimes(3);
});

it("pauses a failed job after three attempts, surfaces errors and retries on return", async () => {
  useJobs.setState({ jobs: { j: job } });
  const request = vi.spyOn(api, "job").mockRejectedValue(new Error("offline"));
  const hook = renderHook(() => useBackgroundJobs(true));
  await tick(3 * BACKGROUND_JOB_MS);
  expect(request).toHaveBeenCalledTimes(3);
  expect(hook.result.current).toContain("paused after repeated failures");
  await tick(20 * BACKGROUND_JOB_MS);
  expect(request).toHaveBeenCalledTimes(3);
  request.mockResolvedValue({ ...job, status: "completed" });
  visible(true);
  await tick(100);
  expect(request).toHaveBeenCalledTimes(4);
  expect(hook.result.current).toBe("");
});

it("does not stop a healthy job when another exhausts its failure budget", async () => {
  const other = { ...job, id: "k" };
  useJobs.setState({ jobs: { j: job, k: other } });
  const request = vi.spyOn(api, "job").mockImplementation(async (id) => {
    if (id === "j") throw new Error("lost job");
    return other;
  });
  renderHook(() => useBackgroundJobs(true));
  await tick(5 * BACKGROUND_JOB_MS);
  expect(request.mock.calls.filter(([id]) => id === "j")).toHaveLength(3);
  expect(request.mock.calls.filter(([id]) => id === "k")).toHaveLength(5);
});

it("does not overlap requests or overwrite newer SSE progress and cleans up pending results", async () => {
  useJobs.setState({ jobs: { j: job } });
  let resolve!: (value: Job) => void;
  const request = vi.spyOn(api, "job").mockImplementation(() => new Promise<Job>((yes) => { resolve = yes; }));
  const hook = renderHook(() => useBackgroundJobs(true));
  await tick(3 * BACKGROUND_JOB_MS);
  expect(request).toHaveBeenCalledTimes(1);
  act(() => useJobs.getState().update({ ...job, progress: .8 }));
  await act(async () => { resolve(job); });
  expect(useJobs.getState().jobs.j.progress).toBe(.8);
  await tick();
  expect(request).toHaveBeenCalledTimes(2);
  hook.unmount();
  await act(async () => { resolve({ ...job, status: "completed" }); });
  visible(true);
  await tick(10 * BACKGROUND_JOB_MS);
  expect(request).toHaveBeenCalledTimes(2);
  expect(useJobs.getState().jobs.j.status).toBe("running");
});
