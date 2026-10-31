import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, FileAudio, Layers, Upload, Wand2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, urls } from "../api/client";
import { keys, queryClient, useCapabilities, useJobs, useModels } from "../api/hooks";
import type { Job, Project, Source } from "../api/types";
import { PlayButton } from "../components/Audio";
import { JobList } from "../components/JobProgress";
import { Waveform } from "../components/Waveform";
import { Badge, Button, Card, cx, Empty, ErrorBanner, NumberField, Select, Stat } from "../components/ui";
import { formatTime, KEYS } from "../lib/music";
import { navigate, projectPath } from "../router";

export default function SourceTab({ project, jobs }: { project: Project; jobs: Job[] }) {
  const sources = project.sources ?? [];
  const caps = useCapabilities();
  const [sel, setSel] = useState<string | null>(null);
  const track = useJobs((s) => s.track);
  const fileRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const current = sources.find((s) => s.id === sel) ?? sources[sources.length - 1];

  const onStarted = (r: { source: Source; job: Job }) => {
    track(r.job);
    setSel(r.source.id);
    queryClient.invalidateQueries({ queryKey: keys.project(project.id) });
    queryClient.invalidateQueries({ queryKey: keys.jobs(project.id) });
  };
  const upload = useMutation({ mutationFn: (f: File) => api.uploadSource(project.id, f), onSuccess: onStarted });
  const demo = useMutation({ mutationFn: () => api.demoSource(project.id), onSuccess: onStarted });

  const importJobs = jobs.filter((j) => ["import", "analyze", "separate"].includes(j.job_type));
  const working = importJobs.some((j) => j.status === "queued" || j.status === "running");

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 p-6"
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const f = e.dataTransfer.files[0];
        if (f) upload.mutate(f);
      }}>
      <input ref={fileRef} type="file" data-testid="source-file" accept="audio/*,.wav,.mp3,.flac,.ogg,.aiff,.aif,.m4a" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => fileRef.current?.click()} busy={upload.isPending}><Upload className="size-4" /> Import audio</Button>
        <Button data-testid="use-demo" onClick={() => demo.mutate()} busy={demo.isPending}><Wand2 className="size-4" /> Use demo sample</Button>
        {sources.length > 1 && (
          <div className="ml-auto flex gap-1">
            {sources.map((s) => (
              <button key={s.id} onClick={() => setSel(s.id)} className={cx("rounded px-2 py-1 text-xs", current?.id === s.id ? "bg-ink-600" : "bg-ink-800 text-ink-400")}>{s.original_filename}</button>
            ))}
          </div>
        )}
      </div>
      <ErrorBanner error={upload.error ?? demo.error} />
      {caps.data?.hosted && <p className="text-xs text-ink-400">Hosted imports: up to {caps.data.max_upload_mb} MiB and {Math.floor((caps.data.max_audio_seconds ?? 300) / 60)} minutes. Audio processing is shared; wait for running jobs before rendering HQ.</p>}
      <JobList jobs={jobs} types={["import", "analyze", "separate"]} />

      {!current ? (
        <div className={cx("rounded-lg transition-colors", drag && "bg-accent-soft")}>
          <Empty icon={<FileAudio className="size-8" />} title="Drop an audio file here">
            WAV, MP3, FLAC, OGG, AIFF or M4A up to {Math.floor((caps.data?.max_audio_seconds ?? 1200) / 60)} minutes. Only use audio you own or have permission to sample.
          </Empty>
        </div>
      ) : (
        <SourceView key={current.id} project={project} source={current} working={working} />
      )}
    </div>
  );
}

