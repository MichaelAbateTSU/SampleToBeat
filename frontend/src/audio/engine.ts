// Live studio playback. Each track is rendered dry in the browser (audio/render.ts, a port of the Python
// renderer) and streamed through a Tone.js channel strip (filters, EQ, drive, crush, compressor, width,
// delay, reverb, volume and constant-power pan) into a master gain and -1 dB limiter. Fader moves are
// instant; pattern edits re-render just the affected lane and hot-swap it at the current position.
// "Render HQ" (POST /preview) plays the exact export render when the last 1% matters.

import * as Tone from "tone";
import { urls } from "../api/client";
import type { Arrangement, Fx, Sample, Track } from "../api/types";
import { audibleTracks } from "../lib/arrangement";
import { SR, TICKS_PER_BAR, barsToSeconds, clamp, dbToGain, gainToDb, ticksToSamples } from "../lib/music";
import { type ChopBuffer, type Stereo, chopKey, renderTrack, requiredChopBuffers, trackSignature } from "./render";

let ctxReady: Promise<void> | null = null;

/** Create (once) a 48 kHz context so buffers match the engine sample rate, then resume it. */
export function ensureAudio(): Promise<void> {
  if (!ctxReady) {
    ctxReady = (async () => {
      try {
        const raw = new AudioContext({ sampleRate: SR, latencyHint: "interactive" });
        Tone.setContext(new Tone.Context(raw));
      } catch {
        /* fall back to Tone's default context */
      }
    })();
  }
  return ctxReady.then(() => Tone.start());
}

const decodeCache = new Map<string, Promise<Stereo>>();

