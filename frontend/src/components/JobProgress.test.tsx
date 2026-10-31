import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { useJobs } from "../api/hooks";
import type { Job } from "../api/types";
import { JobRow } from "./JobProgress";

const job: Job = {
  id: "stopped", project_id: "p", job_type: "analyze", status: "interrupted",
  progress: 0.3, current_stage: "interrupted", result: null,
  error_message: "Application stopped before this job finished. Retry to run it again; saved results were retained.",
};

function show(value = job) {
  return render(<QueryClientProvider client={new QueryClient()}>
    <JobRow job={value} />
  </QueryClientProvider>);
}

beforeEach(() => vi.restoreAllMocks());

it("explains interruption without showing active progress", () => {
  show();
  expect(screen.getByText(job.error_message!)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
});

it("shows retry rejection instead of an unhandled promise failure", async () => {
  vi.spyOn(api, "retryJob").mockRejectedValue(new Error("Newer work owns this source. Retry its latest job instead."));
  show();
  fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
  expect(await screen.findByText(/Newer work owns/)).toBeInTheDocument();
});

it("tracks an explicitly requested retry", async () => {
  const retry = { ...job, id: "retry", status: "completed" as const };
  vi.spyOn(api, "retryJob").mockResolvedValue(retry);
  const track = vi.fn();
  useJobs.setState({ track });
  show();
  fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
  await waitFor(() => expect(track.mock.calls[0]?.[0]).toEqual(retry));
});

it("provides feedback for interrupted legacy jobs without an error message", () => {
  show({ ...job, error_message: null });
  expect(screen.getByText(/Application stopped before/)).toBeInTheDocument();
});
