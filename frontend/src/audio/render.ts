// In-browser port of the per-track renderer in backend/src/choplab/render.py and bass.py.
// Tracks are rendered to stereo Float32 buffers (before track FX/volume/pan) at SR; the live engine plays
// them through Tone.js mixer channels, so moving a fader is instant and edits only re-render one lane.
// Mastering (LUFS normalisation + limiter) and some FX are only exact in the HQ render, which uses the
// same Python code as export.

import type { Arrangement, ArrEvent, BassSettings, ChopEvent, DrumEvent, NoteEvent, Sample, Track } from "../api/types";
import { PPQ, SR, TICKS_PER_BAR, dbToGain, midiToHz, pyRound, swingOffsetTicks, ticksToSamples } from "../lib/music";

export type Stereo = [Float32Array, Float32Array];
export interface TimelineHit<E extends ArrEvent = ArrEvent> {
  tick: number;
  event: E;
  clipEnd: number;
}

export const DEFAULT_808: BassSettings = {
  mode: "synth", decay: 0.9, sustain: 0, release_ms: 25, attack_ms: 2, pitch_env_semis: 7, pitch_env_ms: 30,
  glide_ms: 70, harmonics: 0.25, drive: 0.35, gain_db: 0, sample_path: null, root_midi: 36,
};
export const CHOKE: Record<string, string> = { hat: "hats", openhat: "hats" };

/** (absolute tick, event, clip end) for every event in [a, b); patterns tile across each clip. */
export function timeline<E extends ArrEvent = ArrEvent>(arr: Arrangement, track: Track, a = 0, b?: number): TimelineHit<E>[] {
  const end = b ?? arr.bars * TICKS_PER_BAR;
  const out: TimelineHit<E>[] = [];
  for (const c of track.clips) {
    const p = arr.patterns[c.pattern_id];
    if (!p || p.length_ticks <= 0) continue;
    const c0 = Math.round(c.start_bar * TICKS_PER_BAR);
    const c1 = c0 + Math.round(c.bars * TICKS_PER_BAR);
    for (let r = c0; r < c1; r += p.length_ticks) {
      for (const e of p.events) {
        const t = r + e.tick;
        if (t < c1 && t >= a && t < end) out.push({ tick: t, event: e as E, clipEnd: c1 });
      }
    }
  }
  return out.sort((x, y) => x.tick - y.tick || String((x.event as DrumEvent).inst ?? "").localeCompare(String((y.event as DrumEvent).inst ?? "")));
}

export function stereo(n: number): Stereo {
  return [new Float32Array(n), new Float32Array(n)];
}

/** Add `y` (optionally scaled) into `out` at `pos`, clipping at both ends. */
function place(out: Stereo, y: Stereo, pos: number, gain: [number, number] = [1, 1]) {
  let start = 0;
  if (pos < 0) {
    start = -pos;
    pos = 0;
  }
  const n = Math.min(y[0].length - start, out[0].length - pos);
  for (let ch = 0; ch < 2; ch++) {
    const o = out[ch], s = y[ch], g = gain[ch];
    for (let i = 0; i < n; i++) o[pos + i] += s[start + i] * g;
  }
}

/** transform.fade: sin² ramps in and out. */
export function fade(y: Stereo, inMs: number, outMs: number): Stereo {
  const r: Stereo = [y[0].slice(), y[1].slice()];
  const len = r[0].length;
  for (const [ms, dir] of [[inMs, 1], [outMs, -1]] as const) {
    const n = Math.min(len, Math.floor((SR * ms) / 1000));
    if (n > 1) {
      for (let i = 0; i < n; i++) {
        const env = Math.sin((i / (n - 1)) * (Math.PI / 2)) ** 2;
        const idx = dir === 1 ? i : len - 1 - i;
        r[0][idx] *= env;
        r[1][idx] *= env;
      }
    }
  }
  return r;
}

/** transform.pan gains (constant power, unity at centre). */
export function panGains(p: number): [number, number] {
  const th = ((Math.max(-1, Math.min(1, p)) + 1) * Math.PI) / 4;
  return [Math.cos(th) * Math.SQRT2, Math.sin(th) * Math.SQRT2];
}

