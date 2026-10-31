import { Copy, Fingerprint, Plus, Scissors, Trash2, ListOrdered, X } from "lucide-react";
import { memo, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import type { Arrangement, Clip, Pattern, Section, Track, TrackKind } from "../api/types";
import { engine } from "../audio/engine";
import { Button, cx, IconButton, Modal } from "../components/ui";
import {
  addClip, addTrack, canPlace, deleteClip, duplicateClip, findClip, makeUnique, moveClip, patternUsage, removeTrack,
  resizeClip, setSections, splitClip, toggleMute, toggleSolo, TRACK_COLORS,
} from "../lib/arrangement";
import { barsToSeconds, TICKS_PER_BAR } from "../lib/music";
import { useStudio } from "../stores/studio";
import type { LoopState } from "./Transport";
import { useAnimationFrame } from "./hooks";

const HEADER = 184;
const LANE_H = 44;

type Drag = { clipId: string; mode: "move" | "resize"; x0: number; startBar: number; bars: number; dBars: number; trackId: string };

const ClipPreview = memo(function ClipPreview({ pattern, bars, zoom }: { pattern: Pattern; bars: number; zoom: number }) {
  const w = bars * zoom;
  const total = bars * TICKS_PER_BAR;
  const L = pattern.length_ticks || TICKS_PER_BAR;
  const reps = Math.ceil(total / L);
  const marks: { x: number; y: number; w: number; h: number }[] = [];
  const midis = pattern.events.flatMap((e) => (e.type === "note" ? [e.midi] : []));
  const lo = Math.min(...midis, 127), hi = Math.max(...midis, 0);
  const insts = [...new Set(pattern.events.flatMap((e) => (e.type === "drum" ? [e.inst] : [])))].sort();
  for (let r = 0; r < reps && marks.length < 1500; r++) {
    for (const e of pattern.events) {
      const t = r * L + e.tick;
      if (t >= total) continue;
      const x = (t / total) * w;
      if (e.type === "drum") {
        const row = insts.indexOf(e.inst);
        marks.push({ x, y: 4 + (row / Math.max(1, insts.length)) * 28, w: 1.5, h: Math.max(2, 26 / Math.max(1, insts.length)) });
      } else if (e.type === "note") {
        const y = hi > lo ? 4 + ((hi - e.midi) / (hi - lo)) * 24 : 14;
        marks.push({ x, y, w: Math.max(1.5, (e.dur / total) * w), h: 4 });
      } else {
        marks.push({ x, y: 6 + ((e.s0 * 7) % 20), w: Math.max(1.5, (e.dur / total) * w - 1), h: 6 });
      }
    }
  }
  return (
    <svg width={w} height={34} className="pointer-events-none absolute inset-x-0 top-2.5 opacity-80">
      {marks.map((m, i) => <rect key={i} x={m.x} y={m.y} width={m.w} height={m.h} rx={1} fill="currentColor" />)}
      {Array.from({ length: Math.max(0, reps - 1) }, (_, i) => (
        <line key={`r${i}`} x1={((i + 1) * L / total) * w} x2={((i + 1) * L / total) * w} y1={0} y2={34} stroke="currentColor" strokeOpacity={0.25} strokeDasharray="2 2" />
      ))}
    </svg>
  );
});

function SectionsModal({ arr, onClose }: { arr: Arrangement; onClose: () => void }) {
  const apply = useStudio((s) => s.apply);
  const [rows, setRows] = useState<Section[]>(arr.sections.map((s) => ({ ...s })));
  const [err, setErr] = useState<string | null>(null);
  const upd = (i: number, p: Partial<Section>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const save = () => {
    const ok = apply("Edit sections", (a) => setSections(a, rows));
    if (ok) onClose();
    else setErr(useStudio.getState().lastError);
  };
  return (
    <Modal title="Sections" onClose={onClose} footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Apply</Button></>}>
      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-2">
            <input aria-label="Section name" value={r.name} onChange={(e) => upd(i, { name: e.target.value })} className="h-8 flex-1 rounded border border-ink-600 bg-ink-800 px-2 text-sm" />
            <label className="text-xs text-ink-400">bar <input type="number" min={1} value={r.start_bar + 1} onChange={(e) => upd(i, { start_bar: Math.max(0, Number(e.target.value) - 1) })} className="h-8 w-16 rounded border border-ink-600 bg-ink-800 px-1 text-sm" /></label>
            <label className="text-xs text-ink-400">bars <input type="number" min={1} value={r.bars} onChange={(e) => upd(i, { bars: Math.max(1, Number(e.target.value)) })} className="h-8 w-16 rounded border border-ink-600 bg-ink-800 px-1 text-sm" /></label>
            <IconButton label="Remove section" onClick={() => setRows(rows.filter((_, j) => j !== i))}><X className="size-4" /></IconButton>
          </div>
        ))}
        <Button size="sm" onClick={() => {
          const last = rows[rows.length - 1];
          setRows([...rows, { name: "section", start_bar: last ? last.start_bar + last.bars : 0, bars: 8 }]);
        }}><Plus className="size-3.5" /> Add section</Button>
        <p className="text-xs text-ink-400">Sections are labels for navigation and generation; sections past the song length are rejected.</p>
        {err && <p className="text-sm text-red-400">{err}</p>}
      </div>
    </Modal>
  );
}

