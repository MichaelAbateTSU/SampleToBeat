import { useMutation } from "@tanstack/react-query";
import { Pencil, Plus, Scissors, Star, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { api, urls } from "../api/client";
import { keys, queryClient, useCapabilities, useSamples } from "../api/hooks";
import type { Job, Project, Sample, Transform } from "../api/types";
import { hashString } from "../audio/engine";
import { Peaks, PlayButton } from "../components/Audio";
import { JobList } from "../components/JobProgress";
import { Waveform } from "../components/Waveform";
import { Badge, Button, cx, Empty, ErrorBanner, IconButton, Modal, Select, Slider, Spinner, TextField, Toggle } from "../components/ui";
import { formatTime } from "../lib/music";
import { navigate, projectPath } from "../router";

type Filter = "all" | "favorites" | "loop" | "phrase" | "chop" | "manual";

export default function SamplesTab({ project, jobs }: { project: Project; jobs: Job[] }) {
  const samples = useSamples(project.id);
  const [filter, setFilter] = useState<Filter>("all");
  const [editing, setEditing] = useState<Sample | null>(null);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const bpm = project.target_bpm ?? project.sources?.[0]?.analysis?.bpm ?? null;

  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.samples(project.id) });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof api.updateSample>[1] }) => api.updateSample(id, body),
    onSuccess: refresh,
    onError: setErr,
  });
  const remove = useMutation({ mutationFn: (id: string) => api.deleteSample(id), onSuccess: refresh, onError: setErr });

  const list = useMemo(() => {
    const all = samples.data ?? [];
    if (filter === "all") return all;
    if (filter === "favorites") return all.filter((s) => s.favorite);
    if (filter === "manual") return all.filter((s) => s.origin === "manual");
    return all.filter((s) => s.kind === filter);
  }, [samples.data, filter]);

  const kinds = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of samples.data ?? []) c[s.kind] = (c[s.kind] ?? 0) + 1;
    return c;
  }, [samples.data]);

  const analyzed = (project.sources ?? []).some((s) => s.analysis);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center gap-2">
        {(["all", "favorites", "loop", "phrase", "chop", "manual"] as Filter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)} className={cx("rounded-full px-3 py-1 text-xs capitalize", filter === f ? "bg-accent text-ink-950" : "bg-ink-800 text-ink-300 hover:bg-ink-700")}>
            {f}{kinds[f] ? ` · ${kinds[f]}` : ""}
          </button>
        ))}
        <div className="ml-auto flex gap-2">
          <Button onClick={() => setCreating(true)} disabled={!analyzed} data-testid="new-sample"><Plus className="size-4" /> Select region manually</Button>
          <Button variant="primary" onClick={() => navigate(projectPath(project.id, "generate"))} disabled={!samples.data?.length}>Next: generate →</Button>
        </div>
      </div>
      <JobList jobs={jobs} types={["import", "analyze", "separate"]} />
      <ErrorBanner error={err ?? samples.error} onClose={err ? () => setErr(null) : undefined} />
      {samples.isLoading && <Spinner />}
      {samples.data && samples.data.length === 0 && (
        <Empty icon={<Scissors className="size-8" />} title="No samples yet">
          {analyzed ? "No usable regions were detected automatically. Select one manually." : "Import a source on the Source tab; candidate loops and chops appear here after analysis."}
        </Empty>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2" data-testid="sample-list">
        {list.map((s) => (
          <SampleCard key={s.id} s={s} bpm={bpm}
            onFavorite={() => update.mutate({ id: s.id, body: { favorite: !s.favorite } })}
            onEdit={() => setEditing(s)}
            onDelete={() => remove.mutate(s.id)} />
        ))}
      </div>
      {editing && <SampleEditor sample={editing} project={project} onClose={() => setEditing(null)} />}
      {creating && <RegionPicker project={project} onClose={() => setCreating(false)} />}
    </div>
  );
}

