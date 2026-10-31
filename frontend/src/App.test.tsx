import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api/client";
import App from "./App";

vi.mock("./api/client", () => ({ api: { health: vi.fn() } }));
vi.mock("./api/hooks", () => ({ useCapabilities: () => ({ data: undefined }) }));
vi.mock("./components/Audio", () => ({ NowPlaying: () => null }));
vi.mock("./pages/Home", () => ({ default: () => null }));
vi.mock("./pages/Project", () => ({ default: () => null }));
vi.mock("./pages/Settings", () => ({ default: () => null }));
vi.mock("./router", () => ({ useRoute: () => [], navigate: vi.fn() }));

const client = new QueryClient();

afterEach(() => {
  cleanup();
  client.clear();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

it("shows hosting-neutral outage guidance and automatically reconnects", async () => {
  vi.useFakeTimers();
  focusManager.setFocused(true);
  vi.mocked(api.health).mockRejectedValue(new Error("Service unavailable"));
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByRole("alert")).toHaveTextContent("starting or temporarily unavailable");
  expect(screen.getByRole("alert")).toHaveTextContent("This page reconnects automatically");
  expect(screen.getByRole("alert")).not.toHaveTextContent("start script");
  expect(api.health).toHaveBeenCalledTimes(1);
  vi.mocked(api.health).mockResolvedValue({ ok: true, version: "test", engine: "test" });
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(api.health).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