function TrackHeader({ track, selected }: { track: Track; selected: boolean }) {
  const apply = useStudio((s) => s.apply);
  const select = useStudio((s) => s.select);
  return (
    <div onClick={() => select({ trackId: track.id })} data-testid={`track-${track.id}`}
      className={cx("sticky left-0 z-10 flex shrink-0 cursor-pointer items-center gap-1.5 border-r border-b border-ink-700 px-2", selected ? "bg-ink-700" : "bg-ink-800")}
      style={{ width: HEADER, height: LANE_H }}>
      <span className="h-6 w-1 rounded" style={{ background: track.color || TRACK_COLORS[track.kind] }} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm">{track.name}</div>
        <div className="text-[10px] text-ink-400 uppercase">{track.kind}</div>
      </div>
      <button data-testid={`mute-${track.id}`} aria-pressed={track.muted} title="Mute (M)" onClick={(e) => { e.stopPropagation(); apply("Mute", (a) => toggleMute(a, track.id)); }}
        className={cx("h-6 w-6 rounded text-xs font-bold", track.muted ? "bg-red-500 text-white" : "bg-ink-700 text-ink-300 hover:bg-ink-600")}>M</button>
      <button data-testid={`solo-${track.id}`} aria-pressed={track.solo} title="Solo" onClick={(e) => { e.stopPropagation(); apply("Solo", (a) => toggleSolo(a, track.id)); }}
        className={cx("h-6 w-6 rounded text-xs font-bold", track.solo ? "bg-accent text-ink-950" : "bg-ink-700 text-ink-300 hover:bg-ink-600")}>S</button>
    </div>
  );
}

