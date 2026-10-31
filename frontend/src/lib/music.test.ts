import { describe, expect, it } from "vitest";
import { formatBarBeat, formatTime, parseKey, pyRound, scalePitchClasses, secondsToTicks, swingOffsetTicks, ticksToSeconds } from "./music";

describe("music helpers", () => {
  it("rounds half to even like Python", () => {
    expect([0.5, 1.5, 2.5, -1.5, 2.4, 2.6].map(pyRound)).toEqual([0, 2, 2, -2, 2, 3]);
  });

  it("swings only off-beat 16ths, matching the backend", () => {
    expect(swingOffsetTicks(120, 50)).toBe(0);
    expect(swingOffsetTicks(120, 66)).toBe(38);
    expect(swingOffsetTicks(0, 66)).toBe(0);
    expect(swingOffsetTicks(240, 66)).toBe(0);
    expect(swingOffsetTicks(360, 75)).toBe(60);
  });

  it("converts ticks and seconds", () => {
    expect(ticksToSeconds(480, 120)).toBeCloseTo(0.5);
    expect(secondsToTicks(0.5, 120)).toBeCloseTo(480);
  });

  it("formats positions", () => {
    expect(formatBarBeat(0)).toBe("1.1");
    expect(formatBarBeat(1920 + 480)).toBe("2.2");
    expect(formatTime(75.25)).toBe("1:15.3");
  });

  it("parses keys and scales", () => {
    expect(parseKey("A minor")).toEqual({ tonic: 9, mode: "minor" });
    expect(parseKey("Bb major")).toEqual({ tonic: 10, mode: "major" });
    expect(parseKey("nonsense")).toBeNull();
    expect([...scalePitchClasses("C major")!].sort((a, b) => a - b)).toEqual([0, 2, 4, 5, 7, 9, 11]);
  });
});
