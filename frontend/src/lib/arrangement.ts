// Pure, immutable edit operations on an Arrangement. Each returns a new arrangement (or throws on invalid
// input) so the studio store can keep undo/redo snapshots. Mirrors the validation in schemas.Arrangement.

import type { Arrangement, ArrEvent, ChopEvent, Clip, DrumEvent, NoteEvent, Pattern, Section, Track, TrackKind } from "../api/types";
import { STEP_TICKS, TICKS_PER_BAR, clamp } from "./music";

export const KIND_EVENT: Record<TrackKind, ArrEvent["type"]> = { sample: "chop", drums: "drum", bass: "note" };
export const TRACK_COLORS: Record<TrackKind, string> = { sample: "#f59e0b", drums: "#38bdf8", bass: "#a78bfa" };

let counter = 0;
export function newId(prefix: string): string {
  counter = (counter + 1) % 1e6;
  return `${prefix}_${Date.now().toString(36)}${counter.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

const clone = <T,>(x: T): T => structuredClone(x);

export function findClip(arr: Arrangement, clipId: string): { track: Track; clip: Clip } | null {
  for (const track of arr.tracks) {
    const clip = track.clips.find((c) => c.id === clipId);
    if (clip) return { track, clip };
  }
  return null;
}

function withClip(arr: Arrangement, clipId: string, fn: (a: Arrangement, t: Track, c: Clip) => void): Arrangement {
  const a = clone(arr);
  const hit = findClip(a, clipId);
  if (!hit) throw new Error(`No clip ${clipId}`);
  fn(a, hit.track, hit.clip);
  return a;
}

function withTrack(arr: Arrangement, trackId: string, fn: (t: Track, a: Arrangement) => void): Arrangement {
  const a = clone(arr);
  const t = a.tracks.find((x) => x.id === trackId);
  if (!t) throw new Error(`No track ${trackId}`);
  fn(t, a);
  return a;
}

function withPattern(arr: Arrangement, patternId: string, fn: (p: Pattern) => void): Arrangement {
  const a = clone(arr);
  const p = a.patterns[patternId];
  if (!p) throw new Error(`No pattern ${patternId}`);
  fn(p);
  p.events.sort((x, y) => x.tick - y.tick);
  return a;
}

const overlaps = (c: Clip, start: number, bars: number) => c.start_bar < start + bars && start < c.start_bar + c.bars;

/** True if [start, start+bars) is free on the track (ignoring `exceptId`) and inside the arrangement. */
export function canPlace(arr: Arrangement, track: Track, start: number, bars: number, exceptId?: string): boolean {
  if (start < 0 || bars < 1 || start + bars > arr.bars) return false;
  return !track.clips.some((c) => c.id !== exceptId && overlaps(c, start, bars));
}

// ---------------------------------------------------------------- clips

export function moveClip(arr: Arrangement, clipId: string, startBar: number, toTrackId?: string): Arrangement {
  return withClip(arr, clipId, (a, track, clip) => {
    const target = toTrackId ? a.tracks.find((t) => t.id === toTrackId) : track;
    if (!target) throw new Error("No such track");
    if (target.kind !== track.kind) throw new Error(`Can't move a ${track.kind} clip onto a ${target.kind} track`);
    const start = clamp(Math.round(startBar), 0, a.bars - clip.bars);
    if (!canPlace(a, target, start, clip.bars, clip.id)) throw new Error("That spot is taken");
    clip.start_bar = start;
    if (target !== track) {
      track.clips = track.clips.filter((c) => c.id !== clip.id);
      target.clips.push(clip);
    }
    target.clips.sort((x, y) => x.start_bar - y.start_bar);
  });
}

export function resizeClip(arr: Arrangement, clipId: string, bars: number): Arrangement {
  return withClip(arr, clipId, (a, track, clip) => {
    const b = Math.max(1, Math.round(bars));
    if (!canPlace(a, track, clip.start_bar, b, clip.id)) throw new Error("Clip would overlap another clip or the end");
    clip.bars = b;
  });
}

