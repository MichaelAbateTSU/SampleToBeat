import { RotateCcw } from "lucide-react";
import { useCapabilities } from "../api/hooks";
import type { Fx, Track } from "../api/types";
import { Button, cx } from "../components/ui";
import { toggleMute, toggleSolo, TRACK_COLORS, updateTrack } from "../lib/arrangement";
import { useStudio } from "../stores/studio";
import { CommitSlider } from "./controls";
import { useLevels } from "./hooks";

type NumFx = Exclude<keyof Fx, "compress">;
const FX_UI: { key: NumFx; label: string; min: number; max: number; step: number; fmt: (v: number) => string; live: boolean }[] = [
  { key: "highpass_hz", label: "High-pass", min: 0, max: 2000, step: 10, fmt: (v) => (v ? `${v} Hz` : "off"), live: true },
  { key: "lowpass_hz", label: "Low-pass", min: 0, max: 20000, step: 100, fmt: (v) => (v ? `${(v / 1000).toFixed(1)} kHz` : "off"), live: true },
  { key: "eq_low_db", label: "EQ low", min: -12, max: 12, step: 0.5, fmt: (v) => `${v} dB`, live: true },
  { key: "eq_mid_db", label: "EQ mid", min: -12, max: 12, step: 0.5, fmt: (v) => `${v} dB`, live: true },
  { key: "eq_high_db", label: "EQ high", min: -12, max: 12, step: 0.5, fmt: (v) => `${v} dB`, live: true },
  { key: "saturation", label: "Saturation", min: 0, max: 1, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, live: true },
  { key: "bitcrush_bits", label: "Bitcrush", min: 0, max: 16, step: 1, fmt: (v) => (v ? `${v} bit` : "off"), live: true },
  { key: "reverb", label: "Reverb", min: 0, max: 1, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, live: true },
  { key: "delay", label: "Delay", min: 0, max: 1, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, live: true },
  { key: "delay_beats", label: "Delay time", min: 0.25, max: 2, step: 0.25, fmt: (v) => `${v} beat`, live: true },
  { key: "width", label: "Width", min: 0, max: 2, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%`, live: true },
  { key: "wobble", label: "Tape wobble", min: 0, max: 1, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, live: false },
  { key: "vinyl", label: "Vinyl", min: 0, max: 1, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, live: false },
];

function Meter({ db }: { db: number | undefined }) {
  const v = db ?? -Infinity;
  const pct = Math.max(0, Math.min(1, (v + 60) / 60));
  return (
    <div className="relative h-28 w-2 overflow-hidden rounded bg-ink-700" title={Number.isFinite(v) ? `${v.toFixed(1)} dB` : "-∞ dB"}>
      <div className={cx("absolute inset-x-0 bottom-0", v > -1.5 ? "bg-red-500" : v > -12 ? "bg-amber-400" : "bg-emerald-500")} style={{ height: `${pct * 100}%` }} />
    </div>
  );
}

function Strip({ track, level, selected }: { track: Track; level: number | undefined; selected: boolean }) {
  const apply = useStudio((s) => s.apply);
  const select = useStudio((s) => s.select);
  return (
    <div data-testid={`strip-${track.id}`} onClick={() => select({ trackId: track.id })}
      className={cx("flex w-24 shrink-0 cursor-pointer flex-col items-center gap-2 rounded border p-2", selected ? "border-accent/70 bg-ink-800" : "border-ink-700 bg-ink-900")}>
      <div className="flex w-full items-center gap-1">
        <span className="h-3 w-1 rounded" style={{ background: track.color || TRACK_COLORS[track.kind] }} />
        <span className="truncate text-xs">{track.name}</span>
      </div>
      <div className="flex items-end gap-2">
        <Meter db={level} />
        <CommitSlider label="Vol" value={track.volume_db} min={-60} max={12} step={0.5} vertical testId={`volume-${track.id}`}
          format={(v) => `${v > 0 ? "+" : ""}${v}`} onCommit={(v) => apply("Volume", (a) => updateTrack(a, track.id, { volume_db: v }))} />
      </div>
      <CommitSlider label="Pan" value={track.pan} min={-1} max={1} step={0.05} className="w-full"
        format={(v) => (Math.abs(v) < 0.01 ? "C" : v < 0 ? `L${Math.round(-v * 100)}` : `R${Math.round(v * 100)}`)}
        onCommit={(v) => apply("Pan", (a) => updateTrack(a, track.id, { pan: v }))} />
      <div className="flex gap-1">
        <button aria-pressed={track.muted} onClick={(e) => { e.stopPropagation(); apply("Mute", (a) => toggleMute(a, track.id)); }}
          className={cx("h-6 w-7 rounded text-xs font-bold", track.muted ? "bg-red-500 text-white" : "bg-ink-700 hover:bg-ink-600")}>M</button>
        <button aria-pressed={track.solo} onClick={(e) => { e.stopPropagation(); apply("Solo", (a) => toggleSolo(a, track.id)); }}
          className={cx("h-6 w-7 rounded text-xs font-bold", track.solo ? "bg-accent text-ink-950" : "bg-ink-700 hover:bg-ink-600")}>S</button>
      </div>
    </div>
  );
}

function FxPanel({ track }: { track: Track }) {
  const apply = useStudio((s) => s.apply);
  const caps = useCapabilities();
  const fx = { ...(caps.data?.fx_defaults ?? {}), ...track.fx } as Fx;
  const set = (patch: Partial<Fx>) => apply("Track FX", (a) => updateTrack(a, track.id, { fx: { ...track.fx, ...patch } }));
  return (
    <div className="min-w-0 flex-1 rounded border border-ink-700 p-2" data-testid="fx-panel">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">{track.name} FX</span>
        <label className="flex items-center gap-1 text-xs text-ink-300">
          <input type="checkbox" checked={!!fx.compress} onChange={(e) => set({ compress: e.target.checked })} /> Compressor
        </label>
        {track.kind === "drums" && (
          <label className="flex items-center gap-1 text-xs text-ink-300" title="Closed hats cut off open hats">
            <input type="checkbox" checked={track.choke} onChange={(e) => apply("Choke", (a) => updateTrack(a, track.id, { choke: e.target.checked }))} /> Hat choke
          </label>
        )}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => apply("Reset FX", (a) => updateTrack(a, track.id, { fx: {} }))}><RotateCcw className="size-3.5" /> Reset</Button>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-x-4 gap-y-2">
        {FX_UI.map((f) => (
          <CommitSlider key={f.key} label={f.live ? f.label : `${f.label} (HQ)`} value={Number(fx[f.key] ?? 0)} min={f.min} max={f.max} step={f.step} format={f.fmt}
            testId={`fx-${f.key}`} onCommit={(v) => set({ [f.key]: v } as Partial<Fx>)} />
        ))}
      </div>
      <p className="mt-2 text-[10px] text-ink-500">“(HQ)” effects are applied in Render HQ and exports only.</p>
    </div>
  );
}

export default function Mixer() {
  const arr = useStudio((s) => s.arr)!;
  const selected = useStudio((s) => s.selection.trackId);
  const levels = useLevels();
  const track = arr.tracks.find((t) => t.id === selected);
  return (
    <div className="flex gap-3" data-testid="mixer">
      <div className="flex gap-2 overflow-x-auto pb-1">
        {arr.tracks.map((t) => <Strip key={t.id} track={t} level={levels[t.id]} selected={t.id === selected} />)}
        <div className="flex w-20 shrink-0 flex-col items-center gap-2 rounded border border-ink-600 bg-ink-900 p-2">
          <span className="text-xs font-medium">Master</span>
          <Meter db={levels.master} />
          <span className="text-center text-[10px] text-ink-500">Exports master to {arr.master.target_lufs} LUFS</span>
        </div>
      </div>
      {track ? <FxPanel track={track} /> : <p className="self-center text-sm text-ink-400">Select a channel to edit its effects.</p>}
    </div>
  );
}
