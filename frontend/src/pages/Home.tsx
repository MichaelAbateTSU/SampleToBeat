import { useMutation } from "@tanstack/react-query";
import { Music2, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { keys, queryClient, useProjects } from "../api/hooks";
import type { ProjectSummary } from "../api/types";
import { Badge, Button, Empty, ErrorBanner, IconButton, Modal, Spinner, TextField } from "../components/ui";
import { navigate, projectPath } from "../router";

export default function HomePage() {
  const projects = useProjects();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<ProjectSummary | null>(null);
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);

  const create = useMutation({
    mutationFn: (n: string) => api.createProject(n),
    onSuccess: (p) => {
      queryClient.invalidateQueries({ queryKey: keys.projects });
      setCreating(false);
      setName("");
      navigate(projectPath(p.id));
    },
  });
  const rename = useMutation({
    mutationFn: ({ id, n }: { id: string; n: string }) => api.renameProject(id, n),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.projects });
      setRenaming(null);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteProject(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.projects });
      setDeleting(null);
    },
  });

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Projects</h1>
          <p className="text-sm text-ink-400">Import a sample, let ChopLab analyze and chop it, then generate and edit beats in your private studio.</p>
        </div>
        <Button variant="primary" data-testid="new-project" onClick={() => setCreating(true)}><Plus className="size-4" /> New project</Button>
      </div>

      {projects.isLoading && <Spinner />}
      <ErrorBanner error={projects.error} />
      {projects.data && projects.data.length === 0 && (
        <Empty icon={<Music2 className="size-8" />} title="No projects yet">Create a project, then import an audio file (WAV, MP3, FLAC…) or use the built-in demo sample.</Empty>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {projects.data?.map((p) => (
          <div key={p.id} data-testid="project-card" className="group flex flex-col gap-2 rounded-lg border border-ink-700 bg-ink-900 p-4 hover:border-ink-500">
            <button className="text-left" onClick={() => navigate(projectPath(p.id, p.has_arrangement ? "studio" : "source"))}>
              <div className="truncate font-medium">{p.name}</div>
              <div className="text-xs text-ink-400">Updated {new Date(p.updated_at * 1000).toLocaleString()}</div>
            </button>
            <div className="flex flex-wrap gap-1.5">
              <Badge>{p.source_ids.length} source{p.source_ids.length === 1 ? "" : "s"}</Badge>
              {p.target_bpm && <Badge>{Math.round(p.target_bpm)} BPM</Badge>}
              {p.musical_key && <Badge>{p.musical_key}</Badge>}
              {p.has_arrangement && <Badge tone="accent">beat</Badge>}
            </div>
            <div className="flex justify-end gap-1 opacity-60 group-hover:opacity-100">
              <IconButton label="Rename project" onClick={() => setRenaming(p)}><Pencil className="size-4" /></IconButton>
              <IconButton label="Delete project" onClick={() => setDeleting(p)}><Trash2 className="size-4" /></IconButton>
            </div>
          </div>
        ))}
      </div>

      {creating && (
        <Modal title="New project" onClose={() => setCreating(false)} footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
            <Button variant="primary" data-testid="create-project" busy={create.isPending} disabled={!name.trim()} onClick={() => create.mutate(name.trim())}>Create</Button>
          </>
        }>
          <form onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(name.trim()); }} className="flex flex-col gap-3">
            <TextField label="Name" value={name} onChange={setName} maxLength={80} autoFocus placeholder="Late night flip" />
            <ErrorBanner error={create.error} />
          </form>
        </Modal>
      )}
      {renaming && <RenameModal p={renaming} busy={rename.isPending} error={rename.error} onClose={() => setRenaming(null)} onSave={(n) => rename.mutate({ id: renaming.id, n })} />}
      {deleting && (
        <Modal title="Delete project?" onClose={() => setDeleting(null)} footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>Cancel</Button>
            <Button variant="danger" busy={remove.isPending} onClick={() => remove.mutate(deleting.id)}>Delete</Button>
          </>
        }>
          <p className="text-sm">“{deleting.name}” and all its sources, stems, samples, variations and exports will be permanently deleted.</p>
          <ErrorBanner error={remove.error} />
        </Modal>
      )}
    </div>
  );
}

function RenameModal({ p, busy, error, onClose, onSave }: { p: ProjectSummary; busy: boolean; error: unknown; onClose: () => void; onSave: (n: string) => void }) {
  const [n, setN] = useState(p.name);
  return (
    <Modal title="Rename project" onClose={onClose} footer={
      <>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" busy={busy} disabled={!n.trim()} onClick={() => onSave(n.trim())}>Save</Button>
      </>
    }>
      <TextField label="Name" value={n} onChange={setN} maxLength={80} autoFocus />
      <ErrorBanner error={error} />
    </Modal>
  );
}
