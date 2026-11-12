import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, variationAudio, type ProjectSummary } from "../api";
import { Icon } from "../components/Icon";
import { ConfirmDialog } from "../components/Modal";
import { usePlayer } from "../player";
import { useSession } from "../session";

const ago = (t: number) => {
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(t * 1000);
};

export function Library() {
  const { guard, caps, me, loading } = useSession();
  const player = usePlayer();
  const nav = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [removing, setRemoving] = useState<ProjectSummary | null>(null);

  useEffect(() => {
    if (loading) return;
    api.projects().then(setProjects).catch(() => setProjects([]));
  }, [loading, me?.user?.id]);

  if (projects === null) return <div aria-busy="true" aria-label="Loading your beats"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div>;
  if (!projects.length) {
    return (
      <div className="empty">
        <h1>No beats yet</h1>
        <p className="muted">Drop in a sample and you have four flips to pick from in about half a minute.</p>
        <button className="btn btn-primary btn-lg" onClick={() => nav("/app/new")}>Make your first beat</button>
      </div>
    );
  }
  return (
    <div>
      <div className="section-row">
        <h1>My beats</h1>
        <span className="muted">{projects.length} of {me?.usage?.projects_limit.toLocaleString()} projects</span>
      </div>
      <ul className="library">
        {projects.map((p) => {
          const pid = `var:${p.variation_id}`;
          const on = player.isPlaying(pid);
          return (
            <li className="beat" key={p.id}>
              <button className="play-btn" disabled={!p.variation_id} aria-label={on ? "Pause" : `Play ${p.name}`} onClick={() => p.variation_id && player.toggle({ id: pid, url: variationAudio(p.variation_id), title: p.name, subtitle: p.genre ? caps?.genres[p.genre]?.label : undefined })}>
                <Icon name={on ? "pause" : "play"} />
              </button>
              <Link to={`/app/p/${p.id}`} className="beat-main">
                <strong>{p.name}</strong>
                <span className="chips">
                  {p.genre && <span className="chip">{caps?.genres[p.genre]?.label ?? p.genre}</span>}
                  {p.target_bpm && <span className="chip num">{p.target_bpm} BPM</span>}
                  {p.musical_key && <span className="chip">{p.musical_key}</span>}
                  {!p.has_arrangement && <span className="chip">In progress</span>}
                </span>
              </Link>
              <span className="muted beat-time">{ago(p.updated_at)}</span>
              <button className="btn btn-ghost btn-sm btn-icon" aria-label={`Delete ${p.name}`} onClick={() => setRemoving(p)}>
                <Icon name="trash" size={16} />
              </button>
            </li>
          );
        })}
      </ul>
      {removing && (
        <ConfirmDialog
          title={`Delete “${removing.name}”?`}
          body="This removes the upload and every flip made from it. It can't be undone."
          confirmLabel="Delete beat"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            const id = removing.id;
            await guard(() => api.deleteProject(id));
            setProjects((ps) => ps?.filter((x) => x.id !== id) ?? null);
          }}
        />
      )}
    </div>
  );
}