function SampleCard({ s, bpm, onFavorite, onEdit, onDelete }: { s: Sample; bpm: number | null; onFavorite: () => void; onEdit: () => void; onDelete: () => void }) {
  const v = hashString(JSON.stringify([s.transform, s.start, s.end]));
  return (
    <div data-testid="sample-card" className="flex flex-col gap-2 rounded-lg border border-ink-700 bg-ink-900 p-3">
      <div className="flex items-center gap-2">
        <PlayButton url={urls.sampleAudio(s.id, undefined, v)} label={s.name} testId="sample-play" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium" title={s.name}>{s.name}</div>
          <div className="tabular text-xs text-ink-400">{formatTime(s.start)}–{formatTime(s.end)} · {s.beats} beats · {s.source_bpm.toFixed(1)} BPM{s.key ? ` · ${s.key}` : ""}</div>
        </div>
        {s.score !== null && <Badge tone={s.score > 0.75 ? "good" : s.score > 0.5 ? "accent" : "neutral"} title="Overall usability score">{Math.round(s.score * 100)}</Badge>}
        <IconButton label={s.favorite ? "Unfavorite" : "Favorite"} active={s.favorite} onClick={onFavorite}><Star className={cx("size-4", s.favorite && "fill-current")} /></IconButton>
        <IconButton label="Edit sample" onClick={onEdit}><Pencil className="size-4" /></IconButton>
        <IconButton label="Delete sample" onClick={onDelete}><Trash2 className="size-4" /></IconButton>
      </div>
      <Peaks peaks={s.peaks ?? []} />
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="accent">{s.kind}</Badge>
        {s.stems.map((st) => <Badge key={st}>{st}</Badge>)}
        {s.origin === "manual" && <Badge>manual</Badge>}
        {s.explanation?.map((e) => <span key={e} className="text-[11px] text-ink-400">· {e}</span>)}
        {bpm && Math.abs(bpm - s.source_bpm) > 0.5 && (
          <span className="ml-auto flex items-center gap-1 text-xs text-ink-400">at {bpm.toFixed(0)} BPM <PlayButton url={urls.sampleAudio(s.id, bpm, v)} label={`${s.name} @ ${bpm.toFixed(0)}`} /></span>
        )}
      </div>
    </div>
  );
}

function SampleEditor({ sample, project, onClose }: { sample: Sample; project: Project; onClose: () => void }) {
  const caps = useCapabilities();
  const defaults = caps.data?.transform_defaults;
  const [name, setName] = useState(sample.name);
  const [t, setT] = useState<Transform | null>(null);
  const [region, setRegion] = useState({ start: sample.start, end: sample.end });
  const cur: Transform | null = t ?? (defaults ? { ...defaults, ...sample.transform } : null);
  const src = project.sources?.find((x) => x.id === sample.source_id);
  const save = useMutation({
    mutationFn: () => api.updateSample(sample.id, {
      name: name.trim() || sample.name,
      ...(t ? { transform: t } : {}),
      ...(Math.abs(region.start - sample.start) > 1e-3 || Math.abs(region.end - sample.end) > 1e-3 ? { start: region.start, end: region.end } : {}),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.samples(project.id) });
      onClose();
    },
  });
  const set = (p: Partial<Transform>) => cur && setT({ ...cur, ...p });
  return (
    <Modal wide title="Edit sample" onClose={onClose} footer={
      <>
        <Button variant="ghost" onClick={() => setT(defaults ? { ...defaults } : null)}>Reset transform</Button>
        <Button variant="primary" busy={save.isPending} onClick={() => save.mutate()}>Save</Button>
      </>
    }>
      <div className="flex flex-col gap-3">
        <TextField label="Name" value={name} onChange={setName} maxLength={80} />
        {src && (
          <div className="flex flex-col gap-1">
            <span className="text-xs text-ink-300">Region (drag the edges)</span>
            <Waveform url={urls.sourceAudio(src.id)} peaks={src.peaks} duration={src.duration_seconds} markers={src.analysis?.downbeats} region={region} onRegion={setRegion} height={64} />
          </div>
        )}
        {!cur ? <Spinner /> : (
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Slider label="Pitch" value={cur.pitch_semitones} min={-12} max={12} step={1} onChange={(v) => set({ pitch_semitones: v })} format={(v) => `${v > 0 ? "+" : ""}${v} st`} />
            <Slider label="Fine" value={cur.pitch_cents} min={-100} max={100} step={1} onChange={(v) => set({ pitch_cents: v })} format={(v) => `${v} ct`} />
            <Slider label="Gain" value={cur.gain_db} min={-24} max={12} step={0.5} onChange={(v) => set({ gain_db: v })} format={(v) => `${v} dB`} />
            <Select label="Pitch mode" value={cur.stretch_mode} onChange={(v) => set({ stretch_mode: v })}
              options={[{ value: "stretch", label: "Keep tempo (time-stretch)" }, { value: "tape", label: "Tape (speed + pitch)" }]} />
            <Slider label="Fade in" value={cur.fade_in_ms} min={0} max={500} step={1} onChange={(v) => set({ fade_in_ms: v })} format={(v) => `${v} ms`} />
            <Slider label="Fade out" value={cur.fade_out_ms} min={0} max={500} step={1} onChange={(v) => set({ fade_out_ms: v })} format={(v) => `${v} ms`} />
            <Toggle label="Reverse" checked={cur.reverse} onChange={(v) => set({ reverse: v })} />
            <Toggle label="Preserve formants" checked={cur.preserve_formants} onChange={(v) => set({ preserve_formants: v })} />
          </div>
        )}
        <ErrorBanner error={save.error} />
      </div>
    </Modal>
  );
}

