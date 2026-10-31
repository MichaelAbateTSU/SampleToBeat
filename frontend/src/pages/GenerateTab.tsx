import { Dices, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useCapabilities, useJobMutation, useSamples } from "../api/hooks";
import type { GenerateSettings, Genre, Job, Project } from "../api/types";
import { JobList } from "../components/JobProgress";
import { Button, Card, cx, ErrorBanner, NumberField, Select, Slider, Spinner, Toggle } from "../components/ui";
import { navigate, projectPath } from "../router";

const pct = (v: number) => `${Math.round(v * 100)}%`;

export default function GenerateTab({ project, jobs }: { project: Project; jobs: Job[] }) {
  const caps = useCapabilities();
  const samples = useSamples(project.id);
  const [s, setS] = useState<GenerateSettings | null>(null);
  useEffect(() => {
    if (!s && caps.data) setS({ ...caps.data.generate_defaults, ...project.generator_settings });
  }, [caps.data, project.generator_settings, s]);
  const gen = useJobMutation((body: GenerateSettings) => api.generate(project.id, body));
  const lastGen = jobs.find((j) => j.job_type === "generate");
  useEffect(() => {
    // jump to the results when this session's generation finishes
    if (gen.data && lastGen?.id === gen.data.id && lastGen.status === "completed") navigate(projectPath(project.id, "variations"));
  }, [gen.data, lastGen, project.id]);

  if (!caps.data || !s) return <div className="p-6"><Spinner /></div>;
  const g = caps.data.genres[s.genre];
  const set = (p: Partial<GenerateSettings>) => setS({ ...s, ...p });
  const running = jobs.some((j) => (j.job_type === "generate" || j.job_type === "regenerate") && (j.status === "running" || j.status === "queued"));
  const hasSource = (project.sources ?? []).some((x) => x.analysis);
  const manualOk = s.sample_mode === "auto" || s.sample_ids.length > 0;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card title="Style">
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Genre">
              {(Object.keys(caps.data.genres) as Genre[]).map((id) => (
                <button key={id} role="radio" aria-checked={s.genre === id} data-testid={`genre-${id}`} onClick={() => set({ genre: id })}
                  className={cx("rounded-md border px-3 py-2 text-left text-sm", s.genre === id ? "border-accent bg-accent-soft" : "border-ink-600 hover:border-ink-500")}>
                  <div className="font-medium">{caps.data!.genres[id].label}</div>
                  <div className="text-xs text-ink-400">{caps.data!.genres[id].bpm[0]}–{caps.data!.genres[id].bpm[1]} BPM</div>
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <NumberField label="Tempo (blank = auto from sample)" value={s.bpm} min={60} max={180} step={0.5} placeholder="auto" onChange={(v) => set({ bpm: v })} />
              <NumberField label={`Swing % (blank = ${g.swing[0]}–${g.swing[1]})`} value={s.swing} min={50} max={75} step={1} placeholder="auto" onChange={(v) => set({ swing: v })} />
              <NumberField label="Length (bars)" value={s.bars} min={4} max={128} onChange={(v) => set({ bars: v ?? 36 })} />
              <Select label="Structure" value={s.structure} onChange={(v) => set({ structure: v })} options={[
                { value: "auto", label: "Auto" }, { value: "standard", label: "Intro · verse · hook · outro" },
                { value: "verse_hook", label: "Verse / hook" }, { value: "loop", label: "Single loop" },
              ]} />
            </div>
          </div>
        </Card>

        <Card title="Feel">
          <div className="flex flex-col gap-4">
            <Slider label="Energy" value={s.energy} min={0} max={1} onChange={(v) => set({ energy: v })} format={pct} />
            <Slider label="Chop intensity" value={s.chop_intensity} min={0} max={1} onChange={(v) => set({ chop_intensity: v })} format={pct} hint="0 = loop flip, 100% = hard chops" />
            <Slider label="808 / bass intensity" value={s.bass_intensity} min={0} max={1} onChange={(v) => set({ bass_intensity: v })} format={pct} />
            <Slider label="Drum complexity" value={s.drum_complexity} min={0} max={1} onChange={(v) => set({ drum_complexity: v })} format={pct} />
            <div className="grid grid-cols-2 gap-3">
              <Slider label="Variations" value={s.variations} min={1} max={8} step={1} onChange={(v) => set({ variations: v })} />
              <div className="flex items-end gap-1">
                <NumberField label="Seed (blank = random)" className="flex-1" value={s.seed} min={0} max={2 ** 31} placeholder="random" onChange={(v) => set({ seed: v })} />
                <Button variant="ghost" title="Random seed" onClick={() => set({ seed: Math.floor(Math.random() * 2 ** 31) })}><Dices className="size-4" /></Button>
              </div>
            </div>
          </div>
        </Card>
      </div>

      <Card title="Samples">
        <div className="flex flex-col gap-3">
          <div className="flex gap-4">
            <Toggle label="Let ChopLab choose the best samples" checked={s.sample_mode === "auto"} onChange={(v) => set({ sample_mode: v ? "auto" : "manual" })} />
          </div>
          {s.sample_mode === "manual" && (
            <div className="grid max-h-64 grid-cols-1 gap-1 overflow-auto sm:grid-cols-2">
              {(samples.data ?? []).map((x) => {
                const on = s.sample_ids.includes(x.id);
                return (
                  <label key={x.id} className={cx("flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm", on ? "bg-accent-soft" : "hover:bg-ink-800")}>
                    <input type="checkbox" checked={on} disabled={!on && s.sample_ids.length >= 8}
                      onChange={() => set({ sample_ids: on ? s.sample_ids.filter((i) => i !== x.id) : [...s.sample_ids, x.id] })} />
                    <span className="truncate">{x.name}</span>
                  </label>
                );
              })}
              <p className="col-span-full text-xs text-ink-400">Pick up to 8. The first one is the main sample; others become secondary chops.</p>
            </div>
          )}
        </div>
      </Card>

      <JobList jobs={jobs} types={["generate"]} />
      <ErrorBanner error={gen.error} />
      <div className="flex items-center justify-end gap-3">
        {!hasSource && <span className="text-sm text-ink-400">Import and analyze a source first.</span>}
        <Button variant="primary" data-testid="generate" disabled={!hasSource || running || !manualOk} busy={gen.isPending}
          onClick={() => gen.mutate({ ...s, bpm: s.bpm || null, swing: s.swing || null })}>
          <Wand2 className="size-4" /> Generate {s.variations} variation{s.variations === 1 ? "" : "s"}
        </Button>
      </div>
    </div>
  );
}
