import { describe, expect, it } from "vitest";
import { makeArrangement } from "../test/fixtures";
import {
  addClip, addTrack, deleteClip, duplicateClip, findClip, makeUnique, moveClip, patternUsage, removeTrack, resizeClip, setBars,
  setPatternLength, splitClip, toggleDrumStep, toggleMute, transposePattern, validate,
} from "./arrangement";
import { TICKS_PER_BAR } from "./music";

describe("arrangement editing", () => {
  it("starts valid and edits are immutable", () => {
    const a = makeArrangement();
    expect(validate(a)).toEqual([]);
    const snapshot = JSON.stringify(a);
    const b = toggleMute(a, "kick");
    expect(b.tracks[1].muted).toBe(true);
    expect(JSON.stringify(a)).toBe(snapshot);
  });

  it("moves clips and refuses overlaps", () => {
    const a = makeArrangement();
    const b = moveClip(a, "c2", 2);
    expect(findClip(b, "c2")!.clip.start_bar).toBe(2);
    expect(() => moveClip(a, "c2", 3)).toThrow();
    expect(validate(b)).toEqual([]);
  });

  it("moves a clip to another track of the same kind only", () => {
    let a = addTrack(makeArrangement(), "drums", "drums 2");
    const t2 = a.tracks[a.tracks.length - 1];
    a = moveClip(a, "c2", 0, t2.id);
    expect(findClip(a, "c2")!.track.id).toBe(t2.id);
    expect(() => moveClip(a, "c2", 0, "bass")).toThrow();
  });

  it("resizes, duplicates and deletes", () => {
    const a = makeArrangement();
    expect(findClip(resizeClip(a, "c2", 3), "c2")!.clip.bars).toBe(3);
    const d = duplicateClip(a, "c2");
    const kick = d.tracks.find((t) => t.id === "kick")!;
    expect(kick.clips).toHaveLength(3);
    expect(kick.clips.map((c) => c.start_bar)).toEqual([0, 2, 4]);
    expect(findClip(deleteClip(a, "c2"), "c2")).toBeNull();
  });

  it("splits mid-pattern with a rotated copy so playback is unchanged", () => {
    const a = makeArrangement();
    const b = splitClip(a, "c1", 1); // pattern ps is 2 bars, so the right half starts halfway through
    const s = b.tracks[0].clips;
    expect(s.map((c) => [c.start_bar, c.bars])).toEqual([[0, 1], [1, 3]]);
    expect(s[1].pattern_id).not.toBe("ps");
    expect(b.patterns[s[1].pattern_id].events[0].tick).toBe(TICKS_PER_BAR);
    expect(() => splitClip(a, "c1", 0)).toThrow();
  });

  it("make unique copies a shared pattern", () => {
    const a = makeArrangement();
    expect(patternUsage(a, "pd")).toBe(2);
    const b = makeUnique(a, "c3");
    expect(findClip(b, "c3")!.clip.pattern_id).not.toBe("pd");
    expect(patternUsage(b, "pd")).toBe(1);
  });

  it("adds empty clips and toggles drum steps", () => {
    let a = addClip(makeArrangement(), "kick", 6, 2);
    const clip = a.tracks[1].clips.find((c) => c.start_bar === 6)!;
    a = toggleDrumStep(a, clip.pattern_id, "kick", 4);
    expect(a.patterns[clip.pattern_id].events).toMatchObject([{ inst: "kick", tick: 480 }]);
    a = toggleDrumStep(a, clip.pattern_id, "kick", 4);
    expect(a.patterns[clip.pattern_id].events).toHaveLength(0);
    expect(() => addClip(a, "kick", 0, 1)).toThrow(/taken/);
  });

  it("shortening a pattern drops events past the end; transposing moves notes", () => {
    const a = setPatternLength(makeArrangement(), "pd", 480);
    expect(a.patterns.pd.events).toHaveLength(1);
    const t = transposePattern(makeArrangement(), "pb", 12);
    expect(t.patterns.pb.events[0]).toMatchObject({ midi: 48 });
  });

  it("validation catches clips past the end and tracks can be removed", () => {
    const a = makeArrangement();
    expect(validate({ ...a, bars: 4 }).some((e) => /past the end/.test(e))).toBe(true);
    const short = setBars(a, 5); // trims clips/sections rather than leaving them past the end
    expect(validate(short)).toEqual([]);
    expect(findClip(short, "c3")!.clip.bars).toBe(1);
    expect(setBars(a, 0).bars).toBe(1);
    expect(removeTrack(a, "bass").tracks.map((t) => t.id)).toEqual(["sample", "kick"]);
  });
});
