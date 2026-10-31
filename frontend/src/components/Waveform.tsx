import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin, { type Region } from "wavesurfer.js/dist/plugins/regions.esm.js";
import { usePreview } from "../audio/player";
import { formatTime } from "../lib/music";
import { IconButton } from "./ui";

interface Props {
  url: string;
  peaks?: number[];
  duration?: number | null;
  /** downbeat times (s) drawn as bar lines */
  markers?: number[];
  region?: { start: number; end: number } | null;
  onRegion?: (r: { start: number; end: number }) => void;
  height?: number;
  testId?: string;
}

/** Source waveform with bar markers and an optional draggable selection region (wavesurfer.js). */
export function Waveform({ url, peaks, duration, markers = [], region, onRegion, height = 112, testId }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const ws = useRef<WaveSurfer | null>(null);
  const regions = useRef<RegionsPlugin | null>(null);
  const onRegionRef = useRef(onRegion);
  onRegionRef.current = onRegion;
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(duration ?? 0);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!box.current) return;
    const reg = RegionsPlugin.create();
    const w = WaveSurfer.create({
      container: box.current,
      url,
      peaks: peaks?.length ? [peaks] : undefined,
      duration: peaks?.length && duration ? duration : undefined,
      height,
      waveColor: "#5b5b72",
      progressColor: "#f59e0b",
      cursorColor: "#fbbf24",
      barWidth: 2,
      barGap: 1,
      barRadius: 1,
      normalize: true,
      plugins: [reg],
    });
    ws.current = w;
    regions.current = reg;
    w.on("ready", () => {
      setReady(true);
      setDur(w.getDuration());
    });
    w.on("play", () => {
      setPlaying(true);
      usePreview.getState().stop(); // one thing at a time
    });
    w.on("pause", () => setPlaying(false));
    w.on("finish", () => setPlaying(false));
    w.on("timeupdate", (t) => setTime(t));
    if (onRegionRef.current) {
      reg.enableDragSelection({ color: "rgba(245,158,11,0.18)" });
      const emit = (r: Region) => {
        for (const other of reg.getRegions()) if (other !== r && other.id !== "marker") other.remove();
        onRegionRef.current?.({ start: r.start, end: r.end });
      };
      reg.on("region-created", emit);
      reg.on("region-updated", emit);
    }
    return () => {
      w.destroy();
      ws.current = null;
      setReady(false);
    };
  }, [url, peaks, duration, height]);

  // keep the selection in sync with props
  useEffect(() => {
    const reg = regions.current;
    if (!reg || !ready) return;
    const cur = reg.getRegions().find((r) => r.id === "sel" || r.end > r.start + 0.01);
    if (!region) {
      reg.getRegions().forEach((r) => r.remove());
      return;
    }
    if (cur && Math.abs(cur.start - region.start) < 1e-3 && Math.abs(cur.end - region.end) < 1e-3) return;
    reg.getRegions().forEach((r) => r.remove());
    reg.addRegion({ id: "sel", start: region.start, end: region.end, color: "rgba(245,158,11,0.18)", drag: !!onRegion, resize: !!onRegion });
  }, [region, ready, onRegion]);

  const total = dur || duration || 0;
  return (
    <div data-testid={testId} className="flex flex-col gap-2">
      <div className="relative rounded-md bg-ink-850 px-1">
        <div ref={box} />
        {total > 0 && (
          <div className="pointer-events-none absolute inset-0 mx-1">
            {markers.map((m, i) => (
              <div key={i} className="absolute top-0 h-2 w-px bg-ink-400/70" style={{ left: `${(m / total) * 100}%` }} />
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 text-xs text-ink-400">
        <IconButton label={playing ? "Pause source" : "Play source"} onClick={() => ws.current?.playPause()} disabled={!ready}>
          {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        </IconButton>
        <span className="tabular">{formatTime(time)} / {formatTime(total)}</span>
        {onRegion && <span className="ml-auto">Drag across the waveform to select a region.</span>}
      </div>
    </div>
  );
}
