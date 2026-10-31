import { useMutation } from "@tanstack/react-query";
import { AudioLines, Check, CloudOff, Loader2, Pause, Play, Redo2, Repeat, Square, Undo2, ZoomIn, ZoomOut } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useCapabilities } from "../api/hooks";
import type { Project } from "../api/types";
import { engine } from "../audio/engine";
import { usePreview } from "../audio/player";
import { cx, IconButton } from "../components/ui";
import { setBars, setGlobal } from "../lib/arrangement";
import { formatBarBeat, formatTime, KEYS, secondsToTicks } from "../lib/music";
import { useStudio } from "../stores/studio";
import { useEnginePlaying, useLevels, usePosition } from "./hooks";

export interface LoopState {
  on: boolean;
  start: number;
  bars: number;
}

function NumInput({ label, value, min, max, step = 1, onCommit, width = "w-16", testId }: {
  label: string; value: number; min: number; max: number; step?: number; onCommit: (v: number) => void; width?: string; testId?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    const v = Number(draft);
    setDraft(null);
    if (Number.isFinite(v) && v >= min && v <= max && v !== value) onCommit(v);
  };
  return (
    <label className="flex flex-col text-[10px] text-ink-400 uppercase">
      {label}
      <input aria-label={label} data-testid={testId} type="number" min={min} max={max} step={step} value={draft ?? String(Math.round(value * 100) / 100)}
        onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        className={cx("tabular h-7 rounded border border-ink-600 bg-ink-800 px-1.5 text-sm text-ink-200 normal-case", width)} />
    </label>
  );
}

function SmallSelect({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col text-[10px] text-ink-400 uppercase">
      {label}
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className="h-7 rounded border border-ink-600 bg-ink-800 px-1 text-sm text-ink-200 normal-case">
        {options.map((o) => <option key={o} value={o}>{o || "—"}</option>)}
      </select>
    </label>
  );
}

function MasterMeter() {
  const lv = useLevels();
  const db = lv.master ?? -Infinity;
  const pct = Math.max(0, Math.min(1, (db + 48) / 48));
  return (
    <div className="flex w-24 flex-col gap-0.5" title={`Master ${Number.isFinite(db) ? db.toFixed(1) : "-∞"} dB (live, limited at −1 dB)`}>
      <span className="text-[10px] text-ink-400 uppercase">Master</span>
      <div className="h-2 overflow-hidden rounded bg-ink-700">
        <div className={cx("h-full", db > -1.5 ? "bg-red-500" : db > -9 ? "bg-amber-400" : "bg-emerald-500")} style={{ width: `${pct * 100}%` }} />
      </div>
    </div>
  );
}