export async function fetchStereo(url: string): Promise<Stereo> {
  let p = decodeCache.get(url);
  if (!p) {
    p = (async () => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Could not load audio (${res.status})`);
      const ab = await res.arrayBuffer();
      const buf = await Tone.getContext().rawContext.decodeAudioData(ab);
      const l = buf.getChannelData(0).slice();
      const r = buf.numberOfChannels > 1 ? buf.getChannelData(1).slice() : l.slice();
      return [l, r] as Stereo;
    })();
    decodeCache.set(url, p);
    p.catch(() => decodeCache.delete(url));
  }
  return p;
}

function toToneBuffer(y: Stereo): Tone.ToneAudioBuffer {
  const ctx = Tone.getContext().rawContext;
  const b = ctx.createBuffer(2, Math.max(1, y[0].length), SR);
  b.copyToChannel(y[0], 0);
  b.copyToChannel(y[1], 1);
  return new Tone.ToneAudioBuffer(b as AudioBuffer);
}

const FX_DEFAULTS: Fx = {
  highpass_hz: 0, lowpass_hz: 0, saturation: 0, bitcrush_bits: 0, reverb: 0, delay: 0, delay_beats: 0.75, width: 1,
  compress: false, wobble: 0, vinyl: 0, eq_low_db: 0, eq_mid_db: 0, eq_high_db: 0,
};

class Strip {
  player = new Tone.Player();
  hp = new Tone.Filter({ type: "highpass", frequency: 10, rolloff: -12 });
  lp = new Tone.Filter({ type: "lowpass", frequency: 20000, rolloff: -12 });
  eq = new Tone.EQ3({ lowFrequency: 200, highFrequency: 4000 });
  drive = new Tone.Distortion({ distortion: 0, wet: 0 });
  crush = new Tone.BitCrusher(16);
  comp = new Tone.Compressor({ threshold: 0, ratio: 1, attack: 0.01, release: 0.12 });
  width = new Tone.StereoWidener(0.5);
  delay = new Tone.FeedbackDelay({ delayTime: 0.5, feedback: 0.35, wet: 0 });
  verb = new Tone.Freeverb({ roomSize: 0.6, dampening: 3000, wet: 0 });
  vol = new Tone.Gain(1);
  split = new Tone.Split(2);
  gl = new Tone.Gain(1);
  gr = new Tone.Gain(1);
  merge = new Tone.Merge(2);
  meter = new Tone.Meter({ smoothing: 0.8 });
  signature = "";
  duration = 0;

  constructor(dest: Tone.InputNode) {
    this.crush.wet.value = 0;
    this.player.chain(this.hp, this.lp, this.eq, this.drive, this.crush, this.comp, this.width, this.delay, this.verb, this.vol, this.split);
    this.split.connect(this.gl, 0, 0);
    this.split.connect(this.gr, 1, 0);
    this.gl.connect(this.merge, 0, 0);
    this.gr.connect(this.merge, 0, 1);
    this.merge.connect(dest);
    this.merge.connect(this.meter);
  }

  apply(track: Track, audible: boolean, bpm: number) {
    const fx = { ...FX_DEFAULTS, ...track.fx };
    this.hp.frequency.value = fx.highpass_hz > 0 ? fx.highpass_hz : 10;
    this.lp.frequency.value = fx.lowpass_hz > 0 ? fx.lowpass_hz : 20000;
    this.eq.low.value = fx.eq_low_db;
    this.eq.mid.value = fx.eq_mid_db;
    this.eq.high.value = fx.eq_high_db;
    this.drive.distortion = clamp(fx.saturation, 0, 1);
    this.drive.wet.value = fx.saturation > 0 ? 1 : 0;
    this.crush.bits.value = fx.bitcrush_bits ? clamp(fx.bitcrush_bits, 1, 16) : 16;
    this.crush.wet.value = fx.bitcrush_bits ? 1 : 0;
    this.comp.threshold.value = fx.compress ? -18 : 0;
    this.comp.ratio.value = fx.compress ? 3 : 1;
    this.width.width.value = clamp(fx.width / 2, 0, 1);
    this.delay.delayTime.value = (60 / bpm) * fx.delay_beats;
    this.delay.wet.value = clamp(fx.delay * 0.5, 0, 1);
    this.verb.wet.value = clamp(fx.reverb * 0.4, 0, 1);
    this.vol.gain.value = audible ? dbToGain(track.volume_db) : 0;
    const th = ((clamp(track.pan, -1, 1) + 1) * Math.PI) / 4;
    this.gl.gain.value = Math.cos(th) * Math.SQRT2;
    this.gr.gain.value = Math.sin(th) * Math.SQRT2;
  }

  dispose() {
    for (const n of [this.player, this.hp, this.lp, this.eq, this.drive, this.crush, this.comp, this.width, this.delay, this.verb, this.vol, this.split, this.gl, this.gr, this.merge, this.meter]) n.dispose();
  }
}

export interface EngineStatus {
  loading: boolean;
  error: string | null;
  missing: string[];
}

type Listener = () => void;

export class StudioEngine {
  private strips = new Map<string, Strip>();
  private master?: Tone.Gain;
  private limiter?: Tone.Limiter;
  private masterMeter?: Tone.Meter;
  private kit: Record<string, Stereo | undefined> = {};
  private kitName = "";
  private chopBuffers = new Map<string, ChopBuffer>();
  private arr: Arrangement | null = null;
  private samples: Record<string, Sample> = {};
  private startedAt = 0;
  private startOffset = 0;
  private playing = false;
  private loop: { start: number; end: number } | null = null;
  private syncSeq = 0;
  private listeners = new Set<Listener>();
  status: EngineStatus = { loading: false, error: null, missing: [] };
  masterGainDb = 0;

  subscribe(fn: Listener) {
    this.listeners.add(fn);
    return () => void this.listeners.delete(fn);
  }
  private emit() {
    this.listeners.forEach((f) => f());
  }
  private setStatus(s: Partial<EngineStatus>) {
    this.status = { ...this.status, ...s };
    this.emit();
  }

  private ensureMaster() {
    if (!this.master) {
      this.limiter = new Tone.Limiter(-1).toDestination();
      this.master = new Tone.Gain(1).connect(this.limiter);
      this.masterMeter = new Tone.Meter({ smoothing: 0.8 });
      this.limiter.connect(this.masterMeter);
    }
    return this.master;
  }

  get isPlaying() {
    return this.playing;
  }

  /** Current position in seconds from bar 1. */
  get position(): number {
    if (!this.playing) return this.startOffset;
    const t = this.startOffset + (Tone.now() - this.startedAt);
    if (this.loop && t >= this.loop.end) {
      const len = this.loop.end - this.loop.start;
      return this.loop.start + ((t - this.loop.start) % len);
    }
    return t;
  }

  get duration(): number {
    return this.arr ? barsToSeconds(this.arr.bars, this.arr.bpm) : 0;
  }

  levels(): Record<string, number> {
    const out: Record<string, number> = {};
    this.strips.forEach((s, id) => (out[id] = Number(s.meter.getValue())));
    if (this.masterMeter) out.master = Number(this.masterMeter.getValue());
    return out;
  }

  /** Bring the engine in line with the arrangement: fetch assets, re-render changed lanes, update mixer. */
  async sync(arr: Arrangement, samples: Record<string, Sample>): Promise<void> {
    const seq = ++this.syncSeq;
    await ensureAudio();
    const master = this.ensureMaster();
    this.arr = arr;
    this.samples = samples;
    const needsAssets = arr.kit !== this.kitName || requiredChopBuffers(arr).some((r) => !this.chopBuffers.has(chopKey(r.sampleId, r.ratio, r.pitch) + "@" + arr.bpm + this.transformSig(r.sampleId)));
    if (needsAssets) this.setStatus({ loading: true, error: null });
    try {
      await this.loadAssets(arr, samples);
    } catch (e) {
      this.setStatus({ loading: false, error: (e as Error).message });
      return;
    }
    if (seq !== this.syncSeq) return; // a newer sync superseded this one
    for (const [id, s] of this.strips) {
      if (!arr.tracks.some((t) => t.id === id)) {
        s.dispose();
        this.strips.delete(id);
      }
    }
    const audible = audibleTracks(arr);
    const bufs = this.currentBuffers(arr);
    let rerendered = false;
    for (const t of arr.tracks) {
      let strip = this.strips.get(t.id);
      if (!strip) {
        strip = new Strip(master);
        this.strips.set(t.id, strip);
      }
      strip.apply(t, audible.has(t.id), arr.bpm);
      const sig = trackSignature(arr, t, samples) + "|" + this.kitName;
      if (sig !== strip.signature) {
        const y = renderTrack(arr, t, { samples, chopBuffers: bufs, kit: this.kit });
        strip.player.buffer = toToneBuffer(y);
        strip.duration = y[0].length / SR;
        strip.signature = sig;
        rerendered = true;
        if (this.playing) this.startStrip(strip, Tone.now() + 0.02, this.position);
      }
    }
    this.updateMasterGain(arr);
    if (rerendered || needsAssets) this.setStatus({ loading: false });
  }

  private transformSig(sampleId: string) {
    const s = this.samples[sampleId];
    return s ? JSON.stringify([s.transform, s.start, s.end]) : "";
  }

  private currentBuffers(arr: Arrangement): Map<string, ChopBuffer> {
    const m = new Map<string, ChopBuffer>();
    for (const r of requiredChopBuffers(arr)) {
      const b = this.chopBuffers.get(chopKey(r.sampleId, r.ratio, r.pitch) + "@" + arr.bpm + this.transformSig(r.sampleId));
      if (b) m.set(chopKey(r.sampleId, r.ratio, r.pitch), b);
    }
    return m;
  }

  private async loadAssets(arr: Arrangement, samples: Record<string, Sample>) {
    const jobs: Promise<unknown>[] = [];
    if (arr.kit !== this.kitName) {
      const kit = arr.kit;
      const insts = ["kick", "snare", "clap", "hat", "openhat", "ride", "rim", "perc", "crash"];
      jobs.push(
        Promise.all(insts.map(async (i) => [i, await fetchStereo(urls.kitAudio(kit, i, kitVersion))] as const)).then((pairs) => {
          this.kit = Object.fromEntries(pairs);
          this.kitName = kit;
        }),
      );
    }
    const missing: string[] = [];
    for (const r of requiredChopBuffers(arr)) {
      const s = samples[r.sampleId];
      if (!s) {
        missing.push(r.sampleId);
        continue;
      }
      const tsig = this.transformSig(r.sampleId);
      const key = chopKey(r.sampleId, r.ratio, r.pitch) + "@" + arr.bpm + tsig;
      if (this.chopBuffers.has(key)) continue;
      const v = hashString(tsig);
      jobs.push(
        fetchStereo(urls.sampleBuffer(r.sampleId, arr.bpm, r.ratio, r.pitch, v)).then((data) => {
          this.chopBuffers.set(key, { data, speed: arr.bpm / (s.source_bpm * r.ratio) });
        }),
      );
    }
    await Promise.all(jobs);
    this.status.missing = missing;
  }

  /** Invalidate kit sounds (after the user replaces one). */
  reloadKit() {
    kitVersion = Date.now();
    this.kitName = "";
    for (const k of [...decodeCache.keys()]) if (k.startsWith("/api/kits/")) decodeCache.delete(k);
    this.strips.forEach((s) => (s.signature = ""));
  }

  private updateMasterGain(arr: Arrangement) {
    // live stand-in for the export's LUFS normalisation: RMS of the summed lanes toward the target
    let sum = 0, count = 0;
    const audible = audibleTracks(arr);
    for (const t of arr.tracks) {
      const s = this.strips.get(t.id);
      const b = s?.player.loaded ? s.player.buffer.toArray() : null;
      if (!s || !b || !audible.has(t.id)) continue;
      const g = dbToGain(t.volume_db);
      const ch = Array.isArray(b) ? b : [b, b];
      const step = 64;
      for (let i = 0; i < ch[0].length; i += step) {
        const v = ((ch[0][i] + ch[1][i]) / 2) * g;
        sum += v * v;
      }
      count = Math.max(count, Math.ceil(ch[0].length / step));
    }
    const rmsDb = count ? gainToDb(Math.sqrt(sum / count)) : -60;
    const target = arr.master?.target_lufs ?? -14;
    this.masterGainDb = rmsDb < -60 ? 0 : clamp(target - (rmsDb + 1), -24, 12);
    if (this.master) this.master.gain.rampTo(dbToGain(this.masterGainDb), 0.05);
  }

  private startStrip(strip: Strip, when: number, offset: number) {
    if (!strip.player.loaded) return;
    if (strip.player.state === "started") strip.player.stop(when);
    strip.player.loop = !!this.loop;
    if (this.loop) {
      strip.player.loopStart = this.loop.start;
      strip.player.loopEnd = this.loop.end;
    }
    if (offset < strip.duration) strip.player.start(when, offset);
  }

  setLoop(startBar: number | null, bars = 4) {
    if (!this.arr || startBar === null) this.loop = null;
    else {
      const a = this.arr;
      const s = ticksToSamples(startBar * TICKS_PER_BAR, a.bpm) / SR;
      this.loop = { start: s, end: s + barsToSeconds(Math.min(bars, a.bars - startBar), a.bpm) };
    }
    if (this.playing) this.play(this.position);
  }

  async play(fromSeconds = this.startOffset) {
    await ensureAudio();
    let from = Math.max(0, fromSeconds);
    if (this.loop && (from < this.loop.start || from >= this.loop.end)) from = this.loop.start;
    if (!this.loop && from >= this.duration) from = 0;
    const when = Tone.now() + 0.05;
    this.strips.forEach((s) => this.startStrip(s, when, from));
    this.startedAt = when;
    this.startOffset = from;
    this.playing = true;
    this.emit();
  }

  pause() {
    const pos = this.position;
    this.strips.forEach((s) => s.player.state === "started" && s.player.stop());
    this.playing = false;
    this.startOffset = pos;
    this.emit();
  }

  stop() {
    this.strips.forEach((s) => s.player.state === "started" && s.player.stop());
    this.playing = false;
    this.startOffset = this.loop ? this.loop.start : 0;
    this.emit();
  }

  seek(seconds: number) {
    const s = clamp(seconds, 0, this.duration);
    if (this.playing) this.play(s);
    else {
      this.startOffset = s;
      this.emit();
    }
  }

  /** Audition a chop (pads, sample editor) through the master bus. */
  async audition(sampleId: string, s0Beats: number, s1Beats: number, bpm: number, ratio = 1, pitch = 0) {
    await ensureAudio();
    const master = this.ensureMaster();
    const s = this.samples[sampleId];
    if (!s) return;
    const data = await fetchStereo(urls.sampleBuffer(sampleId, bpm, ratio, pitch, hashString(this.transformSig(sampleId))));
    const speed = bpm / (s.source_bpm * ratio);
    const spb = 60 / bpm;
    const base = Math.round(((s.pickup ?? 0) / speed) * SR);
    const a = base + Math.round(s0Beats * ratio * spb * SR);
    const b = Math.min(data[0].length, base + Math.round(s1Beats * ratio * spb * SR));
    if (b - a < 16) return;
    const p = new Tone.Player(toToneBuffer([data[0].slice(a, b), data[1].slice(a, b)])).connect(master);
    p.fadeIn = 0.003;
    p.fadeOut = 0.008;
    p.onstop = () => setTimeout(() => p.dispose(), 100);
    p.start();
  }

  setSamples(samples: Record<string, Sample>) {
    this.samples = samples;
  }

  dispose() {
    this.stop();
    this.strips.forEach((s) => s.dispose());
    this.strips.clear();
    this.master?.dispose();
    this.limiter?.dispose();
    this.masterMeter?.dispose();
    this.master = undefined;
    this.arr = null;
  }
}

let kitVersion = 0;

export function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return Math.abs(h) || 1;
}

export const engine = new StudioEngine();
