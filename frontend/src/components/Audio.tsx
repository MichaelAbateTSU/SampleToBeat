import { Pause, Play, Square } from "lucide-react";
import { formatTime } from "../lib/music";
import { usePreview } from "../audio/player";
import { IconButton } from "./ui";

/** Small peaks sparkline, used for sample cards. */
export function Peaks({ peaks, height = 36, color = "#f59e0b", progress }: { peaks: number[]; height?: number; color?: string; progress?: number }) {
  if (!peaks.length) return <div style={{ height }} />;
  const w = peaks.length;
  const max = Math.max(1e-6, ...peaks);
  const d = peaks.map((p, i) => `M${i + 0.5},${height / 2 - ((p / max) * height) / 2}V${height / 2 + ((p / max) * height) / 2}`).join("");
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      <path d={d} stroke={color} strokeWidth={0.7} opacity={0.85} />
      {progress !== undefined && progress > 0 && <rect x={0} y={0} width={w * progress} height={height} fill="white" opacity={0.08} />}
    </svg>
  );
}

/** Play/pause button bound to the shared preview player. */
export function PlayButton({ url, label, testId, loop }: { url: string; label: string; testId?: string; loop?: boolean }) {
  const cur = usePreview((s) => s.url);
  const playing = usePreview((s) => s.playing);
  const play = usePreview((s) => s.play);
  const toggle = usePreview((s) => s.toggle);
  const mine = cur === url;
  return (
    <IconButton data-testid={testId} label={mine && playing ? `Pause ${label}` : `Play ${label}`} active={mine && playing}
      onClick={() => (mine ? toggle() : void play(url, label, { loop }))}>
      {mine && playing ? <Pause className="size-4" /> : <Play className="size-4" />}
    </IconButton>
  );
}

/** Persistent bar showing the current preview, with a scrubber. */
export function NowPlaying() {
  const { url, label, playing, time, duration, toggle, stop, seek, error } = usePreview();
  if (!url) return null;
  return (
    <div data-testid="now-playing" className="flex items-center gap-3 border-t border-ink-700 bg-ink-900 px-4 py-2">
      <IconButton label={playing ? "Pause" : "Play"} onClick={toggle}>{playing ? <Pause className="size-4" /> : <Play className="size-4" />}</IconButton>
      <IconButton label="Stop" onClick={stop}><Square className="size-3.5" /></IconButton>
      <div className="w-56 truncate text-sm">{label}</div>
      <span className="tabular text-xs text-ink-400">{formatTime(time)}</span>
      <input aria-label="Seek" type="range" min={0} max={duration || 1} step={0.01} value={time} onChange={(e) => seek(Number(e.target.value))} className="flex-1" />
      <span className="tabular text-xs text-ink-400">{formatTime(duration)}</span>
      {error && <span className="text-xs text-red-400">{error}</span>}
    </div>
  );
}
