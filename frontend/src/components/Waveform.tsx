import { useEffect, useRef } from "react";

interface Props {
  peaks?: number[];
  progress?: number; // 0..1
  height?: number;
  onSeek?: (fraction: number) => void;
  color?: string;
  bars?: number;
}

/** Bar-style waveform drawn from precomputed peaks; the played part lights up like an LCD. */
export function Waveform({ peaks, progress = 0, height = 48, onSeek, color, bars }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (!w || !h) return;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);
      const css = getComputedStyle(canvas);
      const idle = css.getPropertyValue("--wave-idle").trim() || "#5b5e64";
      const lit = css.getPropertyValue("--wave-on").trim() || "#ffb52e";
      const n = Math.max(8, Math.min(bars ?? Math.floor(w / 3), peaks?.length || 64));
      const step = w / n;
      const bw = Math.max(1, step * 0.62);
      const max = peaks?.length ? Math.max(0.05, ...peaks) : 1;
      for (let i = 0; i < n; i++) {
        let v = 0.12;
        if (peaks?.length) {
          const a = Math.floor((i / n) * peaks.length);
          const b = Math.max(a + 1, Math.floor(((i + 1) / n) * peaks.length));
          for (let j = a; j < b; j++) v = Math.max(v, peaks[j] / max);
        }
        const bh = Math.max(2, v * (h - 2));
        ctx.fillStyle = (i + 0.5) / n <= progress ? lit : color ?? idle;
        ctx.beginPath();
        ctx.roundRect(i * step + (step - bw) / 2, (h - bh) / 2, bw, bh, bw / 2);
        ctx.fill();
      }
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [peaks, progress, color, bars]);

  return (
    <canvas
      ref={ref}
      className={`waveform${onSeek ? " seekable" : ""}`}
      aria-hidden="true"
      style={{ height }}
      onClick={(e) => {
        if (!onSeek) return;
        const r = e.currentTarget.getBoundingClientRect();
        onSeek(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)));
      }}
    />
  );
}