const slice = (y: Stereo, a: number, b: number): Stereo => [y[0].subarray(Math.max(0, a), b), y[1].subarray(Math.max(0, a), b)];
const reversed = (y: Stereo): Stereo => [y[0].slice().reverse(), y[1].slice().reverse()];

// ---------------------------------------------------------------- drums

export function renderDrums(tl: TimelineHit<DrumEvent>[], kit: Record<string, Stereo | undefined>, bpm: number, n: number, swing: number, a: number): Stereo {
  const out = stereo(n);
  const starts = tl.map(({ tick }) => ticksToSamples(tick + swingOffsetTicks(tick, swing) - a, bpm));
  tl.forEach(({ event: e }, i) => {
    const snd = kit[e.inst];
    if (!snd) return;
    let y: Stereo = snd;
    let pos = starts[i];
    if (e.reverse) {
      const d = ticksToSamples(e.dur ?? PPQ, bpm);
      const r = reversed(y);
      y = fade(slice(r, r[0].length - d, r[0].length), 20, 2);
      pos += d - y[0].length;
    } else if (e.dur) {
      y = fade(slice(y, 0, ticksToSamples(e.dur, bpm)), 0, 5);
    }
    const group = CHOKE[e.inst];
    if (group) {
      for (let j = i + 1; j < tl.length; j++) {
        if (CHOKE[tl[j].event.inst] === group) {
          const nxt = starts[j];
          if (nxt - pos < y[0].length) y = fade(slice(y, 0, Math.max(0, nxt - pos)), 0, 5);
          break;
        }
      }
    }
    const v = e.vel ?? 1;
    place(out, y, pos, [v, v]);
  });
  return out;
}

// ---------------------------------------------------------------- 808

interface ChainNote { note: NoteEvent; s: number; e: number }

function chains(notes: NoteEvent[], bpm: number, total: number): ChainNote[][] {
  const ev = [...notes].sort((x, y) => x.tick - y.tick);
  const out: ChainNote[][] = [];
  for (const nt of ev) {
    const s = ticksToSamples(nt.tick, bpm);
    const e = Math.min(total, s + Math.max(1, ticksToSamples(nt.dur, bpm)));
    if (s >= total) break;
    if (out.length) {
      const last = out[out.length - 1];
      const prev = last[last.length - 1];
      if (s < prev.e) {
        prev.e = s;
        if (nt.slide) {
          last.push({ note: nt, s, e });
          continue;
        }
      }
    }
    out.push([{ note: nt, s, e }]);
  }
  return out.filter((c) => c[c.length - 1].e > c[0].s);
}

function env(n: number, attack: number, release: number): Float32Array {
  const e = new Float32Array(n).fill(1);
  const a = Math.min(attack, Math.floor(n / 2)), r = Math.min(release, Math.floor(n / 2));
  for (let i = 0; i < a; i++) e[i] = Math.sin((a > 1 ? i / (a - 1) : 1) * (Math.PI / 2)) ** 2;
  for (let i = 0; i < r; i++) e[n - r + i] *= Math.cos((r > 1 ? i / (r - 1) : 1) * (Math.PI / 2)) ** 2;
  return e;
}

