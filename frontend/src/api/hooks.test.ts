import { afterEach, expect, it, vi } from "vitest";
import { keys, queryClient, useJobs } from "./hooks";
import type { Job } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("refreshes queued retry records immediately and closes tracking when finished", () => {
  const close = vi.fn();
  vi.stubGlobal("EventSource", class { close = close; });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
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