export default function Timeline({ zoom, loop, setLoop }: { zoom: number; loop: LoopState; setLoop: (l: LoopState) => void }) {
  const arr = useStudio((s) => s.arr)!;
  const selection = useStudio((s) => s.selection);
  const apply = useStudio((s) => s.apply);
  const select = useStudio((s) => s.select);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [sectionsOpen, setSectionsOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const playhead = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const barS = barsToSeconds(1, arr.bpm);
  const width = arr.bars * zoom;

  useAnimationFrame((pos) => {
    if (playhead.current) playhead.current.style.transform = `translateX(${HEADER + (pos / barS) * zoom}px)`;
  });

  const sel = selection.clipId ? findClip(arr, selection.clipId) : null;
  const playheadBar = () => Math.round(engine.position / barS);

  const barAt = (clientX: number, el: HTMLElement) => (clientX - el.getBoundingClientRect().left) / zoom;

  const onRulerClick = (e: RPointerEvent<HTMLDivElement>) => {
    const bar = Math.max(0, barAt(e.clientX, e.currentTarget));
    if (e.shiftKey) setLoop({ on: true, start: Math.floor(bar), bars: loop.bars });
    else engine.seek(bar * barS);
  };

  const onClipDown = (e: RPointerEvent<HTMLDivElement>, track: Track, clip: Clip, mode: Drag["mode"]) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    select({ clipId: clip.id, trackId: track.id });
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ clipId: clip.id, mode, x0: e.clientX, startBar: clip.start_bar, bars: clip.bars, dBars: 0, trackId: track.id });
  };
  const onClipMove = (e: RPointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const dBars = Math.round((e.clientX - drag.x0) / zoom);
    let trackId = drag.trackId;
    if (drag.mode === "move") {
      const lane = document.elementsFromPoint(e.clientX, e.clientY).map((el) => (el as HTMLElement).closest?.("[data-lane-id]")).find(Boolean) as HTMLElement | undefined;
      if (lane?.dataset.laneId) trackId = lane.dataset.laneId;
    }
    if (dBars !== drag.dBars || trackId !== drag.trackId) setDrag({ ...drag, dBars, trackId });
  };
  const onClipUp = () => {
    if (!drag) return;
    const d = drag;
    setDrag(null);
    const orig = findClip(arr, d.clipId);
    if (!orig) return;
    if (d.mode === "move") {
      const start = Math.max(0, d.startBar + d.dBars);
      if (start === d.startBar && d.trackId === orig.track.id) return;
      apply("Move clip", (a) => moveClip(a, d.clipId, start, d.trackId === orig.track.id ? undefined : d.trackId));
    } else {
      const bars = Math.max(1, d.bars + d.dBars);
      if (bars !== d.bars) apply("Resize clip", (a) => resizeClip(a, d.clipId, bars));
    }
  };

  const onLaneDouble = (e: React.MouseEvent<HTMLDivElement>, track: Track) => {
    const bar = Math.floor(barAt(e.clientX, e.currentTarget));
    const len = [4, 2, 1].find((b) => bar + b <= arr.bars && canPlace(arr, track, bar, b));
    if (len === undefined) return;
    apply("Add clip", (a) => addClip(a, track.id, bar, len));
    const t = useStudio.getState().arr!.tracks.find((x) => x.id === track.id)!;
    const c = t.clips.find((x) => x.start_bar === bar);
    if (c) select({ clipId: c.id, trackId: track.id });
  };

  const addNewTrack = (kind: TrackKind) => {
    setAddOpen(false);
    const before = new Set(arr.tracks.map((t) => t.id));
    if (apply(`Add ${kind} track`, (a) => addTrack(a, kind))) {
      const t = useStudio.getState().arr!.tracks.find((x) => !before.has(x.id));
      if (t) select({ trackId: t.id, clipId: null });
    }
  };

  const selTrack = arr.tracks.find((t) => t.id === selection.trackId);
  const ruler = useMemo(() => {
    const step = zoom < 16 ? 8 : zoom < 30 ? 4 : zoom < 60 ? 2 : 1;
    return Array.from({ length: Math.ceil(arr.bars / step) }, (_, i) => i * step);
  }, [arr.bars, zoom]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 border-b border-ink-700 bg-ink-900 px-2 py-1">
        <IconButton label="Split at playhead (S)" data-testid="split" disabled={!sel} onClick={() => sel && apply("Split clip", (a) => splitClip(a, sel.clip.id, playheadBar()))}><Scissors className="size-4" /></IconButton>
        <IconButton label="Duplicate (Ctrl+D)" data-testid="duplicate" disabled={!sel} onClick={() => sel && apply("Duplicate clip", (a) => duplicateClip(a, sel.clip.id))}><Copy className="size-4" /></IconButton>
        <IconButton label="Make pattern unique" disabled={!sel || patternUsage(arr, sel.clip.pattern_id) < 2}
          onClick={() => sel && apply("Make unique", (a) => makeUnique(a, sel.clip.id))}><Fingerprint className="size-4" /></IconButton>
        <IconButton label="Delete clip (Del)" data-testid="delete-clip" disabled={!sel} onClick={() => { if (sel && apply("Delete clip", (a) => deleteClip(a, sel.clip.id))) select({ clipId: null }); }}><Trash2 className="size-4" /></IconButton>
        <div className="mx-1 h-5 w-px bg-ink-700" />
        <div className="relative">
          <Button size="sm" variant="ghost" onClick={() => setAddOpen(!addOpen)} data-testid="add-track"><Plus className="size-3.5" /> Track</Button>
          {addOpen && (
            <div className="absolute top-full left-0 z-30 mt-1 w-36 rounded-md border border-ink-600 bg-ink-800 p-1 shadow-xl">
              {(["sample", "drums", "bass"] as TrackKind[]).map((k) => (
                <button key={k} onClick={() => addNewTrack(k)} className="block w-full rounded px-2 py-1 text-left text-sm capitalize hover:bg-ink-700">{k} track</button>
              ))}
            </div>
          )}
        </div>
        <Button size="sm" variant="ghost" disabled={!selTrack || arr.tracks.length <= 1}
          onClick={() => selTrack && window.confirm(`Remove track “${selTrack.name}” and its clips?`) && apply("Remove track", (a) => removeTrack(a, selTrack.id)) && select({ trackId: null, clipId: null })}>
          <Trash2 className="size-3.5" /> Track
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setSectionsOpen(true)}><ListOrdered className="size-3.5" /> Sections</Button>
        <span className="ml-auto text-xs text-ink-400">
          {sel ? `${sel.track.name}: bars ${sel.clip.start_bar + 1}–${sel.clip.start_bar + sel.clip.bars}${patternUsage(arr, sel.clip.pattern_id) > 1 ? " · shared pattern" : ""}` : "Double-click a lane to add a clip · Shift-click the ruler to set the loop"}
        </span>
      </div>

      <div ref={scroller} className="relative min-h-0 flex-1 overflow-auto" data-testid="timeline">
        <div className="relative" style={{ width: HEADER + width + 40 }}>
          {/* ruler + sections */}
          <div className="sticky top-0 z-20 flex bg-ink-900">
            <div className="sticky left-0 z-10 shrink-0 border-r border-b border-ink-700 bg-ink-900" style={{ width: HEADER }} />
            <div className="relative border-b border-ink-700" style={{ width }}>
              <div className="relative h-5 cursor-pointer" onPointerDown={onRulerClick} data-testid="ruler">
                {loop.on && (
                  <div className="absolute inset-y-0 bg-accent/25 border-x border-accent" style={{ left: loop.start * zoom, width: loop.bars * zoom }} />
                )}
                {ruler.map((b) => (
                  <span key={b} className="tabular absolute top-0.5 border-l border-ink-500 pl-1 text-[10px] text-ink-400" style={{ left: b * zoom }}>{b + 1}</span>
                ))}
              </div>
              <div className="relative h-5">
                {arr.sections.map((s, i) => (
                  <button key={i} onClick={() => engine.seek(s.start_bar * barS)} title={`${s.name}: bars ${s.start_bar + 1}–${s.start_bar + s.bars}`}
                    className="absolute top-0.5 h-4 truncate rounded-sm bg-ink-700 px-1 text-left text-[10px] text-ink-300 hover:bg-ink-600"
                    style={{ left: s.start_bar * zoom + 1, width: s.bars * zoom - 2 }}>{s.name}</button>
                ))}
              </div>
            </div>
          </div>

          {/* lanes */}
          {arr.tracks.map((track) => (
            <div key={track.id} className="flex">
              <TrackHeader track={track} selected={selection.trackId === track.id} />
              <div data-lane-id={track.id} onDoubleClick={(e) => onLaneDouble(e, track)}
                onPointerDown={(e) => { if (e.target === e.currentTarget) select({ trackId: track.id, clipId: null }); }}
                className={cx("relative border-b border-ink-700", drag?.trackId === track.id && drag.mode === "move" && "bg-ink-800/60", track.muted && "opacity-50")}
                style={{ width, height: LANE_H, backgroundImage: `repeating-linear-gradient(90deg, rgb(255 255 255 / 0.05) 0 1px, transparent 1px ${zoom * 4}px)` }}>
                {loop.on && <div className="pointer-events-none absolute inset-y-0 bg-accent/5" style={{ left: loop.start * zoom, width: loop.bars * zoom }} />}
                {track.clips.map((clip) => {
                  const d = drag?.clipId === clip.id ? drag : null;
                  const start = d?.mode === "move" ? Math.max(0, d.startBar + d.dBars) : clip.start_bar;
                  const bars = d?.mode === "resize" ? Math.max(1, d.bars + d.dBars) : clip.bars;
                  return <ClipBox key={clip.id} track={track} clip={clip} start={start} bars={bars} zoom={zoom} arr={arr}
                    selected={selection.clipId === clip.id} onDown={onClipDown} onMove={onClipMove} onUp={onClipUp} />;
                })}
                {drag && drag.mode === "move" && drag.trackId === track.id && !track.clips.some((c) => c.id === drag.clipId) && (() => {
                  const f = findClip(arr, drag.clipId);
                  return f ? <div className="pointer-events-none absolute top-1 bottom-1 rounded border border-dashed border-white/70"
                    style={{ left: Math.max(0, drag.startBar + drag.dBars) * zoom + 1, width: f.clip.bars * zoom - 2 }} /> : null;
                })()}
              </div>
            </div>
          ))}
          <div ref={playhead} className="pointer-events-none absolute top-0 bottom-0 z-20 w-px bg-white/80" data-testid="playhead" style={{ transform: `translateX(${HEADER}px)` }} />
        </div>
      </div>
      {sectionsOpen && <SectionsModal arr={arr} onClose={() => setSectionsOpen(false)} />}
    </div>
  );
}