/** Split at an absolute bar: [start, at) keeps the pattern; [at, end) continues it with the pattern offset kept. */
export function splitClip(arr: Arrangement, clipId: string, atBar: number): Arrangement {
  return withClip(arr, clipId, (a, track, clip) => {
    const at = Math.round(atBar);
    if (at <= clip.start_bar || at >= clip.start_bar + clip.bars) throw new Error("Split point must be inside the clip");
    const p = a.patterns[clip.pattern_id];
    const offsetTicks = ((at - clip.start_bar) * TICKS_PER_BAR) % p.length_ticks;
    let patternId = clip.pattern_id;
    if (offsetTicks) {
      // the right half starts mid-pattern: rotate a copy so it sounds identical after the split
      const rot = clone(p);
      rot.id = newId("pat");
      rot.events = p.events.map((e) => ({ ...e, tick: (e.tick - offsetTicks + p.length_ticks) % p.length_ticks }));
      rot.events.sort((x, y) => x.tick - y.tick);
      a.patterns[rot.id] = rot;
      patternId = rot.id;
    }
    const right: Clip = { id: newId("clip"), pattern_id: patternId, start_bar: at, bars: clip.start_bar + clip.bars - at };
    clip.bars = at - clip.start_bar;
    track.clips.push(right);
    track.clips.sort((x, y) => x.start_bar - y.start_bar);
  });
}

/** Copy the clip to the first free slot after it (same pattern: edits stay linked until made unique). */
export function duplicateClip(arr: Arrangement, clipId: string): Arrangement {
  return withClip(arr, clipId, (a, track, clip) => {
    for (let s = clip.start_bar + clip.bars; s + clip.bars <= a.bars; s++) {
      if (canPlace(a, track, s, clip.bars)) {
        track.clips.push({ ...clip, id: newId("clip"), start_bar: s });
        track.clips.sort((x, y) => x.start_bar - y.start_bar);
        return;
      }
    }
    throw new Error("No room after this clip; extend the arrangement first");
  });
}

export function deleteClip(arr: Arrangement, clipId: string): Arrangement {
  return gcPatterns(withClip(arr, clipId, (_a, track, clip) => {
    track.clips = track.clips.filter((c) => c.id !== clip.id);
  }));
}

export function addClip(arr: Arrangement, trackId: string, startBar: number, bars: number, patternId?: string): Arrangement {
  return withTrack(arr, trackId, (t, a) => {
    if (!canPlace(a, t, startBar, bars)) throw new Error("That spot is taken");
    let pid = patternId;
    if (!pid) {
      const p: Pattern = { id: newId("pat"), kind: t.kind, length_ticks: Math.min(bars, 4) * TICKS_PER_BAR, events: [] };
      a.patterns[p.id] = p;
      pid = p.id;
    } else if (a.patterns[pid]?.kind !== t.kind) throw new Error("Pattern kind does not match the track");
    t.clips.push({ id: newId("clip"), pattern_id: pid, start_bar: startBar, bars });
    t.clips.sort((x, y) => x.start_bar - y.start_bar);
  });
}

/** Give a clip its own copy of the pattern so editing it doesn't change other clips. */
export function makeUnique(arr: Arrangement, clipId: string): Arrangement {
  return withClip(arr, clipId, (a, _t, clip) => {
    const p = clone(a.patterns[clip.pattern_id]);
    p.id = newId("pat");
    a.patterns[p.id] = p;
    clip.pattern_id = p.id;
  });
}

export function patternUsage(arr: Arrangement, patternId: string): number {
  return arr.tracks.reduce((n, t) => n + t.clips.filter((c) => c.pattern_id === patternId).length, 0);
}

/** Drop patterns no clip references. */
export function gcPatterns(arr: Arrangement): Arrangement {
  const used = new Set(arr.tracks.flatMap((t) => t.clips.map((c) => c.pattern_id)));
  const patterns = Object.fromEntries(Object.entries(arr.patterns).filter(([id]) => used.has(id)));
  return Object.keys(patterns).length === Object.keys(arr.patterns).length ? arr : { ...arr, patterns };
}

// ---------------------------------------------------------------- tracks

export function updateTrack(arr: Arrangement, trackId: string, patch: Partial<Omit<Track, "id" | "kind" | "clips">>): Arrangement {
  return withTrack(arr, trackId, (t) => Object.assign(t, patch));
}

export function toggleMute(arr: Arrangement, trackId: string): Arrangement {
  return withTrack(arr, trackId, (t) => void (t.muted = !t.muted));
}

export function toggleSolo(arr: Arrangement, trackId: string): Arrangement {
  return withTrack(arr, trackId, (t) => void (t.solo = !t.solo));
}