export default function Transport({ project, loop, setLoop, zoom, setZoom, onSave }: {
  project: Project; loop: LoopState; setLoop: (l: LoopState) => void; zoom: number; setZoom: (z: number) => void; onSave: () => void;
}) {
  const arr = useStudio((s) => s.arr)!;
  const apply = useStudio((s) => s.apply);
  const undo = useStudio((s) => s.undo);
  const redo = useStudio((s) => s.redo);
  const canUndo = useStudio((s) => s.past.length > 0);
  const canRedo = useStudio((s) => s.future.length > 0);
  const save = useStudio((s) => s.save);
  const saveError = useStudio((s) => s.saveError);
  const playing = useEnginePlaying();
  const pos = usePosition();
  const caps = useCapabilities();
  const preview = usePreview();

  const hq = useMutation({
    mutationFn: () => api.renderPreview(project.id, {
      scope: loop.on ? "loop" : "full", loop_start_bar: loop.start, loop_bars: loop.bars, arrangement: arr,
    }),
    onSuccess: (r) => {
      engine.pause();
      void preview.play(`${r.url}${r.url.includes("?") ? "&" : "?"}t=${Date.now()}`, `HQ render · ${project.name}`, { loop: loop.on });
    },
  });

  const toggle = () => {
    preview.stop();
    if (playing) engine.pause();
    else void engine.play();
  };

  return (
    <div className="flex flex-wrap items-end gap-x-3 gap-y-2 border-b border-ink-700 bg-ink-900 px-3 py-2" data-testid="transport">
      <div className="flex items-center gap-0.5">
        <IconButton label={playing ? "Pause (Space)" : "Play (Space)"} data-testid="play" active={playing} onClick={toggle}>
          {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        </IconButton>
        <IconButton label="Stop" data-testid="stop" onClick={() => engine.stop()}><Square className="size-3.5" /></IconButton>
        <IconButton label="Loop (L)" data-testid="loop" active={loop.on} onClick={() => setLoop({ ...loop, on: !loop.on })}><Repeat className="size-4" /></IconButton>
      </div>
      <div className="flex flex-col">
        <span className="text-[10px] text-ink-400 uppercase">Position</span>
        <span className="tabular w-24 text-sm" data-testid="position">{formatBarBeat(secondsToTicks(pos, arr.bpm))} · {formatTime(pos)}</span>
      </div>
      {loop.on && (
        <>
          <NumInput label="Loop bar" value={loop.start + 1} min={1} max={arr.bars} onCommit={(v) => setLoop({ ...loop, start: v - 1 })} width="w-14" />
          <NumInput label="Loop len" value={loop.bars} min={1} max={64} onCommit={(v) => setLoop({ ...loop, bars: v })} width="w-14" />
        </>
      )}
      <div className="mx-1 h-8 w-px bg-ink-700" />
      <NumInput label="BPM" testId="bpm" value={arr.bpm} min={40} max={220} step={0.1} onCommit={(v) => apply("Tempo", (a) => setGlobal(a, { bpm: v }))} />
      <NumInput label="Swing %" value={arr.swing} min={50} max={75} onCommit={(v) => apply("Swing", (a) => setGlobal(a, { swing: v }))} width="w-14" />
      <NumInput label="Bars" testId="bars" value={arr.bars} min={1} max={256} onCommit={(v) => apply("Length", (a) => setBars(a, v))} width="w-14" />
      <SmallSelect label="Key" value={arr.key ?? ""} options={["", ...KEYS]} onChange={(v) => apply("Key", (a) => setGlobal(a, { key: v || null }))} />
      <SmallSelect label="Kit" value={arr.kit} options={caps.data?.kits ?? [arr.kit]} onChange={(v) => apply("Kit", (a) => setGlobal(a, { kit: v }))} />
      <div className="mx-1 h-8 w-px bg-ink-700" />
      <div className="flex items-center gap-0.5">
        <IconButton label="Undo (Ctrl+Z)" data-testid="undo" disabled={!canUndo} onClick={undo}><Undo2 className="size-4" /></IconButton>
        <IconButton label="Redo (Ctrl+Y)" data-testid="redo" disabled={!canRedo} onClick={redo}><Redo2 className="size-4" /></IconButton>
        <IconButton label="Zoom out" onClick={() => setZoom(Math.max(10, zoom / 1.25))}><ZoomOut className="size-4" /></IconButton>
        <IconButton label="Zoom in" onClick={() => setZoom(Math.min(160, zoom * 1.25))}><ZoomIn className="size-4" /></IconButton>
      </div>
      <button onClick={() => hq.mutate()} disabled={hq.isPending} data-testid="render-hq"
        title="Render through the export engine (mastering, vinyl, wobble, sampled 808) and play it"
        className="flex h-8 items-center gap-1.5 rounded-md bg-ink-700 px-2.5 text-xs hover:bg-ink-600 disabled:opacity-50">
        {hq.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <AudioLines className="size-3.5" />} Render HQ
      </button>
      {hq.error && <span className="text-xs text-red-400">{(hq.error as Error).message}</span>}
      <div className="ml-auto flex items-end gap-3">
        <MasterMeter />
        <button onClick={onSave} data-testid="save-state" data-state={save} title={saveError ?? "Saved automatically (Ctrl+S to save now)"}
          className={cx("flex h-7 items-center gap-1 rounded px-2 text-xs", save === "error" ? "bg-red-500/15 text-red-400" : "text-ink-400")}>
          {save === "saved" && <><Check className="size-3.5" /> Saved</>}
          {save === "dirty" && <>● Unsaved</>}
          {save === "saving" && <><Loader2 className="size-3.5 animate-spin" /> Saving</>}
          {save === "error" && <><CloudOff className="size-3.5" /> Save failed — retry</>}
        </button>
      </div>
    </div>
  );
}