function SourceView({ project, source, working }: { project: Project; source: Source; working: boolean }) {
  const a = source.analysis;
  const track = useJobs((s) => s.track);
  const models = useModels();
  const [bpm, setBpm] = useState<number | null>(a?.bpm ?? null);
  const [key, setKey] = useState(a?.key ?? "");
  const [phase, setPhase] = useState(String(a?.downbeat_phase ?? 0));
  const [offset, setOffset] = useState<number | null>(a?.grid_offset ?? null);
  const [model, setModel] = useState("");
  useEffect(() => {
    setBpm(a?.bpm ?? null);
    setKey(a?.key ?? "");
    setPhase(String(a?.downbeat_phase ?? 0));
    setOffset(a?.grid_offset ?? null);
  }, [a?.bpm, a?.key, a?.downbeat_phase, a?.grid_offset]);
  useEffect(() => {
    if (!model && models.data) setModel((models.data.find((m) => m.available && m.id !== "builtin-hpss") ?? models.data.find((m) => m.available))?.id ?? "");
  }, [models.data, model]);

  const override = useMutation({
    mutationFn: () => api.overrideAnalysis(source.id, {
      ...(bpm && bpm !== a?.bpm ? { bpm } : {}),
      ...(key && key !== a?.key ? { key } : {}),
      ...(Number(phase) !== a?.downbeat_phase ? { downbeat_phase: Number(phase) } : {}),
      ...(offset !== null && offset !== a?.grid_offset ? { grid_offset: offset } : {}),
    }),
    onSuccess: track,
  });
  const reanalyze = useMutation({ mutationFn: () => api.reanalyze(source.id), onSuccess: track });
  const separate = useMutation({ mutationFn: () => api.separate(source.id, model), onSuccess: track });

  const stems = Object.keys(source.stems ?? {});
  const dirty = !!a && ((bpm !== null && bpm !== a.bpm) || (key !== "" && key !== a.key) || Number(phase) !== a.downbeat_phase || (offset !== null && offset !== a.grid_offset));

  return (
    <div className="flex flex-col gap-5">
      <Card title={<span className="flex items-center gap-2">{source.original_filename} <Badge>{source.analysis_status}</Badge></span>}
        actions={source.duration_seconds ? <span className="tabular text-xs text-ink-400">{formatTime(source.duration_seconds)} · {source.sample_rate} Hz</span> : null}>
        <Waveform testId="source-waveform" url={urls.sourceAudio(source.id)} peaks={source.peaks} duration={source.duration_seconds} markers={a?.downbeats} />
        {source.analysis_status === "failed" && <ErrorBanner error={source.error ?? "Analysis failed"} />}
      </Card>

      {a && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <Card title="Analysis" actions={<Button size="sm" variant="ghost" onClick={() => reanalyze.mutate()} disabled={working}>Re-analyze</Button>}>
            <div className="grid grid-cols-3 gap-4" data-testid="analysis">
              <Stat label="Tempo" value={<span data-testid="analysis-bpm">{a.bpm.toFixed(2)} BPM</span>} hint={`detected ${a.detected_bpm}`} />
              <Stat label="Key" value={<span data-testid="analysis-key">{a.key ?? "unknown"}</span>} hint={`confidence ${(a.key_confidence * 100).toFixed(0)}%`} />
              <Stat label="Tempo stability" value={`${Math.round(a.tempo_stability * 100)}%`} />
              <Stat label="Loudness" value={`${a.loudness_lufs.toFixed(1)} LUFS`} />
              <Stat label="Peak" value={`${a.peak_dbfs.toFixed(1)} dBFS`} />
              <Stat label="Bars found" value={a.downbeats.length} />
            </div>
            {a.weak_grid && (
              <div className="mt-3 flex items-center gap-2 rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                <AlertTriangle className="size-4" /> The beat grid is uncertain (free tempo or sparse rhythm). Check the BPM and downbeat below.
              </div>
            )}
            <div className="mt-4 flex flex-col gap-2 text-xs">
              <div className="text-ink-400">Tempo interpretations</div>
              <div className="flex flex-wrap gap-1.5">
                {a.bpm_candidates.map((c) => (
                  <button key={c.bpm} onClick={() => setBpm(c.bpm)} className={cx("rounded border px-2 py-1", Math.abs(c.bpm - (bpm ?? 0)) < 0.01 ? "border-accent text-accent" : "border-ink-600 text-ink-300")}>
                    {c.bpm.toFixed(1)} <span className="text-ink-400">{c.interpretation}</span>
                  </button>
                ))}
              </div>
              <div className="mt-2 text-ink-400">Key candidates</div>
              <div className="flex flex-wrap gap-1.5">
                {a.key_candidates.slice(0, 5).map((c) => (
                  <button key={c.key} onClick={() => setKey(c.key)} className={cx("rounded border px-2 py-1", c.key === key ? "border-accent text-accent" : "border-ink-600 text-ink-300")}>
                    {c.key} <span className="text-ink-400">{(c.score * 100).toFixed(0)}</span>
                  </button>
                ))}
              </div>
            </div>
          </Card>

          <Card title="Correct the analysis">
            <div className="grid grid-cols-2 gap-3">
              <NumberField label="BPM" value={bpm} min={40} max={220} step={0.01} onChange={setBpm} />
              <Select label="Key" value={key} onChange={setKey} options={[{ value: "", label: "—" }, ...KEYS.map((k) => ({ value: k, label: k }))]} />
              <Select label="Downbeat (which beat is ‘1’)" value={phase} onChange={setPhase} options={[0, 1, 2, 3].map((i) => ({ value: String(i), label: `beat ${i + 1} of detected grid` }))} />
              <NumberField label="Grid offset (s)" value={offset} min={0} max={30} step={0.001} onChange={setOffset} />
            </div>
            <div className="mt-4 flex items-center gap-2">
              <Button variant="primary" disabled={!dirty || working} busy={override.isPending} onClick={() => override.mutate()}>Apply corrections</Button>
              <span className="text-xs text-ink-400">Re-runs sample detection with your grid.</span>
            </div>
            <ErrorBanner error={override.error ?? reanalyze.error} />
          </Card>
        </div>
      )}

      {a && (
        <Card title={<span className="flex items-center gap-2"><Layers className="size-4" /> Stems</span>} actions={source.separation_status ? <Badge>{source.separation_status}</Badge> : null}>
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Model" value={model} onChange={setModel} className="w-72"
              options={(models.data ?? []).map((m) => ({ value: m.id, label: `${m.label}${m.available ? "" : " (not installed)"}` }))} />
            <Button data-testid="separate" onClick={() => separate.mutate()} disabled={!model || working || !models.data?.find((m) => m.id === model)?.available} busy={separate.isPending}>Separate stems</Button>
          </div>
          <p className="mt-2 text-xs text-ink-400">Separation is optional — samples can be cut from the full mix. Demucs is higher quality but slow on CPU; the built-in HPSS splits harmonic and percussive parts instantly.</p>
          <ErrorBanner error={separate.error} />
          {stems.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {stems.map((st) => (
                <div key={st} className="flex items-center gap-1 rounded-md border border-ink-700 bg-ink-850 py-0.5 pr-2 pl-0.5 text-sm">
                  <PlayButton url={urls.stemAudio(source.id, st)} label={`${st} stem`} /> {st}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {a && (
        <div className="flex justify-end">
          <Button variant="primary" onClick={() => navigate(projectPath(project.id, "samples"))}>Next: review samples →</Button>
        </div>
      )}
    </div>
  );
}