export function addTrack(arr: Arrangement, kind: TrackKind, name?: string): Arrangement {
  if (arr.tracks.length >= 32) throw new Error("A project can have at most 32 tracks");
  const a = clone(arr);
  const n = a.tracks.filter((t) => t.kind === kind).length + 1;
  a.tracks.push({
    id: newId(kind), name: name ?? `${{ sample: "Sample", drums: "Drums", bass: "808" }[kind]} ${n}`, kind,
    color: TRACK_COLORS[kind], volume_db: 0, pan: 0, muted: false, solo: false, choke: kind === "sample", fx: {}, clips: [],
  });
  return a;
}

export function removeTrack(arr: Arrangement, trackId: string): Arrangement {
  return gcPatterns({ ...clone(arr), tracks: arr.tracks.filter((t) => t.id !== trackId).map(clone) });
}

/** Track ids sounding right now: unmuted, and soloed if anything is soloed. */
export function audibleTracks(arr: Arrangement): Set<string> {
  const solo = arr.tracks.some((t) => t.solo);
  return new Set(arr.tracks.filter((t) => !t.muted && (!solo || t.solo)).map((t) => t.id));
}

// ---------------------------------------------------------------- global settings

export function setBars(arr: Arrangement, bars: number): Arrangement {
  const b = clamp(Math.round(bars), 1, 256);
  const a = clone(arr);
  a.bars = b;
  for (const t of a.tracks) {
    t.clips = t.clips.filter((c) => c.start_bar < b).map((c) => ({ ...c, bars: Math.min(c.bars, b - c.start_bar) }));
  }
  a.sections = a.sections
    .filter((s) => s.start_bar < b)
    .map((s) => ({ ...s, bars: Math.min(s.bars, b - s.start_bar) }));
  return gcPatterns(a);
}

export function setGlobal(arr: Arrangement, patch: Partial<Pick<Arrangement, "bpm" | "swing" | "key" | "kit" | "genre">>): Arrangement {
  const a = { ...clone(arr), ...patch };
  a.bpm = clamp(a.bpm, 40, 220);
  a.swing = clamp(a.swing, 50, 75);
  return a;
}

export function setSections(arr: Arrangement, sections: Section[]): Arrangement {
  const s = sections
    .map((x) => ({ ...x, start_bar: clamp(Math.round(x.start_bar), 0, arr.bars - 1) }))
    .map((x) => ({ ...x, bars: clamp(Math.round(x.bars), 1, arr.bars - x.start_bar) }))
    .sort((x, y) => x.start_bar - y.start_bar);
  return { ...clone(arr), sections: s };
}

// ---------------------------------------------------------------- pattern events

export function setPatternLength(arr: Arrangement, patternId: string, lengthTicks: number): Arrangement {
  const len = clamp(Math.round(lengthTicks / STEP_TICKS) * STEP_TICKS, STEP_TICKS, 64 * TICKS_PER_BAR);
  return withPattern(arr, patternId, (p) => {
    p.length_ticks = len;
    p.events = p.events.filter((e) => e.tick < len);
  });
}

export function replaceEvents(arr: Arrangement, patternId: string, events: ArrEvent[]): Arrangement {
  return withPattern(arr, patternId, (p) => {
    const type = KIND_EVENT[p.kind];
    if (events.some((e) => e.type !== type)) throw new Error(`Only ${type} events belong in a ${p.kind} pattern`);
    p.events = clone(events).filter((e) => e.tick >= 0 && e.tick < p.length_ticks);
  });
}

/** Drum step at a 16th: if a hit of `inst` sits within the step, remove it; else add one. */
export function toggleDrumStep(arr: Arrangement, patternId: string, inst: string, step: number, vel = 0.9): Arrangement {
  return withPattern(arr, patternId, (p) => {
    const t0 = step * STEP_TICKS;
    const hit = (e: ArrEvent) => e.type === "drum" && e.inst === inst && e.tick >= t0 && e.tick < t0 + STEP_TICKS;
    if (p.events.some(hit)) p.events = p.events.filter((e) => !hit(e));
    else if (t0 < p.length_ticks) p.events.push({ type: "drum", tick: t0, inst, vel } satisfies DrumEvent);
  });
}

export function setDrumVelocity(arr: Arrangement, patternId: string, inst: string, step: number, vel: number): Arrangement {
  return withPattern(arr, patternId, (p) => {
    const t0 = step * STEP_TICKS;
    for (const e of p.events) if (e.type === "drum" && e.inst === inst && e.tick >= t0 && e.tick < t0 + STEP_TICKS) e.vel = clamp(vel, 0.05, 1.5);
  });
}