function ClipBox({ track, clip, start, bars, zoom, arr, selected, onDown, onMove, onUp }: {
  track: Track; clip: Clip; start: number; bars: number; zoom: number; arr: Arrangement; selected: boolean;
  onDown: (e: RPointerEvent<HTMLDivElement>, t: Track, c: Clip, m: Drag["mode"]) => void;
  onMove: (e: RPointerEvent<HTMLDivElement>) => void; onUp: () => void;
}) {
  const pattern = arr.patterns[clip.pattern_id];
  const color = track.color || TRACK_COLORS[track.kind];
  const shared = pattern ? patternUsage(arr, pattern.id) > 1 : false;
  return (
    <div data-testid="clip" data-clip-id={clip.id} data-selected={selected}
      onPointerDown={(e) => onDown(e, track, clip, "move")} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
      className={cx("absolute top-1 bottom-1 cursor-grab touch-none overflow-hidden rounded border active:cursor-grabbing", selected ? "border-white" : "border-transparent")}
      style={{ left: start * zoom + 1, width: Math.max(4, bars * zoom - 2), background: `${color}33`, color }}>
      <div className="pointer-events-none truncate px-1 text-[10px] leading-3 font-medium text-ink-200">{shared ? "◆ " : ""}{pattern ? `${pattern.length_ticks / TICKS_PER_BAR} bar ${track.kind}` : "missing pattern"}</div>
      {pattern && <ClipPreview pattern={pattern} bars={bars} zoom={zoom} />}
      <div onPointerDown={(e) => onDown(e, track, clip, "resize")} onPointerMove={onMove} onPointerUp={onUp}
        className="absolute inset-y-0 right-0 w-1.5 cursor-ew-resize bg-white/0 hover:bg-white/40" title="Drag to resize" />
    </div>
  );
}
