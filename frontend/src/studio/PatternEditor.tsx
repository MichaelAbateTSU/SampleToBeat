import { Dices, Eraser, Minus, Pause, Play, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useCapabilities } from "../api/hooks";
import type { Arrangement, ChopEvent, Clip, DrumEvent, NoteEvent, Pattern, Sample, Track } from "../api/types";
import { engine } from "../audio/engine";
import { DEFAULT_808, kickTicks } from "../audio/render";
import { Button, cx, Empty } from "../components/ui";
import {
  addChop, addNote, findClip, patternUsage, removeEvent, replaceEvents, setDrumVelocity, setPatternLength, toggleDrumStep,
  transposePattern, updateEvent,
} from "../lib/arrangement";
import { noteName, parseKey, PPQ, scalePitchClasses, STEP_TICKS, TICKS_PER_BAR } from "../lib/music";
import { useStudio } from "../stores/studio";
import { CommitSlider } from "./controls";

const CELL = 22;
const LENGTHS = [1, 2, 4, 8, 16];
const randomSeed = () => Math.floor(Math.random() * 2 ** 31);

function StepHeader({ steps }: { steps: number }) {
  return (
    <div className="flex" style={{ paddingLeft: 72 }}>
      {Array.from({ length: steps }, (_, s) => (
        <div key={s} className={cx("tabular shrink-0 text-center text-[9px]", s % 16 === 0 ? "text-ink-200" : "text-ink-500")} style={{ width: CELL }}>
          {s % 4 === 0 ? (s % 16 === 0 ? s / 16 + 1 : `.${(s % 16) / 4 + 1}`) : ""}
        </div>
      ))}
    </div>
  );
}

function LengthSelect({ pattern }: { pattern: Pattern }) {
  const apply = useStudio((s) => s.apply);
  const bars = pattern.length_ticks / TICKS_PER_BAR;
  return (
    <label className="flex items-center gap-1 text-xs text-ink-400">
      Length
      <select aria-label="Pattern length" value={LENGTHS.includes(bars) ? bars : ""} onChange={(e) => apply("Pattern length", (a) => setPatternLength(a, pattern.id, Number(e.target.value) * TICKS_PER_BAR))}
        className="h-7 rounded border border-ink-600 bg-ink-800 px-1 text-sm text-ink-200">
        {!LENGTHS.includes(bars) && <option value="">{bars.toFixed(2)} bars</option>}
        {LENGTHS.map((b) => <option key={b} value={b}>{b} bar{b > 1 ? "s" : ""}</option>)}
      </select>
    </label>
  );
}

// ------------------------------------------------------------------ drums

