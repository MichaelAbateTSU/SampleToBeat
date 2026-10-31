import { act, renderHook } from "@testing-library/react";
import { focusManager, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";
import { queryClient, useJobs } from "./hooks";
import { HEALTH_IDLE_MS, useHealth } from "./health";
import type { Job } from "./types";

const wrapper = ({ children }: { children: React.ReactNode }) =>
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;

async function tick(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

beforeEach(() => {
  vi.useFakeTimers();
  focusManager.setFocused(true);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  queryClient.clear();
  queryClient.setQueryData(["capabilities"], { hosted: true });
  useJobs.setState({ jobs: {} });
  vi.spyOn(api, "health").mockResolvedValue({ ok: true, engine: "2", version: "1" });
});

afterEach(() => {
  queryClient.clear();
  vi.restoreAllMocks();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

describe("hosted health activity", () => {
  it("stops visible idle traffic for more than Render's idle window", async () => {
    const hook = renderHook(useHealth, { wrapper });
    await tick(HEALTH_IDLE_MS + 100);
    expect(hook.result.current.paused).toBe(true);
    const count = vi.mocked(api.health).mock.calls.length;
    await tick(20 * 60_000);
    expect(api.health).toHaveBeenCalledTimes(count);
  });

  it.each(["pointerdown", "keydown", "wheel", "touchstart", "focus", "online"])("reconnects on %s and reconciles active queries", async (event) => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const hook = renderHook(useHealth, { wrapper });
    await tick(HEALTH_IDLE_MS + 100);
    const count = vi.mocked(api.health).mock.calls.length;
    act(() => window.dispatchEvent(new Event(event)));
    await tick(100);
    expect(hook.result.current.paused).toBe(false);
    expect(vi.mocked(api.health).mock.calls.length).toBeGreaterThan(count);
    expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ refetchType: "active" }));
    const resumed = vi.mocked(api.health).mock.calls.length;
    act(() => window.dispatchEvent(new Event("pointerdown")));
    await tick(100);
    expect(api.health).toHaveBeenCalledTimes(resumed);
  });

  it("pauses when hidden and reconnects on visible return despite disabled global focus refetch", async () => {
    const hook = renderHook(useHealth, { wrapper });
    await tick(100);
    act(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick(100);
    const count = vi.mocked(api.health).mock.calls.length;
    await tick(60_000);
    expect(api.health).toHaveBeenCalledTimes(count);
    act(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick(100);
    expect(hook.result.current.paused).toBe(false);
    expect(vi.mocked(api.health).mock.calls.length).toBeGreaterThan(count);
  });

  it("keeps active-job health observation, then pauses after completion", async () => {
    const job: Job = {
      id: "j", project_id: "p", job_type: "export", status: "running",
      progress: .2, current_stage: "rendering", error_message: null, result: null,
    };
    useJobs.setState({ jobs: { j: job } });
    const hook = renderHook(useHealth, { wrapper });
    await tick(HEALTH_IDLE_MS + 100);
    expect(hook.result.current.paused).toBe(false);
    const count = vi.mocked(api.health).mock.calls.length;
    await tick(20_000);
    expect(vi.mocked(api.health).mock.calls.length).toBeGreaterThan(count);
    act(() => useJobs.setState({ jobs: { j: { ...job, status: "completed" } } }));
    await tick(100);
    expect(hook.result.current.paused).toBe(true);
  });

  it("retains errors while idle and retries after real return", async () => {
    vi.mocked(api.health).mockRejectedValue(new Error("offline"));
    const hook = renderHook(useHealth, { wrapper });
    await tick(HEALTH_IDLE_MS + 100);
    expect(hook.result.current.isError).toBe(true);
    expect(hook.result.current.paused).toBe(true);
    vi.mocked(api.health).mockResolvedValue({ ok: true, engine: "2", version: "1" });
    act(() => window.dispatchEvent(new Event("keydown")));
    await tick(100);
    expect(hook.result.current.isError).toBe(false);
  });

  it("preserves local periodic polling after inactivity", async () => {
    queryClient.setQueryData(["capabilities"], { hosted: false });
    const hook = renderHook(useHealth, { wrapper });
    await tick(HEALTH_IDLE_MS + 100);
    const count = vi.mocked(api.health).mock.calls.length;
    await tick(20_000);
    expect(vi.mocked(api.health).mock.calls.length).toBeGreaterThan(count);
    expect(hook.result.current.paused).toBe(false);
  });

  it("automatically recovers an unknown-mode backend after more than a minute without activity", async () => {
    queryClient.removeQueries({ queryKey: ["capabilities"] });
    vi.spyOn(api, "capabilities").mockRejectedValue(new Error("offline"));
    vi.mocked(api.health).mockRejectedValue(new Error("offline"));
    const hook = renderHook(useHealth, { wrapper });
    await tick(90_100);
    expect(hook.result.current.isError).toBe(true);
    expect(hook.result.current.paused).toBe(false);
    const count = vi.mocked(api.health).mock.calls.length;
    vi.mocked(api.health).mockResolvedValue({ ok: true, engine: "2", version: "1" });
    await tick(10_000);
    expect(vi.mocked(api.health).mock.calls.length).toBeGreaterThan(count);
    expect(hook.result.current.isError).toBe(false);
  });

  it("does not promise background health keepalives for a hidden nonterminal job", async () => {
    const job: Job = {
      id: "j", project_id: "p", job_type: "export", status: "running",
      progress: .2, current_stage: "rendering", error_message: null, result: null,
    };
    useJobs.setState({ jobs: { j: job } });
    renderHook(useHealth, { wrapper });
    await tick(100);
    act(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
      focusManager.setFocused(false);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick(100);
    const count = vi.mocked(api.health).mock.calls.length;
    await tick(20 * 60_000);
    expect(api.health).toHaveBeenCalledTimes(count);
    act(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
      focusManager.setFocused(true);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick(100);
    expect(vi.mocked(api.health).mock.calls.length).toBeGreaterThan(count);
  });

  it("removes activity listeners and health observation on unmount", async () => {
    const hook = renderHook(useHealth, { wrapper });
    await tick(HEALTH_IDLE_MS + 100);
    hook.unmount();
    const count = vi.mocked(api.health).mock.calls.length;
    act(() => window.dispatchEvent(new Event("pointerdown")));
    await tick(HEALTH_IDLE_MS + 100);
    expect(api.health).toHaveBeenCalledTimes(count);
  });
});
