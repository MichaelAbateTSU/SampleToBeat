import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Waveform } from "./Waveform";

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  stop: vi.fn(),
  regions: vi.fn(),
}));
vi.mock("wavesurfer.js", () => ({ default: { create: mocks.create } }));
vi.mock("wavesurfer.js/dist/plugins/regions.esm.js", () => ({ default: { create: mocks.regions } }));
vi.mock("../audio/player", () => ({ usePreview: { getState: () => ({ stop: mocks.stop }) } }));

describe("Waveform media ownership", () => {
  const listeners = new Map<string, (...args: unknown[]) => void>();
  const destroy = vi.fn();
  const playPause = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    listeners.clear();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    mocks.regions.mockReturnValue({ getRegions: () => [], on: vi.fn(), enableDragSelection: vi.fn() });
    mocks.create.mockImplementation((options: { media: HTMLMediaElement; peaks?: number[][]; duration?: number }) => {
      // This callback is the boundary before WaveSurfer can assign/load the URL.
      expect(options.media.preload).toBe(options.peaks && options.duration ? "none" : "metadata");
      expect(options.media.getAttribute("src")).toBeNull();
      return {
        on: (event: string, callback: (...args: unknown[]) => void) => listeners.set(event, callback),
        destroy,
        playPause,
        getDuration: () => 60,
      };
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("passes peaks/duration with preload none before loading, then retains ready/play controls", () => {
    const peaks = [0, 0.5, -0.5];
    render(<Waveform url="/source.wav" peaks={peaks} duration={60} />);
    const options = mocks.create.mock.calls[0][0];
    expect(options).toMatchObject({ url: "/source.wav", peaks: [peaks], duration: 60 });
    expect(screen.getByRole("button", { name: "Play source" })).toBeDisabled();
    act(() => listeners.get("ready")?.());
    fireEvent.click(screen.getByRole("button", { name: "Play source" }));
    expect(playPause).toHaveBeenCalledOnce();
    act(() => listeners.get("play")?.());
    expect(screen.getByRole("button", { name: "Pause source" })).toBeEnabled();
    expect(mocks.stop).toHaveBeenCalledOnce();
    act(() => listeners.get("pause")?.());
    expect(screen.getByRole("button", { name: "Play source" })).toBeEnabled();
  });

  it.each([undefined, []])("preserves the URL decoding fallback when peaks are %s", (peaks) => {
    render(<Waveform url="/source.wav" peaks={peaks} duration={60} />);
    expect(mocks.create.mock.calls[0][0]).toMatchObject({
      url: "/source.wav", peaks: undefined, duration: undefined,
    });
  });

  it("retains metadata loading when peaks have no duration so ready can still fire", () => {
    render(<Waveform url="/source.wav" peaks={[0, 1]} />);
    expect(mocks.create.mock.calls[0][0]).toMatchObject({ duration: undefined });
    expect(mocks.create.mock.calls[0][0].media.preload).toBe("metadata");
  });

  it("stops and resets owned media on unmount and creates a fresh unloaded element on remount", () => {
    const first = render(<Waveform url="/source.wav" peaks={[0, 1]} duration={60} />);
    const media = mocks.create.mock.calls[0][0].media as HTMLMediaElement;
    media.src = "/source.wav";
    first.unmount();
    expect(destroy).toHaveBeenCalledOnce();
    expect(media.pause).toHaveBeenCalledOnce();
    expect(media.load).toHaveBeenCalledOnce();
    expect(media.getAttribute("src")).toBeNull();
    render(<Waveform url="/source.wav" peaks={[0, 1]} duration={60} />);
    const remounted = mocks.create.mock.calls[1][0].media as HTMLMediaElement;
    expect(remounted).not.toBe(media);
    expect(remounted.preload).toBe("none");
  });
});