export function addNote(arr: Arrangement, patternId: string, note: Omit<NoteEvent, "type">): Arrangement {
  return withPattern(arr, patternId, (p) => {
    if (note.tick < 0 || note.tick >= p.length_ticks) throw new Error("Note outside the pattern");
    p.events.push({ type: "note", ...note, midi: clamp(note.midi, 12, 96), dur: Math.max(1, note.dur) });
  });
}

export function updateEvent(arr: Arrangement, patternId: string, index: number, patch: Partial<ArrEvent>): Arrangement {
  return withPattern(arr, patternId, (p) => {
    const e = p.events[index];
    if (!e) throw new Error("No such event");
    const next = { ...e, ...patch } as ArrEvent;
    if (next.tick < 0 || next.tick >= p.length_ticks) throw new Error("Event outside the pattern");
    if (next.type === "note") next.midi = clamp(next.midi, 12, 96);
    if ("dur" in next && next.dur !== undefined) next.dur = Math.max(1, Math.round(next.dur));
    if (next.type === "chop" && next.s1 <= next.s0) throw new Error("Chop end must be after its start");
    p.events[index] = next;
  });
}

export function removeEvent(arr: Arrangement, patternId: string, index: number): Arrangement {
  return withPattern(arr, patternId, (p) => {
    p.events = p.events.filter((_, i) => i !== index);
  });
}

/** Place a chop (a sample slice in beats) at a tick; used by pads and drag-and-drop. */
export function addChop(arr: Arrangement, patternId: string, chop: Omit<ChopEvent, "type">): Arrangement {
  return withPattern(arr, patternId, (p) => {
    if (p.kind !== "sample") throw new Error("Chops go on sample tracks");
    if (chop.tick < 0 || chop.tick >= p.length_ticks) throw new Error("Chop outside the pattern");
    if (chop.s1 <= chop.s0) throw new Error("Chop end must be after its start");
    p.events.push({ type: "chop", ...chop });
  });
}

/** Transpose (semitones) every note/chop in a pattern; notes are clamped to the 808 range. */
export function transposePattern(arr: Arrangement, patternId: string, semis: number): Arrangement {
  return withPattern(arr, patternId, (p) => {
    for (const e of p.events) {
      if (e.type === "note") e.midi = clamp(e.midi + semis, 12, 96);
      if (e.type === "chop") e.pitch = clamp(e.pitch + semis, -24, 24);
    }
  });
}

/** Sample ids used by chop events. */
export function referencedSamples(arr: Arrangement): Set<string> {
  const out = new Set<string>();
  for (const p of Object.values(arr.patterns)) for (const e of p.events) if (e.type === "chop") out.add(e.sample_id);
  return out;
}

// ---------------------------------------------------------------- validation (mirrors schemas.Arrangement)

export function validate(arr: Arrangement): string[] {
  const errs: string[] = [];
  if (!(arr.bpm >= 40 && arr.bpm <= 220)) errs.push("BPM must be 40-220");
  if (!(arr.bars >= 1 && arr.bars <= 256)) errs.push("Length must be 1-256 bars");
  if (arr.tracks.length > 32) errs.push("At most 32 tracks");
  const tids = arr.tracks.map((t) => t.id);
  if (new Set(tids).size !== tids.length) errs.push("Track ids must be unique");
  const cids = arr.tracks.flatMap((t) => t.clips.map((c) => c.id));
  if (new Set(cids).size !== cids.length) errs.push("Clip ids must be unique");
  for (const [pid, p] of Object.entries(arr.patterns)) {
    if (p.id !== pid) errs.push(`Pattern key ${pid} does not match its id`);
    if (p.length_ticks % STEP_TICKS) errs.push(`Pattern ${pid}: length must be whole 16ths`);
    for (const e of p.events) {
      if (e.type !== KIND_EVENT[p.kind]) errs.push(`Pattern ${pid}: ${e.type} event in a ${p.kind} pattern`);
      if (e.tick < 0 || e.tick >= p.length_ticks) errs.push(`Pattern ${pid}: event at tick ${e.tick} is past the pattern end`);
    }
  }
  for (const t of arr.tracks) {
    for (const c of t.clips) {
      const p = arr.patterns[c.pattern_id];
      if (!p) errs.push(`Clip ${c.id} on ${t.name} uses a missing pattern`);
      else if (p.kind !== t.kind) errs.push(`Clip ${c.id}: ${p.kind} pattern on a ${t.kind} track`);
      if (c.start_bar + c.bars > arr.bars) errs.push(`Clip ${c.id} on ${t.name} runs past the end`);
    }
  }
  return errs;
}
