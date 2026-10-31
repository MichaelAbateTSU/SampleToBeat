import { RotateCcw, X } from "lucide-react";
import { api } from "../api/client";
import { useJobs } from "../api/hooks";
import type { Job } from "../api/types";
import { Badge, Button, Progress } from "./ui";

const LABEL: Record<string, string> = {
  import: "Import", analyze: "Analysis", separate: "Stem separation", generate: "Generation", regenerate: "Regeneration", export: "Export",
};

export function JobRow({ job, compact }: { job: Job; compact?: boolean }) {
  const track = useJobs((s) => s.track);
  const active = job.status === "queued" || job.status === "running";
  const tone = job.status === "completed" ? "good" : job.status === "failed" || job.status === "interrupted" ? "bad" : job.status === "canceled" ? "neutral" : "accent";
  return (
    <div data-testid={`job-${job.job_type}`} data-status={job.status} className="flex flex-col gap-1.5 rounded-md border border-ink-700 bg-ink-850 px-3 py-2">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium">{LABEL[job.job_type] ?? job.job_type}</span>
        <Badge tone={tone}>{job.status}</Badge>
        <span className="flex-1 truncate text-xs text-ink-400">{active ? job.current_stage : null}</span>
        {active && <span className="tabular text-xs text-ink-300">{Math.round(job.progress * 100)}%</span>}
        {active && (
          <Button size="sm" variant="ghost" onClick={() => api.cancelJob(job.id).then(track)} title="Cancel">
            <X className="size-3.5" /> {!compact && "Cancel"}
          </Button>
        )}
        {(job.status === "failed" || job.status === "interrupted" || job.status === "canceled") && (
          <Button size="sm" variant="ghost" onClick={() => api.retryJob(job.id).then(track)}>
            <RotateCcw className="size-3.5" /> Retry
          </Button>
        )}
      </div>
      {active && <Progress value={job.progress} />}
      {job.status === "failed" && job.error_message && <div className="text-xs text-red-400">{job.error_message}</div>}
    </div>
  );
}

/** Shows active jobs (and the most recent failure) of the given types. */
export function JobList({ jobs, types }: { jobs: Job[]; types: string[] }) {
  const relevant = jobs.filter((j) => types.includes(j.job_type));
  const active = relevant.filter((j) => j.status === "queued" || j.status === "running");
  const latest = relevant[0];
  const show = active.length ? active : latest && latest.status !== "completed" ? [latest] : [];
  if (!show.length) return null;
  return <div className="flex flex-col gap-2">{show.map((j) => <JobRow key={j.id} job={j} />)}</div>;
}
