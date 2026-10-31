import { useMutation } from "@tanstack/react-query";
import { Download, FolderOpen, PackageOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { api, urls } from "../api/client";
import { keys, queryClient, useCapabilities, useExports, useJobs } from "../api/hooks";
import type { ExportRequest, Job, Project } from "../api/types";
import { JobList } from "../components/JobProgress";
import { Badge, Button, Card, Empty, ErrorBanner, NumberField, Select, TextField, Toggle } from "../components/ui";
import { useStudio } from "../stores/studio";

const FORMATS = [
  { value: "wav24", label: "WAV 24-bit" },
  { value: "wav16", label: "WAV 16-bit" },
  { value: "wav32f", label: "WAV 32-bit float" },
  { value: "mp3", label: "MP3 320 kbps" },
] as const;

/** Persist pending studio edits so the export renders exactly what the user hears. */
export async function flushStudio(projectId: string) {
  const st = useStudio.getState();
  if (st.projectId === projectId && st.arr && st.save !== "saved") {
    st.setSave("saving");
    const saved = await api.saveArrangement(projectId, st.arr);
    st.setBaseline(JSON.stringify(saved));
    st.setSave("saved");
  }
}

export default function ExportTab({ project, jobs }: { project: Project; jobs: Job[] }) {
  const caps = useCapabilities();
  const exports = useExports(project.id);
  const track = useJobs((s) => s.track);
  const [req, setReq] = useState<ExportRequest>(() => ({ ...project.export_settings, filename: project.export_settings?.filename && project.export_settings.filename !== "beat" ? project.export_settings.filename : project.name.replace(/[^\w\- ]+/g, "").trim() || "beat" }));
  const bars = project.arrangement?.bars ?? 0;
  useEffect(() => {
    if (req.loop_start_bar >= bars && bars) setReq((r) => ({ ...r, loop_start_bar: 0 }));
  }, [bars, req.loop_start_bar]);
  const run = useMutation({
    mutationFn: async () => {
      await flushStudio(project.id);
      return api.exportProject(project.id, req);
    },
    onSuccess: (r) => {
      track(r.job);
      queryClient.invalidateQueries({ queryKey: keys.exports(project.id) });
    },
  });
  const reveal = useMutation({ mutationFn: (id: string) => api.reveal(id) });
  const set = (p: Partial<ExportRequest>) => setReq({ ...req, ...p });
  const mp3Missing = req.format === "mp3" && caps.data && !caps.data.ffmpeg;
  const running = jobs.some((j) => j.job_type === "export" && (j.status === "queued" || j.status === "running"));

  if (!project.arrangement) {
    return <div className="mx-auto max-w-4xl p-6"><Empty icon={<PackageOpen className="size-8" />} title="Nothing to export yet">Open a variation in the studio first.</Empty></div>;
  }
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5 p-6">
      <Card title="Export">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <TextField label="File name" value={req.filename} onChange={(v) => set({ filename: v })} maxLength={120} />
          <Select label="Format" value={req.format} onChange={(v) => set({ format: v })} options={[...FORMATS]} />
          <Select label="Range" value={req.scope} onChange={(v) => set({ scope: v })} options={[{ value: "full", label: `Full beat (${bars} bars)` }, { value: "loop", label: "Loop region" }]} />
          {req.scope === "loop" && (
            <>
              <NumberField label="Loop start bar" value={req.loop_start_bar + 1} min={1} max={bars} onChange={(v) => set({ loop_start_bar: Math.max(0, (v ?? 1) - 1) })} />
              <NumberField label="Loop length (bars)" value={req.loop_bars} min={1} max={64} onChange={(v) => set({ loop_bars: v ?? 4 })} />
            </>
          )}
        </div>
        <div className="mt-4 flex flex-wrap gap-6">
          <Toggle label="Also export stems (one file per track)" checked={req.stems} onChange={(v) => set({ stems: v })} />
          <Toggle label="Include project file (.json)" checked={req.project_json} onChange={(v) => set({ project_json: v })} />
        </div>
        {mp3Missing && <p className="mt-3 text-sm text-amber-400">MP3 export needs FFmpeg, which was not found. Choose WAV or install FFmpeg.</p>}
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" data-testid="export" busy={run.isPending} disabled={running || !req.filename.trim() || !!mp3Missing} onClick={() => run.mutate()}>
            <Download className="size-4" /> Export
          </Button>
          <span className="text-xs text-ink-400">Rendered offline at 48 kHz, mastered to {project.arrangement.master?.target_lufs ?? -14} LUFS with a −1 dBTP limiter.</span>
        </div>
        <div className="mt-3"><ErrorBanner error={run.error} /></div>
      </Card>
      <JobList jobs={jobs} types={["export"]} />

      <Card title="Previous exports">
        {!exports.data?.length ? <p className="text-sm text-ink-400">No exports yet.</p> : (
          <div className="flex flex-col divide-y divide-ink-700" data-testid="export-list">
            {exports.data.map((e) => (
              <div key={e.id} className="flex flex-col gap-1.5 py-3">
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-medium">{e.request.filename}</span>
                  <Badge>{e.request.format}</Badge>
                  <Badge tone={e.status === "completed" ? "good" : e.status === "failed" ? "bad" : "accent"}>{e.status}</Badge>
                  {e.metrics && <span className="text-xs text-ink-400">{e.metrics.lufs.toFixed(1)} LUFS · {e.metrics.true_peak_dbfs.toFixed(1)} dBTP</span>}
                  <span className="ml-auto text-xs text-ink-400">{new Date(e.created_at * 1000).toLocaleString()}</span>
                  {e.folder && caps.data && !caps.data.hosted && <Button size="sm" variant="ghost" onClick={() => reveal.mutate(e.id)}><FolderOpen className="size-3.5" /> Show folder</Button>}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {e.files?.map((f) => (
                    <a key={f.name} data-testid="download-link" href={urls.download(e.id, f.name)} download={f.name}
                      className="inline-flex items-center gap-1 rounded bg-ink-800 px-2 py-1 text-xs hover:bg-ink-700">
                      <Download className="size-3" /> {f.name}
                    </a>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <ErrorBanner error={reveal.error} />
      </Card>
    </div>
  );
}