/** bass.render (synth mode): mono chains with pitch drop, log-frequency glide, harmonics and tanh drive. */
export function render808(notes: NoteEvent[], bpm: number, total: number, params: Partial<BassSettings> = {}): Float32Array {
  const p = { ...DEFAULT_808, ...params };
  const out = new Float32Array(total);
  const h = p.harmonics;
  const d = 1 + 6 * p.drive;
  const tanhD = Math.tanh(d);
  for (const chain of chains(notes, bpm, total)) {
    const s0 = chain[0].s, e0 = chain[chain.length - 1].e;
    const n = e0 - s0;
    let phase = 0;
    let prevHz: number | null = null;
    const vel = chain[0].note.vel ?? 1;
    const en = env(n, Math.floor((p.attack_ms * SR) / 1000), Math.floor((p.release_ms * SR) / 1000));
    const decay = Math.max(0.02, p.decay);
    for (const { note, s, e } of chain) {
      const hz = midiToHz(note.midi);
      const from = prevHz;
      for (let k = 0; k < e - s; k++) {
        const t = k / SR;
        const f = from === null
          ? hz * 2 ** ((p.pitch_env_semis * Math.exp(-t / (p.pitch_env_ms / 1000))) / 12)
          : from * (hz / from) ** Math.min(1, t / Math.max(1e-4, p.glide_ms / 1000));
        phase += (2 * Math.PI * f) / SR;
        const i = s - s0 + k;
        let y = Math.sin(phase) + h * 0.5 * Math.sin(2 * phase) + h * 0.3 * Math.sin(3 * phase);
        y *= Math.max(Math.exp(-(i / SR) / decay), p.sustain) * vel * en[i];
        if (p.drive > 0) y = Math.tanh(d * y) / tanhD;
        out[s0 + i] += y;
      }
      prevHz = hz;
    }
  }
  const g = dbToGain(p.gain_db) * 0.8;
  for (let i = 0; i < total; i++) out[i] *= g;
  return out;
}

/** bass.duck: sidechain-style dip on every kick. */
export function duck(y: Float32Array, kickTicks: number[], bpm: number, amount = 0.5, attackMs = 4, releaseMs = 140): Float32Array {
  if (amount <= 0 || !kickTicks.length) return y;
  const g = new Float32Array(y.length).fill(1);
  const a = Math.max(1, Math.floor((attackMs * SR) / 1000)), r = Math.floor((releaseMs * SR) / 1000);
  const shape = new Float32Array(a + r);
  for (let i = 0; i < a; i++) shape[i] = 1 - (a > 1 ? (amount * i) / (a - 1) : 0);
  for (let i = 0; i < r; i++) shape[a + i] = 1 - amount * Math.exp(-i / (r / 4));
  for (const t of kickTicks) {
    const s = ticksToSamples(t, bpm);
    for (let i = 0; i < shape.length && s + i < g.length; i++) if (s + i >= 0) g[s + i] = Math.min(g[s + i], shape[i]);
  }
  const out = new Float32Array(y.length);
  for (let i = 0; i < y.length; i++) out[i] = y[i] * g[i];
  return out;
}

export function renderBass(tl: TimelineHit<NoteEvent>[], params: Partial<BassSettings>, bpm: number, n: number, swing: number, a: number, kickTicks: number[]): Stereo {
  const notes = tl.map(({ tick, event, clipEnd }) => ({ ...event, tick: tick + swingOffsetTicks(tick, swing) - a, dur: Math.min(event.dur, clipEnd - tick) }));
  let y = render808(notes, bpm, n, params);
  y = duck(y, kickTicks, bpm, params.duck ?? 0.45);
  return [y, y.slice()];
}

// ---------------------------------------------------------------- sample chops

export interface ChopBuffer {
  data: Stereo;
  /** project-tempo speed factor: bpm / (source_bpm * ratio) */
  speed: number;
}
export type ChopBufferKey = string;
export const chopKey = (sampleId: string, ratio: number, pitch: number) => `${sampleId}|${ratio}|${pitch}`;

export function renderChops(tl: TimelineHit<ChopEvent>[], samples: Record<string, Sample>, buffers: Map<ChopBufferKey, ChopBuffer>, bpm: number, n: number, swing: number, a: number, choke = true): Stereo {
  const out = stereo(n);
  const spb = 60 / bpm;
  const starts = tl.map(({ tick }) => ticksToSamples(tick + swingOffsetTicks(tick, swing) - a, bpm));
  tl.forEach(({ tick, event: e, clipEnd }, i) => {
    const s = samples[e.sample_id];
    const buf = buffers.get(chopKey(e.sample_id, e.ratio ?? 1, e.pitch ?? 0));
    if (!s || !buf) return;
    const ratio = e.ratio ?? 1;
    const base = pyRound(((s.pickup ?? 0) / buf.speed) * SR);
    const off = base + pyRound(e.s0 * ratio * spb * SR);
    const pre = e.s0 < 1e-6 ? base : 0;
    let length = Math.min(ticksToSamples(Math.min(e.dur, clipEnd - tick), bpm), pyRound((e.s1 - e.s0) * ratio * spb * SR));
    if (choke && i + 1 < tl.length) length = Math.min(length, starts[i + 1] - starts[i]);
    let seg = slice(buf.data, off - pre, Math.min(buf.data[0].length, off + Math.max(0, length)));
    if (off - pre < 0) return;
    if (seg[0].length < 16) return;
    if (e.reverse) seg = reversed(seg);
    const tf = s.transform ?? {};
    seg = fade(seg, tf.fade_in_ms ?? 5, tf.fade_out_ms ?? 8);
    const g = (e.vel ?? 1) * dbToGain(e.gain_db ?? 0);
    const pg = e.pan ? panGains(e.pan) : [1, 1];
    place(out, seg, starts[i] - pre, [g * pg[0], g * pg[1]]);
  });
  return out;
}

