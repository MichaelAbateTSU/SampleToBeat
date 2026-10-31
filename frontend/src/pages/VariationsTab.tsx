import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, ListMusic, RefreshCw, SlidersHorizontal, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { api, urls } from "../api/client";
import { keys, queryClient, useJobs, useVariations } from "../api/hooks";
import type { Job, Project, Variation } from "../api/types";
import { PlayButton } from "../components/Audio";
import { JobList } from "../components/JobProgress";
import { Badge, Button, Empty, ErrorBanner, IconButton, Modal, Spinner, Stat } from "../components/ui";
import { formatTime } from "../lib/music";
import { navigate, projectPath } from "../router";

export default function VariationsTab({ project, jobs }: { project: Project; jobs: Job[] }) {
  const vars = useVariations(project.id);
  const track = useJobs((s) => s.track);
  const [confirm, setConfirm] = useState<Variation | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.variations(project.id) });

  const regen = useMutation({ mutationFn: (id: string) => api.regenerate(id), onSuccess: track, onError: setErr });
  const remove = useMutation({ mutationFn: (id: string) => api.deleteVariation(id), onSuccess: refresh, onError: setErr });
  const open = useMutation({
    mutationFn: (id: string) => api.openInStudio(id),
    onSuccess: (p) => {
      queryClient.setQueryData(keys.project(project.id), (old: Project | undefined) => (old ? { ...old, ...p, sources: old.sources } : p));
      queryClient.invalidateQueries({ queryKey: keys.project(project.id) });
      navigate(projectPath(project.id, "studio"));
    },
    onError: setErr,
  });
  const openOrConfirm = (v: Variation) => (project.arrangement ? setConfirm(v) : open.mutate(v.id));

  const list = vars.data ?? [];
  const batches = [...new Set(list.map((v) => v.batch))].sort((a, b) => b - a);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-6">
      <JobList jobs={jobs} types={["generate", "regenerate"]} />
      <ErrorBanner error={err ?? vars.error} onClose={err ? () => setErr(null) : undefined} />
      {vars.isLoading && <Spinner />}
      {vars.data && list.length === 0 && (
        <Empty icon={<ListMusic className="size-8" />} title="No variations yet">
          Generate beats on the Generate tab. Each variation flips the sample differently.
        </Empty>
      )}
      {batches.map((b) => (
        <div key={b} className="flex flex-col gap-2">
          <h2 className="text-xs font-semibold tracking-wide text-ink-400 uppercase">Batch {b}</h2>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {list.filter((v) => v.batch === b).map((v, i) => (
              <VariationCard key={v.id} v={v} index={i + 1} busy={open.isPending && open.variables === v.id}
                onOpen={() => openOrConfirm(v)} onRegenerate={() => regen.mutate(v.id)} onDelete={() => remove.mutate(v.id)} />
            ))}
          </div>
        </div>
      ))}
      {confirm && (
        <Modal title="Replace the studio arrangement?" onClose={() => setConfirm(null)} footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
            <Button variant="primary" busy={open.isPending} data-testid="confirm-open" onClick={() => { open.mutate(confirm.id); setConfirm(null); }}>Replace</Button>
          </>
        }>
          <p className="text-sm">Your current studio edits will be replaced by this variation. Export first if you want to keep them.</p>
        </Modal>
      )}
    </div>
  );
}

function VariationCard({ v, index, busy, onOpen, onRegenerate, onDelete }: { v: Variation; index: number; busy: boolean; onOpen: () => void; onRegenerate: () => void; onDelete: () => void }) {
  const [details, setDetails] = useState(false);
  const failed = v.evaluation?.checks?.filter((c) => !c.ok) ?? [];
  return (
    <div data-testid="variation-card" className="flex flex-col gap-3 rounded-lg border border-ink-700 bg-ink-900 p-4">
      <div className="flex items-center gap-2">
        <PlayButton url={urls.variationAudio(v.id, v.updated_at)} label={`Variation ${index} · ${v.strategy}`} testId="variation-play" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">Variation {index} · <span className="capitalize">{v.strategy.replace("_", " ")}</span></div>
          <div className="truncate text-xs text-ink-400">{v.meta.description ?? v.meta.strategy_detail}</div>
        </div>
        {v.evaluation?.passed ? <Badge tone="good"><CheckCircle2 className="size-3" /> checks passed</Badge> : <Badge tone="warn" title={failed.map((c) => c.detail).join("\n")}><XCircle className="size-3" /> {failed.length} warning{failed.length === 1 ? "" : "s"}</Badge>}
      </div>
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        <Stat label="Tempo" value={`${v.bpm.toFixed(1)}`} />
        <Stat label="Key" value={v.key ?? "—"} />
        <Stat label="Length" value={`${v.bars} bars`} hint={formatTime(v.metrics.duration_s)} />
        <Stat label="Genre" value={<span className="capitalize">{v.genre}</span>} />
        <Stat label="Loudness" value={`${v.metrics.lufs.toFixed(1)}`} hint="LUFS integrated" />
        <Stat label="Peak" value={`${v.metrics.true_peak_dbfs.toFixed(1)}`} hint="dBTP" />
      </div>
      <div className="flex flex-wrap gap-1">
        {v.sections.map((s, i) => <Badge key={i}>{s.name} · {s.bars}</Badge>)}
      </div>
      {details && (
        <ul className="flex flex-col gap-0.5 text-xs">
          {v.evaluation?.checks?.map((c) => (
            <li key={c.name} className={c.ok ? "text-ink-400" : "text-amber-400"}>{c.ok ? "✓" : "!"} {c.name}: {c.detail}</li>
          ))}
          <li className="text-ink-500">seed {v.seed}</li>
        </ul>
      )}
      <div className="flex items-center gap-1">
        <Button variant="primary" size="sm" data-testid="open-in-studio" busy={busy} onClick={onOpen}><SlidersHorizontal className="size-3.5" /> Open in studio</Button>
        <Button size="sm" variant="ghost" onClick={onRegenerate}><RefreshCw className="size-3.5" /> Regenerate</Button>
        <Button size="sm" variant="ghost" onClick={() => setDetails(!details)}>{details ? "Hide" : "Quality"} details</Button>
        <IconButton className="ml-auto" label="Delete variation" onClick={onDelete}><Trash2 className="size-4" /></IconButton>
      </div>
    </div>
  );
}
