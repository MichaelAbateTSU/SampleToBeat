import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Icon } from "./components/Icon";
import { clock as fmt, LcdText } from "./components/Lcd";
import { Waveform } from "./components/Waveform";

export interface PlayerTrack {
  id: string;
  url: string;
  title: string;
  subtitle?: string;
  peaks?: number[];
  duration?: number;
}

interface Player {
  track: PlayerTrack | null;
  playing: boolean;
  time: number;
  duration: number;
  /** Plays the track, or toggles pause if it is already the current one. */
  toggle: (track: PlayerTrack) => void;
  play: (track: PlayerTrack) => void;
  pause: () => void;
  seek: (seconds: number) => void;
  isPlaying: (id: string) => boolean;
}

const Ctx = createContext<Player | null>(null);

export function usePlayer(): Player {
  const p = useContext(Ctx);
  if (!p) throw new Error("usePlayer outside PlayerProvider");
  return p;
}


export function PlayerProvider({ children }: { children: ReactNode }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [track, setTrack] = useState<PlayerTrack | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    const a = new Audio();
    a.preload = "auto";
    audio.current = a;
    let raf = 0;
    const tick = () => {
      setTime(a.currentTime);
      raf = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      setPlaying(true);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };
    const onPause = () => {
      setPlaying(false);
      cancelAnimationFrame(raf);
      setTime(a.currentTime);
    };
    const onMeta = () => setDuration(Number.isFinite(a.duration) ? a.duration : 0);
    a.addEventListener("play", onPlay);
    a.addEventListener("pause", onPause);
    a.addEventListener("ended", onPause);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("durationchange", onMeta);
    return () => {
      cancelAnimationFrame(raf);
      a.pause();
      a.src = "";
    };
  }, []);

  const play = useCallback((t: PlayerTrack) => {
    const a = audio.current;
    if (!a) return;
    if (a.getAttribute("data-url") !== t.url) {
      a.src = t.url;
      a.setAttribute("data-url", t.url);
      setTime(0);
      setDuration(t.duration ?? 0);
    }
    setTrack(t);
    a.play().catch(() => setPlaying(false));
  }, []);

  const pause = useCallback(() => audio.current?.pause(), []);

  const toggle = useCallback(
    (t: PlayerTrack) => {
      const a = audio.current;
      if (a && a.getAttribute("data-url") === t.url && !a.paused) a.pause();
      else play(t);
    },
    [play],
  );

  const seek = useCallback((seconds: number) => {
    const a = audio.current;
    if (!a) return;
    a.currentTime = Math.max(0, seconds);
    setTime(a.currentTime);
  }, []);

  const isPlaying = useCallback((id: string) => playing && track?.id === id, [playing, track]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.code !== "Space" || !track || el.closest("input, textarea, select, button, a, [role=slider]")) return;
      e.preventDefault();
      toggle(track);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [track, toggle]);

  const value = useMemo(() => ({ track, playing, time, duration, toggle, play, pause, seek, isPlaying }), [track, playing, time, duration, toggle, play, pause, seek, isPlaying]);
  const total = duration || track?.duration || 0;

  return (
    <Ctx.Provider value={value}>
      {children}
      {track && (
        <div className="playerbar" role="region" aria-label="Player">
          <button className="play-btn" onClick={() => toggle(track)} aria-label={playing ? "Pause" : "Play"}>
            <Icon name={playing ? "pause" : "play"} />
          </button>
          <div className="playerbar-meta">
            <div className="playerbar-title">{track.title}</div>
            {track.subtitle && <div className="playerbar-sub">{track.subtitle}</div>}
          </div>
          <span className="lcd playerbar-time" aria-label="Elapsed"><LcdText>{fmt(time)}</LcdText></span>
          <div className="playerbar-wave">
            <Waveform peaks={track.peaks} progress={total ? time / total : 0} onSeek={(f) => seek(f * total)} height={36} />
          </div>
          <span className="lcd lcd-dim playerbar-time total" aria-label="Length"><LcdText>{fmt(total)}</LcdText></span>
        </div>
      )}
    </Ctx.Provider>
  );
}
