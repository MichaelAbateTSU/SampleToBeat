import { AudioLines, Download, LayoutGrid, ListMusic, Scissors, Sparkles, Wand2 } from "lucide-react";
import { lazy, Suspense } from "react";
import { useProject, useProjectJobs } from "../api/hooks";
import { cx, ErrorBanner, Spinner } from "../components/ui";
import { projectPath } from "../router";
import ExportTab from "./ExportTab";
import GenerateTab from "./GenerateTab";
import SamplesTab from "./SamplesTab";
import SourceTab from "./SourceTab";
import VariationsTab from "./VariationsTab";

const Studio = lazy(() => import("../studio/Studio"));

const TABS = [
  { id: "source", label: "Source & analysis", icon: AudioLines },
  { id: "samples", label: "Samples", icon: Scissors },
  { id: "generate", label: "Generate", icon: Wand2 },
  { id: "variations", label: "Variations", icon: ListMusic },
  { id: "studio", label: "Studio", icon: LayoutGrid },
  { id: "export", label: "Export", icon: Download },
] as const;

export default function ProjectPage({ id, tab }: { id: string; tab: string }) {
  const project = useProject(id);
  const jobs = useProjectJobs(id);
  const busy = jobs.some((j) => j.status === "running" || j.status === "queued");

  if (project.isLoading) return <div className="p-6"><Spinner /></div>;
  if (project.error || !project.data) return <div className="p-6"><ErrorBanner error={project.error ?? "Project not found"} /></div>;
  const p = project.data;
  const current = TABS.some((t) => t.id === tab) ? tab : "source";

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-4 border-b border-ink-700 bg-ink-900/60 px-4">
        <div className="max-w-64 truncate py-2 font-semibold" data-testid="project-name">{p.name}</div>
        <nav className="flex gap-0.5 overflow-x-auto" role="tablist">
          {TABS.map((t) => (
            <a key={t.id} role="tab" aria-selected={current === t.id} href={`#${projectPath(id, t.id)}`} data-testid={`tab-${t.id}`}
              className={cx("flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm whitespace-nowrap",
                current === t.id ? "border-accent text-ink-200" : "border-transparent text-ink-400 hover:text-ink-200")}>
              <t.icon className="size-4" /> {t.label}
            </a>
          ))}
        </nav>
        {busy && <span className="ml-auto flex items-center gap-1.5 text-xs text-accent"><Sparkles className="size-3.5 animate-pulse" /> working…</span>}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {current === "source" && <SourceTab project={p} jobs={jobs} />}
        {current === "samples" && <SamplesTab project={p} jobs={jobs} />}
        {current === "generate" && <GenerateTab project={p} jobs={jobs} />}
        {current === "variations" && <VariationsTab project={p} jobs={jobs} />}
        {current === "studio" && <Suspense fallback={<div className="p-6"><Spinner label="Loading studio…" /></div>}><Studio project={p} /></Suspense>}
        {current === "export" && <ExportTab project={p} jobs={jobs} />}
      </div>
    </div>
  );
}
