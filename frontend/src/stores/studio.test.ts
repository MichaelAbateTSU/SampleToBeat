import { beforeEach, describe, expect, it } from "vitest";
import { makeArrangement } from "../test/fixtures";
import { moveClip, toggleMute } from "../lib/arrangement";
import { useStudio } from "./studio";

describe("studio store", () => {
  beforeEach(() => useStudio.getState().load("p1", makeArrangement()));

  it("applies edits with undo and redo", () => {
    const st = useStudio.getState;
    expect(st().apply("Mute", (a) => toggleMute(a, "kick"))).toBe(true);
    expect(st().arr!.tracks[1].muted).toBe(true);
    expect(st().save).toBe("dirty");
    st().undo();
    expect(st().arr!.tracks[1].muted).toBe(false);
    st().redo();
    expect(st().arr!.tracks[1].muted).toBe(true);
  });

  it("rejects failing edits without touching history", () => {
    const st = useStudio.getState;
    const before = st().arr;
    expect(st().apply("Move", (a) => moveClip(a, "c2", 3))).toBe(false);
    expect(st().arr).toBe(before);
    expect(st().past).toHaveLength(0);
    expect(st().lastError).toBeTruthy();
  });

  it("rejects edits that produce an invalid document", () => {
    const st = useStudio.getState;
    expect(st().apply("Bad", (a) => ({ ...a, bpm: 500 }))).toBe(false);
    expect(st().arr!.bpm).toBe(90);
  });
});