/** Kick tick positions (swung, relative to `a`) used to duck the 808, as in render.render. */
export function kickTicks(arr: Arrangement, a = 0, b?: number): number[] {
  const kick = arr.tracks.find((t) => t.id === "kick");
  if (!kick) return [];
  const swing = arr.swing ?? 50;
  return timeline(arr, kick, a, b).map(({ tick }) => tick + swingOffsetTicks(tick, swing) - a);
}

/** Every (sample, ratio, pitch) buffer the arrangement needs. */
export function requiredChopBuffers(arr: Arrangement): { sampleId: string; ratio: number; pitch: number }[] {
  const seen = new Map<string, { sampleId: string; ratio: number; pitch: number }>();
  for (const p of Object.values(arr.patterns)) {
    for (const e of p.events) {
      if (e.type !== "chop") continue;
      const k = chopKey(e.sample_id, e.ratio ?? 1, e.pitch ?? 0);
      if (!seen.has(k)) seen.set(k, { sampleId: e.sample_id, ratio: e.ratio ?? 1, pitch: e.pitch ?? 0 });
    }
  }
  return [...seen.values()];
}

export interface RenderInputs {
  samples: Record<string, Sample>;
  chopBuffers: Map<ChopBufferKey, ChopBuffer>;
  kit: Record<string, Stereo | undefined>;
}

/** Render one track (dry: before FX, volume and pan) over the whole arrangement, plus a 2 s tail. */
export function renderTrack(arr: Arrangement, track: Track, inputs: RenderInputs, tailS = 2): Stereo {
  const bpm = arr.bpm, swing = arr.swing ?? 50;
  const n = ticksToSamples(arr.bars * TICKS_PER_BAR, bpm) + Math.floor(tailS * SR);
  if (track.kind === "drums") return renderDrums(timeline<DrumEvent>(arr, track), inputs.kit, bpm, n, swing, 0);
  if (track.kind === "bass") return renderBass(timeline<NoteEvent>(arr, track), arr.bass ?? {}, bpm, n, swing, 0, kickTicks(arr));
  return renderChops(timeline<ChopEvent>(arr, track), inputs.samples, inputs.chopBuffers, bpm, n, swing, 0, track.choke ?? true);
}

/** A cheap fingerprint of everything that affects a track's dry render (re-render only when it changes). */
export function trackSignature(arr: Arrangement, track: Track, samples: Record<string, Sample>): string {
  const pats = [...new Set(track.clips.map((c) => c.pattern_id))].map((id) => arr.patterns[id]);
  const extra = track.kind === "bass" ? [arr.bass, kickSignature(arr)] : track.kind === "drums" ? [arr.kit] : [
    [...new Set(pats.flatMap((p) => p?.events.map((e) => (e as ChopEvent).sample_id) ?? []))].map((id) => [id, samples[id]?.transform, samples[id]?.start, samples[id]?.end]),
    track.choke,
  ];
  return JSON.stringify([arr.bpm, arr.swing, arr.bars, track.clips, pats, extra]);
}

function kickSignature(arr: Arrangement) {
  const kick = arr.tracks.find((t) => t.id === "kick");
  return kick ? [kick.clips, kick.clips.map((c) => arr.patterns[c.pattern_id])] : null;
}
