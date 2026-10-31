// One shared <audio> element for rendered previews (variations, HQ renders, samples, stems): starting a
// new preview stops the previous one, and the UI shows a single now-playing bar.
import { create } from "zustand";

interface PreviewState {
  url: string | null;
  label: string;
  playing: boolean;
  time: number;
  duration: number;
  loop: boolean;
  error: string | null;
  play: (url: string, label: string, opts?: { loop?: boolean; startAt?: number }) => Promise<void>;
  toggle: () => void;
  stop: () => void;
  seek: (t: number) => void;
}

let el: HTMLAudioElement | null = null;

function audio(): HTMLAudioElement {
  if (!el) {
    el = new Audio();
    el.preload = "auto";
    el.addEventListener("timeupdate", () => usePreview.setState({ time: el!.currentTime }));
    el.addEventListener("durationchange", () => usePreview.setState({ duration: Number.isFinite(el!.duration) ? el!.duration : 0 }));
    el.addEventListener("play", () => usePreview.setState({ playing: true }));
    el.addEventListener("pause", () => usePreview.setState({ playing: false }));
    el.addEventListener("ended", () => usePreview.setState({ playing: false }));
    el.addEventListener("error", () => usePreview.setState({ playing: false, error: "Could not play this audio." }));
  }
  return el;
}

export const usePreview = create<PreviewState>((set, get) => ({
  url: null,
  label: "",
  playing: false,
  time: 0,
  duration: 0,
  loop: false,
  error: null,
  play: async (url, label, opts) => {
    const a = audio();
    if (get().url !== url) {
      a.src = url;
      set({ url, label, time: 0, duration: 0, error: null });
    }
    a.loop = !!opts?.loop;
    set({ loop: a.loop });
    if (opts?.startAt !== undefined) a.currentTime = opts.startAt;
    try {
      await a.play();
    } catch (e) {
      if ((e as Error).name !== "AbortError") set({ error: (e as Error).message, playing: false });
    }
  },
  toggle: () => {
    const a = audio();
    if (!get().url) return;
    if (a.paused) void a.play();
    else a.pause();
  },
  stop: () => {
    const a = audio();
    a.pause();
    a.currentTime = 0;
    set({ playing: false, time: 0 });
  },
  seek: (t) => {
    const a = audio();
    a.currentTime = Math.max(0, t);
  },
}));

export function isPlayingUrl(url: string) {
  const s = usePreview.getState();
  return s.url === url && s.playing;
}
