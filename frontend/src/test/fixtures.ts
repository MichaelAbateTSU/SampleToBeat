import type { Arrangement, Pattern, Track } from "../api/types";
import { TICKS_PER_BAR } from "../lib/music";

const track = (id: string, kind: Track["kind"], clips: Track["clips"] = []): Track => ({
  id, name: id, kind, color: "", volume_db: 0, pan: 0, muted: false, solo: false, choke: false, fx: {}, clips,
});

/** Small, valid arrangement: an 8-bar beat with one sample, one drum and one bass track. */
export function makeArrangement(): Arrangement {
  const patterns: Record<string, Pattern> = {
    pd: {
      id: "pd", kind: "drums", length_ticks: TICKS_PER_BAR, events: [
        { type: "drum", tick: 0, inst: "kick", vel: 1 },
        { type: "drum", tick: 960, inst: "kick", vel: 0.8 },
      ],
    },
    pb: { id: "pb", kind: "bass", length_ticks: TICKS_PER_BAR, events: [{ type: "note", tick: 0, dur: 480, midi: 36, vel: 0.9, slide: false }] },
    ps: {
      id: "ps", kind: "sample", length_ticks: 2 * TICKS_PER_BAR, events: [
        { type: "chop", tick: 0, dur: 960, sample_id: "s1", s0: 0, s1: 2, ratio: 1, pitch: 0, vel: 0.9 },
      ],
    },
  };
  return {
    schema_version: 1, bpm: 90, key: "A minor", genre: "boombap", kit: "boombap", swing: 50, bars: 8, seed: 1, strategy: "test",
    sections: [{ name: "intro", start_bar: 0, bars: 4 }, { name: "verse", start_bar: 4, bars: 4 }],
    tracks: [
      track("sample", "sample", [{ id: "c1", pattern_id: "ps", start_bar: 0, bars: 4 }]),
      track("kick", "drums", [{ id: "c2", pattern_id: "pd", start_bar: 0, bars: 2 }, { id: "c3", pattern_id: "pd", start_bar: 4, bars: 2 }]),
      track("bass", "bass", [{ id: "c4", pattern_id: "pb", start_bar: 0, bars: 8 }]),
    ],
    patterns, bass: {}, master: { target_lufs: -14 }, meta: {},
  };
}