function RegionPicker({ project, onClose }: { project: Project; onClose: () => void }) {
  const sources = (project.sources ?? []).filter((s) => s.analysis);
  const [srcId, setSrcId] = useState(sources[0]?.id ?? "");
  const src = sources.find((s) => s.id === srcId);
  const [region, setRegion] = useState<{ start: number; end: number } | null>(null);
  const [snap, setSnap] = useState(true);
  const [stem, setStem] = useState("mix");
  const [name, setName] = useState("");
  const snapTo = (t: number) => {
    const beats = src?.analysis?.beats ?? [];
    if (!snap || !beats.length) return t;
    return beats.reduce((b, x) => (Math.abs(x - t) < Math.abs(b - t) ? x : b), beats[0]);
  };
  const create = useMutation({
    mutationFn: () => {
      const start = snapTo(region!.start);
      let end = snapTo(region!.end);
      if (end <= start) end = region!.end;
      return api.createSample(project.id, { source_id: srcId, start, end, stems: [stem], ...(name.trim() ? { name: name.trim() } : {}) });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.samples(project.id) });
      onClose();
    },
  });
  return (
    <Modal wide title="New sample from region" onClose={onClose} footer={
      <>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" data-testid="create-sample" disabled={!region || region.end - region.start < 0.05} busy={create.isPending} onClick={() => create.mutate()}>Create sample</Button>
      </>
    }>
      <div className="flex flex-col gap-3">
        {sources.length > 1 && <Select label="Source" value={srcId} onChange={setSrcId} options={sources.map((s) => ({ value: s.id, label: s.original_filename }))} />}
        {src && <Waveform url={urls.sourceAudio(src.id)} peaks={src.peaks} duration={src.duration_seconds} markers={src.analysis?.downbeats} region={region} onRegion={setRegion} height={80} />}
        <div className="tabular text-xs text-ink-400">{region ? `${formatTime(region.start)} – ${formatTime(region.end)} (${(region.end - region.start).toFixed(2)} s)` : "No region selected"}</div>
        <div className="grid grid-cols-2 gap-3">
          <Select label="Audio" value={stem} onChange={setStem} options={[{ value: "mix", label: "Full mix" }, ...Object.keys(src?.stems ?? {}).map((s) => ({ value: s, label: `${s} stem` }))]} />
          <TextField label="Name (optional)" value={name} onChange={setName} maxLength={80} />
        </div>
        <Toggle label="Snap edges to beats" checked={snap} onChange={setSnap} />
        <ErrorBanner error={create.error} />
      </div>
    </Modal>
  );
}
