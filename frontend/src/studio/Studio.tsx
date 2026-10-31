import { LayoutGrid, Loader2, SlidersVertical } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { keys, queryClient, useSamples } from "../api/hooks";
import type { Project, Sample } from "../api/types";
import { engine } from "../audio/engine";
import { usePreview } from "../audio/player";
import { Button, cx, Empty, ErrorBanner } from "../components/ui";
import { deleteClip, duplicateClip, findClip, splitClip } from "../lib/arrangement";
import { secondsToTicks, TICKS_PER_BAR } from "../lib/music";
import { navigate, projectPath } from "../router";
import { useStudio } from "../stores/studio";
import { useEngineState } from "./hooks";
import Mixer from "./Mixer";
import PatternEditor from "./PatternEditor";
import Timeline from "./Timeline";
import Transport, { type LoopState } from "./Transport";

const AUTOSAVE_MS = 1200;

export default function Studio({ project }: { project: Project }) {
  const arr = useStudio((s) => s.arr);
  const loaded = useStudio((s) => s.projectId === project.id);
  const revision = useStudio((s) => s.revision);
  const lastError = useStudio((s) => s.lastError);
  const clearError = useStudio((s) => s.clearError);
  const samplesQ = useSamples(project.id);
  const status = useEngineState();
  const [panel, setPanel] = useState<"editor" | "mixer">("editor");
  const [loop, setLoop] = useState<LoopState>({ on: false, start: 0, bars: 4 });
  // fit the whole song on screen to start with (within sane limits)
  const [zoom, setZoom] = useState(() => {
    const bars = project.arrangement?.bars ?? 32;
    return Math.max(14, Math.min(80, (window.innerWidth - 240) / bars));
  });

  // load the server arrangement unless it's the copy we already hold (or we have unsaved edits)
  useEffect(() => {
    const st = useStudio.getState();
    if (!project.arrangement) return;
    if (st.projectId === project.id && st.save !== "saved") return;
    if (st.projectId === project.id && st.baseline === JSON.stringify(project.arrangement)) return;
    st.load(project.id, project.arrangement);
  }, [project.id, project.arrangement]);

  const samples = useMemo(() => Object.fromEntries((samplesQ.data ?? []).map((s) => [s.id, s])) as Record<string, Sample>, [samplesQ.data]);

  // keep the audio engine in sync with the document (debounced so slider drags don't re-render every frame)
  useEffect(() => {
    if (!arr || !loaded || !samplesQ.data) return;
    engine.setSamples(samples);
    const t = window.setTimeout(() => void engine.sync(arr, samples), 60);
    return () => clearTimeout(t);
  }, [arr, samples, loaded, samplesQ.data]);

  useEffect(() => {
    engine.setLoop(loop.on ? loop.start : null, loop.bars);
  }, [loop, arr?.bars, arr?.bpm]);

  // stop audio when leaving the studio
  useEffect(() => () => engine.stop(), []);

  // autosave
  const saveNow = useCallback(async () => {
    const st = useStudio.getState();
    if (!st.arr || st.projectId !== project.id || st.save === "saved") return;
    const doc = st.arr;
    st.setSave("saving");
    try {
      const saved = await api.saveArrangement(project.id, doc);
      useStudio.getState().setBaseline(JSON.stringify(saved));
      // only mark saved if nothing changed while the request was in flight
      if (useStudio.getState().arr === doc) useStudio.getState().setSave("saved");
      else useStudio.getState().setSave("dirty");
      queryClient.setQueryData(keys.project(project.id), (old: Project | undefined) => (old ? { ...old, arrangement: saved } : old));
      queryClient.invalidateQueries({ queryKey: keys.projects });
    } catch (e) {
      useStudio.getState().setSave("error", (e as Error).message);
    }
  }, [project.id]);
  useEffect(() => {
    if (!revision) return;
    const t = window.setTimeout(saveNow, AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [revision, saveNow]);
  const saveRef = useRef(saveNow);
  saveRef.current = saveNow;
  useEffect(() => {
    const before = (e: BeforeUnloadEvent) => {
      if (useStudio.getState().save !== "saved") {
        void saveRef.current();
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", before);
    return () => {
      window.removeEventListener("beforeunload", before);
      void saveRef.current();
    };
  }, []);

  // keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest("input, textarea, select, [contenteditable=true]")) return;
      const st = useStudio.getState();
      const mod = e.ctrlKey || e.metaKey;
      if (e.code === "Space") {
        e.preventDefault();
        usePreview.getState().stop();
        if (engine.isPlaying) engine.pause();
        else void engine.play();
      } else if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) st.redo();
        else st.undo();
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        st.redo();
      } else if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void saveRef.current();
      } else if (mod && e.key.toLowerCase() === "d" && st.selection.clipId) {
        e.preventDefault();
        st.apply("Duplicate clip", (a) => duplicateClip(a, st.selection.clipId!));
      } else if ((e.key === "Delete" || e.key === "Backspace") && st.selection.clipId && !e.defaultPrevented) {
        e.preventDefault();
        if (st.apply("Delete clip", (a) => deleteClip(a, st.selection.clipId!))) st.select({ clipId: null });
      } else if (e.key.toLowerCase() === "s" && !mod && st.selection.clipId && st.arr) {
        const at = Math.round(secondsToTicks(engine.position, st.arr.bpm) / TICKS_PER_BAR);
        const hit = findClip(st.arr, st.selection.clipId);
        if (hit && at > hit.clip.start_bar && at < hit.clip.start_bar + hit.clip.bars) st.apply("Split clip", (a) => splitClip(a, st.selection.clipId!, at));
      } else if (e.key === "Home") {
        engine.seek(0);
      } else if (e.key.toLowerCase() === "l" && !mod) {
        setLoop((l) => ({ ...l, on: !l.on }));
      } else if (e.key.toLowerCase() === "m" && !mod && st.selection.trackId) {
        st.apply("Mute", (a) => ({ ...a, tracks: a.tracks.map((t) => (t.id === st.selection.trackId ? { ...t, muted: !t.muted } : t)) }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!project.arrangement) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <Empty icon={<LayoutGrid className="size-8" />} title="No beat in the studio yet">
          Generate variations and choose one with “Open in studio”.
          <div className="mt-3"><Button variant="primary" onClick={() => navigate(projectPath(project.id, "variations"))}>Go to variations</Button></div>
        </Empty>
      </div>
    );
  }
  if (!arr || !loaded) return null;

  return (
    <div className="flex h-full min-h-[560px] flex-col" data-testid="studio">
      <Transport project={project} loop={loop} setLoop={setLoop} zoom={zoom} setZoom={setZoom} onSave={saveNow} />
      {(lastError || status.error || status.missing.length > 0) && (
        <div className="flex flex-col gap-1 px-3 pt-2">
          {lastError && <ErrorBanner error={lastError} onClose={clearError} />}
          {status.error && <ErrorBanner error={`Audio engine: ${status.error}`} />}
          {status.missing.length > 0 && <ErrorBanner error={`Missing samples (deleted?): ${status.missing.join(", ")} — those chops are silent.`} />}
        </div>
      )}
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        <Timeline zoom={zoom} loop={loop} setLoop={setLoop} />
        {status.loading && (
          <div className="pointer-events-none absolute top-2 right-3 flex items-center gap-1.5 rounded bg-ink-800/90 px-2 py-1 text-xs text-ink-300">
            <Loader2 className="size-3.5 animate-spin" /> preparing audio…
          </div>
        )}
      </div>
      <div className="flex h-[42%] min-h-64 flex-col border-t border-ink-600 bg-ink-900">
        <div className="flex items-center gap-1 border-b border-ink-700 px-2">
          {([["editor", "Pattern editor", LayoutGrid], ["mixer", "Mixer", SlidersVertical]] as const).map(([id, label, Icon]) => (
            <button key={id} data-testid={`panel-${id}`} onClick={() => setPanel(id)}
              className={cx("flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-xs", panel === id ? "border-accent text-ink-200" : "border-transparent text-ink-400")}>
              <Icon className="size-3.5" /> {label}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-3">
          {panel === "editor" ? <PatternEditor samples={samples} /> : <Mixer />}
        </div>
      </div>
    </div>
  );
}
