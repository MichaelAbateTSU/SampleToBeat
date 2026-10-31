// Timing constants and math shared with the Python engine (backend/src/choplab/core.py).

export const SR = 48000;
export const PPQ = 480;
export const STEP_TICKS = PPQ / 4;
export const TICKS_PER_BAR = PPQ * 4;
export const STEPS_PER_BAR = 16;

export const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
export const KEYS = NOTE_NAMES.flatMap((n) => [`${n} major`, `${n} minor`]);

export const ticksToSeconds = (ticks: number, bpm: number) => (ticks / PPQ) * (60 / bpm);
export const secondsToTicks = (s: number, bpm: number) => (s * bpm * PPQ) / 60;
export const ticksToSamples = (ticks: number, bpm: number, sr = SR) => Math.round(ticksToSeconds(ticks, bpm) * sr);
export const barsToSeconds = (bars: number, bpm: number) => ticksToSeconds(bars * TICKS_PER_BAR, bpm);
export const midiToHz = (m: number) => 440 * 2 ** ((m - 69) / 12);
export const dbToGain = (db: number) => 10 ** (db / 20);
export const gainToDb = (g: number) => 20 * Math.log10(Math.max(g, 1e-12));

/** Python-compatible round-half-to-even, so tick math lands on the same sample as the renderer. */
export function pyRound(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/** MPC-style swing: 50 = straight, ~66 = triplet feel. Delays off-beat 16ths only (core.swing_offset_ticks). */
export function swingOffsetTicks(tick: number, swingPct: number): number {
  const step = pyRound(tick / STEP_TICKS);
  if (step % 2 === 0 || Math.abs(tick - step * STEP_TICKS) > STEP_TICKS / 4) return 0;
  return pyRound((2 * swingPct / 100 - 1) * STEP_TICKS);
}

export function noteName(midi: number): string {
  return `${NOTE_NAMES[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}

export function parseKey(key: string | null | undefined): { tonic: number; mode: "major" | "minor" } | null {
  if (!key) return null;
  const m = /^\s*([A-Ga-g])([#b]?)\s*(major|minor|maj|min|m)?\s*$/.exec(key);
  if (!m) return null;
  let pc = NOTE_NAMES.indexOf(m[1].toUpperCase());
  if (m[2] === "#") pc += 1;
  if (m[2] === "b") pc -= 1;
  const mode = m[3] && m[3].startsWith("m") && m[3] !== "maj" && m[3] !== "major" ? "minor" : "major";
  return { tonic: (pc + 12) % 12, mode };
}

const SCALES = { major: [0, 2, 4, 5, 7, 9, 11], minor: [0, 2, 3, 5, 7, 8, 10] };

export function scalePitchClasses(key: string | null | undefined): Set<number> | null {
  const k = parseKey(key);
  return k ? new Set(SCALES[k.mode].map((d) => (k.tonic + d) % 12)) : null;
}

export function formatTime(s: number): string {
  if (!Number.isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
}

/** "bar.beat" position label (1-based), e.g. 5.3 */
export function formatBarBeat(ticks: number): string {
  const bar = Math.floor(ticks / TICKS_PER_BAR);
  const beat = Math.floor((ticks % TICKS_PER_BAR) / PPQ);
  return `${bar + 1}.${beat + 1}`;
}

export const clamp = (x: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, x));
