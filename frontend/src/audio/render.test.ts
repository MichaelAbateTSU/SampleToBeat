import { describe, expect, it } from "vitest";
import { makeArrangement } from "../test/fixtures";
import { kickTicks, render808, renderTrack, timeline } from "./render";
import { SR, TICKS_PER_BAR } from "../lib/music";

describe("offline renderer", () => {
  it("tiles patterns across clips", () => {
    const a = makeArrangement();
    const hits = timeline(a, a.tracks[1]);
    expect(hits.map((h) => h.tick)).toEqual([0, 960, 1920, 2880, 7680, 8640, 9600, 10560]);
    expect(timeline(a, a.tracks[1], TICKS_PER_BAR, 2 * TICKS_PER_BAR).map((h) => h.tick)).toEqual([1920, 2880]);
  });

  it("finds kick ticks relative to a window", () => {
    const a = makeArrangement();
    expect(kickTicks(a, 4 * TICKS_PER_BAR, 5 * TICKS_PER_BAR)).toEqual([0, 960]);
  });

  it("renders a finite, non-silent 808", () => {
    const y = render808([{ type: "note", tick: 0, dur: 480, midi: 36, vel: 0.9, slide: false }], 90, SR);
    expect(y.length).toBe(SR);
    const peak = y.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
    expect(peak).toBeGreaterThan(0.05);
    expect(y.every(Number.isFinite)).toBe(true);
  });

  it("renders a bass track to stereo of the song length", () => {
    const a = makeArrangement();
    const [l, r] = renderTrack(a, a.tracks[2], { kit: {}, samples: {}, buffers: new Map() } as never, 0);
    const songSamples = Math.round((a.bars * 4 * 60 / a.bpm) * SR);
    expect(Math.abs(l.length - songSamples)).toBeLessThan(SR / 10);
    expect(r.length).toBe(l.length);
  });
});