function DrumEditor({ arr, track, pattern }: { arr: Arrangement; track: Track; pattern: Pattern }) {
  const apply = useStudio((s) => s.apply);
  const caps = useCapabilities();
  const [busy, setBusy] = useState(false);
  const [complexity, setComplexity] = useState(0.5);
  const steps = Math.round(pattern.length_ticks / STEP_TICKS);
  const insts = useMemo(() => {
    const all = caps.data?.instruments ?? ["kick", "snare", "clap", "hat", "openhat", "perc", "rim", "crash"];
    const mine = Object.entries(caps.data?.track_of ?? {}).filter(([, t]) => t === track.id).map(([i]) => i);
    const used = pattern.events.flatMap((e) => (e.type === "drum" ? [e.inst] : []));
    const list = mine.length ? [...new Set([...mine, ...used])] : all;
    return all.filter((i) => list.includes(i)).concat(list.filter((i) => !all.includes(i)));
  }, [caps.data, track.id, pattern.events]);
  const grid = useMemo(() => {
    const m = new Map<string, DrumEvent>();
    for (const e of pattern.events) if (e.type === "drum") m.set(`${e.inst}:${Math.floor(e.tick / STEP_TICKS)}`, e);
    return m;
  }, [pattern.events]);

  const randomize = async () => {
    setBusy(true);
    try {
      const bars = Math.max(1, Math.round(pattern.length_ticks / TICKS_PER_BAR));
      const r = await api.drumPattern({ genre: arr.genre, complexity, seed: randomSeed(), bars });
      const events = r.events.filter((e) => e.type === "drum" && insts.includes(e.inst) && e.tick < pattern.length_ticks);
      apply("Randomize drums", (a) => replaceEvents(a, pattern.id, events));
    } catch (e) {
      useStudio.setState({ lastError: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <LengthSelect pattern={pattern} />
        <CommitSlider label="Complexity" value={complexity} min={0} max={1} step={0.05} onCommit={setComplexity} className="w-28" format={(v) => `${Math.round(v * 100)}%`} />
        <Button size="sm" onClick={randomize} busy={busy} data-testid="randomize-drums"><Dices className="size-3.5" /> Randomize</Button>
        <Button size="sm" variant="ghost" onClick={() => apply("Clear pattern", (a) => replaceEvents(a, pattern.id, []))}><Eraser className="size-3.5" /> Clear</Button>
        <span className="text-xs text-ink-400">Click toggles a hit · right-click cycles velocity · swing {arr.swing}% applied on playback</span>
      </div>
      <div className="overflow-x-auto pb-1">
        <StepHeader steps={steps} />
        {insts.map((inst) => (
          <div key={inst} className="flex items-center">
            <div className="w-[72px] shrink-0 truncate pr-2 text-right text-xs text-ink-300">{inst}</div>
            {Array.from({ length: steps }, (_, s) => {
              const e = grid.get(`${inst}:${s}`);
              return (
                <button key={s} data-testid="drum-cell" data-inst={inst} data-step={s} data-on={!!e} aria-label={`${inst} step ${s + 1}`}
                  onClick={() => apply(e ? "Remove hit" : "Add hit", (a) => toggleDrumStep(a, pattern.id, inst, s))}
                  onContextMenu={(ev) => {
                    ev.preventDefault();
                    if (!e) return;
                    const next = e.vel >= 0.95 ? 0.45 : e.vel >= 0.7 ? 1.0 : 0.75;
                    apply("Velocity", (a) => setDrumVelocity(a, pattern.id, inst, s, next));
                  }}
                  title={e ? `vel ${e.vel.toFixed(2)}${e.roll ? " · roll" : ""}` : undefined}
                  className={cx("m-px shrink-0 rounded-sm border", s % 4 === 0 ? "border-ink-500" : "border-ink-700", e ? "" : (Math.floor(s / 4) % 2 ? "bg-ink-800" : "bg-ink-700/60"))}
                  style={{ width: CELL - 2, height: CELL - 2, ...(e ? { background: `rgb(56 189 248 / ${0.3 + Math.min(1, e.vel) * 0.7})` } : {}) }} />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ bass

const BASS_LO = 24, BASS_HI = 60;

function BassEditor({ arr, pattern, clip }: { arr: Arrangement; pattern: Pattern; clip: Clip }) {
  const apply = useStudio((s) => s.apply);
  const [busy, setBusy] = useState(false);
  const [noteLen, setNoteLen] = useState(2);
  const steps = Math.round(pattern.length_ticks / STEP_TICKS);
  const scale = scalePitchClasses(arr.key);
  const notes = pattern.events.map((e, i) => ({ e, i })).filter((x): x is { e: NoteEvent; i: number } => x.e.type === "note");
  const rows = Array.from({ length: BASS_HI - BASS_LO + 1 }, (_, i) => BASS_HI - i);
  const bass = { ...DEFAULT_808, ...arr.bass };
  const setBass = (k: string, v: number) => apply("808 settings", (a) => ({ ...a, bass: { ...a.bass, [k]: v } }));

  const randomize = async () => {
    setBusy(true);
    try {
      const bars = Math.max(1, Math.round(pattern.length_ticks / TICKS_PER_BAR));
      const tonic = parseKey(arr.key)?.tonic ?? 0;
      const roots = Array.from({ length: bars }, (_, b) => (pattern.roots?.[b % (pattern.roots?.length || 1)] ?? tonic));
      const a0 = clip.start_bar * TICKS_PER_BAR;
      const kicks = kickTicks(arr, a0, a0 + bars * TICKS_PER_BAR);
      const r = await api.bassPattern({ genre: arr.genre, roots, kick_ticks: kicks, key: arr.key, seed: randomSeed(), complexity: 0.5 });
      apply("Randomize bass", (a) => replaceEvents(a, pattern.id, r.events.filter((e) => e.tick < pattern.length_ticks)));
    } catch (e) {
      useStudio.setState({ lastError: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex gap-4">
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <LengthSelect pattern={pattern} />
          <label className="flex items-center gap-1 text-xs text-ink-400">Note
            <select aria-label="New note length" value={noteLen} onChange={(e) => setNoteLen(Number(e.target.value))} className="h-7 rounded border border-ink-600 bg-ink-800 px-1 text-sm text-ink-200">
              {[1, 2, 4, 8, 16].map((n) => <option key={n} value={n}>{n}/16</option>)}
            </select>
          </label>
          <Button size="sm" variant="ghost" onClick={() => apply("Transpose", (a) => transposePattern(a, pattern.id, -1))} title="Down a semitone"><Minus className="size-3.5" />1</Button>
          <Button size="sm" variant="ghost" onClick={() => apply("Transpose", (a) => transposePattern(a, pattern.id, 1))} title="Up a semitone"><Plus className="size-3.5" />1</Button>
          <Button size="sm" variant="ghost" onClick={() => apply("Transpose", (a) => transposePattern(a, pattern.id, -12))}>−Oct</Button>
          <Button size="sm" variant="ghost" onClick={() => apply("Transpose", (a) => transposePattern(a, pattern.id, 12))}>+Oct</Button>
          <Button size="sm" onClick={randomize} busy={busy} data-testid="randomize-bass"><Dices className="size-3.5" /> Randomize</Button>
          <Button size="sm" variant="ghost" onClick={() => apply("Clear pattern", (a) => replaceEvents(a, pattern.id, []))}><Eraser className="size-3.5" /> Clear</Button>
        </div>
        <p className="text-xs text-ink-400">Click to add a note · click a note to delete · shift-click toggles slide (glide){arr.key ? ` · ${arr.key} scale highlighted` : ""}</p>
        <div className="max-h-56 overflow-auto rounded border border-ink-700">
          <div className="relative" style={{ width: 44 + steps * CELL, height: rows.length * 12 }}>
            {rows.map((m, r) => (
              <div key={m} className={cx("absolute flex w-full", scale?.has(m % 12) ? "bg-ink-800" : "bg-ink-900")} style={{ top: r * 12, height: 12 }}>
                <div className={cx("sticky left-0 z-10 w-11 shrink-0 border-r border-ink-700 pr-1 text-right text-[9px] leading-3", m % 12 === 0 ? "text-ink-200" : "text-ink-500", "bg-ink-900")}>{noteName(m)}</div>
                {Array.from({ length: steps }, (_, s) => (
                  <div key={s} data-testid="bass-cell" onClick={() => apply("Add note", (a) => addNote(a, pattern.id, { tick: s * STEP_TICKS, dur: Math.min(noteLen * STEP_TICKS, pattern.length_ticks - s * STEP_TICKS), midi: m, vel: 0.9, slide: false }))}
                    className={cx("shrink-0 border-r border-b border-ink-800/80 hover:bg-white/10", s % 4 === 0 && "border-l-ink-600")} style={{ width: CELL, height: 12 }} />
                ))}
              </div>
            ))}
            {notes.filter(({ e }) => e.midi >= BASS_LO && e.midi <= BASS_HI).map(({ e, i }) => (
              <button key={i} data-testid="bass-note" title={`${noteName(e.midi)} · ${e.dur / STEP_TICKS}/16${e.slide ? " · slide" : ""}`}
                onClick={(ev) => apply(ev.shiftKey ? "Toggle slide" : "Delete note", (a) => (ev.shiftKey ? updateEvent(a, pattern.id, i, { slide: !e.slide }) : removeEvent(a, pattern.id, i)))}
                className={cx("absolute z-10 rounded-sm border", e.slide ? "border-white bg-violet-300" : "border-violet-200/50 bg-violet-400")}
                style={{ left: 44 + (e.tick / STEP_TICKS) * CELL, top: (BASS_HI - e.midi) * 12 + 1, width: Math.max(4, (e.dur / STEP_TICKS) * CELL - 1), height: 10 }} />
            ))}
          </div>
        </div>
        {notes.some(({ e }) => e.midi < BASS_LO || e.midi > BASS_HI) && <p className="text-xs text-amber-400">Some notes are outside the visible range ({noteName(BASS_LO)}–{noteName(BASS_HI)}); transpose to see them.</p>}
      </div>
      <div className="w-48 shrink-0 space-y-2 rounded border border-ink-700 p-2" data-testid="bass-settings">
        <div className="text-xs font-medium text-ink-300">808 (all bass tracks)</div>
        <CommitSlider label="Decay" value={bass.decay} min={0.1} max={4} step={0.05} onCommit={(v) => setBass("decay", v)} format={(v) => `${v.toFixed(2)} s`} />
        <CommitSlider label="Glide" value={bass.glide_ms} min={0} max={300} step={5} onCommit={(v) => setBass("glide_ms", v)} format={(v) => `${v} ms`} />
        <CommitSlider label="Drive" value={bass.drive} min={0} max={1} step={0.01} onCommit={(v) => setBass("drive", v)} format={(v) => `${Math.round(v * 100)}%`} />
        <CommitSlider label="Punch" value={bass.pitch_env_semis} min={0} max={24} step={1} onCommit={(v) => setBass("pitch_env_semis", v)} format={(v) => `${v} st`} />
        <CommitSlider label="Kick duck" value={bass.duck ?? 0.5} min={0} max={1} step={0.05} onCommit={(v) => setBass("duck", v)} format={(v) => `${Math.round(v * 100)}%`} />
        <CommitSlider label="Gain" value={bass.gain_db} min={-24} max={12} step={0.5} onCommit={(v) => setBass("gain_db", v)} format={(v) => `${v} dB`} />
        {bass.mode === "sample" && <p className="text-[10px] text-amber-400">Sampled 808 is heard in Render HQ and exports; the live preview uses the synth.</p>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ chops

interface Pad { sampleId: string; s0: number; s1: number; label: string }

function padsFor(sample: Sample): Pad[] {
  const sl = sample.slices?.length ? sample.slices : Array.from({ length: Math.max(1, Math.min(16, Math.round(sample.beats))) }, (_, i) => [i, Math.min(sample.beats, i + 1)] as [number, number]);
  return sl.slice(0, 16).map(([s0, s1], i) => ({ sampleId: sample.id, s0, s1, label: `${i + 1}` }));
}

function ChopEditor({ arr, pattern, samples }: { arr: Arrangement; pattern: Pattern; samples: Record<string, Sample> }) {
  const apply = useStudio((s) => s.apply);
  const list = Object.values(samples);
  const used = pattern.events.find((e): e is ChopEvent => e.type === "chop")?.sample_id;
  const [sampleId, setSampleId] = useState<string | undefined>(used ?? list[0]?.id);
  const sample = sampleId ? samples[sampleId] : undefined;
  const pads = useMemo(() => (sample ? padsFor(sample) : []), [sample]);
  const [pad, setPad] = useState<number>(0);
  const [selEvent, setSelEvent] = useState<number | null>(null);
  const steps = Math.round(pattern.length_ticks / STEP_TICKS);
  const chops = pattern.events.map((e, i) => ({ e, i })).filter((x): x is { e: ChopEvent; i: number } => x.e.type === "chop");
  const sel = selEvent !== null ? (pattern.events[selEvent] as ChopEvent | undefined) : undefined;
  const padColor = (s0: number) => `hsl(${(s0 * 47) % 360} 80% 60%)`;

  if (!list.length) return <Empty title="No samples in this project">Create samples in the Samples tab, then place their chops here.</Empty>;

  const place = (step: number) => {
    const p = pads[pad];
    if (!p) return;
    const ratio = 1;
    const dur = Math.min(Math.round((p.s1 - p.s0) * ratio * PPQ), pattern.length_ticks - step * STEP_TICKS);
    if (apply("Place chop", (a) => addChop(a, pattern.id, { tick: step * STEP_TICKS, dur, sample_id: p.sampleId, s0: p.s0, s1: p.s1, ratio, pitch: 0, vel: 0.9 }))) setSelEvent(null);
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <LengthSelect pattern={pattern} />
        <label className="flex items-center gap-1 text-xs text-ink-400">Sample
          <select aria-label="Pad sample" value={sampleId} onChange={(e) => { setSampleId(e.target.value); setPad(0); }} className="h-7 max-w-56 rounded border border-ink-600 bg-ink-800 px-1 text-sm text-ink-200">
            {list.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <Button size="sm" variant="ghost" onClick={() => apply("Clear pattern", (a) => replaceEvents(a, pattern.id, []))}><Eraser className="size-3.5" /> Clear</Button>
        <span className="text-xs text-ink-400">Click a pad to hear it, then click a step to place it · click a placed chop to edit it</span>
      </div>
      <div className="flex flex-wrap gap-1" data-testid="chop-pads">
        {pads.map((p, i) => (
          <button key={i} data-testid="chop-pad" onClick={() => { setPad(i); void engine.audition(p.sampleId, p.s0, p.s1, arr.bpm); }}
            className={cx("flex h-12 w-12 flex-col items-center justify-center rounded border text-xs", pad === i ? "border-white" : "border-ink-600")}
            style={{ background: `${padColor(p.s0).replace(")", " / 0.25)")}` }}>
            <span className="font-semibold">{p.label}</span>
            <span className="tabular text-[9px] text-ink-300">{p.s0.toFixed(1)}–{p.s1.toFixed(1)}</span>
          </button>
        ))}
      </div>
      <div className="overflow-x-auto pb-1">
        <StepHeader steps={steps} />
        <div className="relative flex" style={{ paddingLeft: 72, height: 30 }}>
          {Array.from({ length: steps }, (_, s) => (
            <button key={s} data-testid="chop-step" aria-label={`Place at step ${s + 1}`} onClick={() => place(s)}
              className={cx("m-px shrink-0 rounded-sm border hover:bg-white/10", s % 4 === 0 ? "border-ink-500" : "border-ink-700")} style={{ width: CELL - 2, height: 28 }} />
          ))}
          {chops.map(({ e, i }) => (
            <button key={i} data-testid="chop-event" onClick={() => setSelEvent(i)}
              title={`${samples[e.sample_id]?.name ?? "missing sample"} · beats ${e.s0.toFixed(2)}–${e.s1.toFixed(2)}`}
              className={cx("absolute top-1 h-[26px] truncate rounded-sm border px-0.5 text-left text-[9px] text-ink-950", selEvent === i ? "border-white" : "border-black/30")}
              style={{ left: 72 + (e.tick / STEP_TICKS) * CELL + 1, width: Math.max(6, (e.dur / STEP_TICKS) * CELL - 2), background: samples[e.sample_id] ? padColor(e.s0) : "#ef4444" }}>
              {e.pitch ? `${e.pitch > 0 ? "+" : ""}${e.pitch}` : ""}
            </button>
          ))}
        </div>
      </div>
      {sel && selEvent !== null && (
        <div className="flex flex-wrap items-end gap-3 rounded border border-ink-700 p-2" data-testid="chop-inspector">
          <Button size="sm" variant="ghost" onClick={() => void engine.audition(sel.sample_id, sel.s0, sel.s1, arr.bpm, sel.ratio, sel.pitch)}><Play className="size-3.5" /> Audition</Button>
          <CommitSlider label="Pitch" value={sel.pitch} min={-12} max={12} step={1} className="w-28" format={(v) => `${v > 0 ? "+" : ""}${v} st`}
            onCommit={(v) => apply("Chop pitch", (a) => updateEvent(a, pattern.id, selEvent, { pitch: v }))} />
          <CommitSlider label="Velocity" value={sel.vel} min={0.05} max={1.5} step={0.05} className="w-28" format={(v) => v.toFixed(2)}
            onCommit={(v) => apply("Chop velocity", (a) => updateEvent(a, pattern.id, selEvent, { vel: v }))} />
          <CommitSlider label="Length" value={sel.dur / STEP_TICKS} min={1} max={Math.max(1, steps - sel.tick / STEP_TICKS)} step={1} className="w-28" format={(v) => `${v}/16`}
            onCommit={(v) => apply("Chop length", (a) => updateEvent(a, pattern.id, selEvent, { dur: v * STEP_TICKS }))} />
          <label className="flex items-center gap-1 text-xs text-ink-300">
            <input type="checkbox" checked={!!sel.reverse} onChange={(e) => apply("Reverse chop", (a) => updateEvent(a, pattern.id, selEvent, { reverse: e.target.checked }))} /> Reverse
          </label>
          <Button size="sm" variant="danger" onClick={() => { if (apply("Delete chop", (a) => removeEvent(a, pattern.id, selEvent))) setSelEvent(null); }}><Trash2 className="size-3.5" /> Delete</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelEvent(null)}><Pause className="size-3.5" /> Done</Button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ container

export default function PatternEditor({ samples }: { samples: Record<string, Sample> }) {
  const arr = useStudio((s) => s.arr)!;
  const selection = useStudio((s) => s.selection);
  const found = selection.clipId ? findClip(arr, selection.clipId) : null;
  if (!found) {
    return <Empty title="Select a clip to edit its pattern">Click a clip in the timeline. Double-click an empty lane to create one.</Empty>;
  }
  const { track, clip } = found;
  const pattern = arr.patterns[clip.pattern_id];
  if (!pattern) return <Empty title="This clip's pattern is missing" />;
  const shared = patternUsage(arr, pattern.id);
  return (
    <div className="space-y-2" data-testid="pattern-editor" data-kind={pattern.kind}>
      <div className="text-xs text-ink-400">
        <span className="text-ink-200">{track.name}</span> · bars {clip.start_bar + 1}–{clip.start_bar + clip.bars}
        {shared > 1 && <span className="ml-2 text-amber-400">Shared by {shared} clips — edits change all of them (use “Make unique” to edit just this one)</span>}
      </div>
      {/* key by pattern so local editor state (selected chop etc.) resets when switching clips */}
      {pattern.kind === "drums" && <DrumEditor key={pattern.id} arr={arr} track={track} pattern={pattern} />}
      {pattern.kind === "bass" && <BassEditor key={pattern.id} arr={arr} pattern={pattern} clip={clip} />}
      {pattern.kind === "sample" && <ChopEditor key={pattern.id} arr={arr} pattern={pattern} samples={samples} />}
    </div>
  );
}
