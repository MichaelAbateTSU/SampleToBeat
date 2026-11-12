import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, variationAudio, type ProjectSummary } from "../api";
import { Icon } from "../components/Icon";
import { usePlayer } from "../player";
import { useSession } from "../session";

const ago = (t: number) => {
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

export function Library() {
  const { guard, caps, me, loading } = useSession();
  const player = usePlayer();
  const nav = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);

  useEffect(() => {
    if (loading) return;
    api.projects().then(setProjects).catch(() => setProjects([]));
  }, [loading, me?.user?.id]);

  if (projects === null) return <div className="loading">Loading…</div>;
  if (!projects.length) {
    return (
      <div className="empty">
        <div className="empty-art" aria-hidden="true"><Icon name="wave" size={40} /></div>
        <h1>No beats yet</h1>
        <p className="muted">Drop in a sample and you'll have four flips to pick from in about half a minute.</p>
        <button className="btn btn-primary btn-lg" onClick={() => nav("/app/new")}><Icon name="plus" /> Make your first beat</button>
      </div>
    );
  }
  return (
    <div>
      <div className="section-row">
        <h1>My beats</h1>
        <span className="muted">{projects.length} of {me?.usage?.projects_limit.toLocaleString()} projects</span>
      </div>
      <div className="library">
        {projects.map((p) => {
          const pid = `var:${p.variation_id}`;
          const on = player.isPlaying(pid);
          return (
            <article className="beat" key={p.id}>
              <button className="play-btn" disabled={!p.variation_id} aria-label={on ? "Pause" : `Play ${p.name}`} onClick={() => p.variation_id && player.toggle({ id: pid, url: variationAudio(p.variation_id), title: p.name, subtitle: p.genre ? caps?.genres[p.genre]?.label : undefined })}>
                <Icon name={on ? "pause" : "play"} />
              </button>
              <Link to={`/app/p/${p.id}`} className="beat-main">
                <strong>{p.name}</strong>
                <span className="chips">
                  {p.genre && <span className="chip">{caps?.genres[p.genre]?.label ?? p.genre}</span>}
                  {p.target_bpm && <span className="chip mono">{p.target_bpm} BPM</span>}
                  {p.musical_key && <span className="chip mono">{p.musical_key}</span>}
                  {!p.has_arrangement && <span className="chip">In progress</span>}
                </span>
              </Link>
              <span className="muted beat-time">{ago(p.updated_at)}</span>
              <button className="btn btn-ghost btn-sm btn-icon" aria-label={`Delete ${p.name}`} onClick={async () => {
                if (!window.confirm(`Delete "${p.name}"? This removes the upload and every flip made from it.`)) return;
                await guard(() => api.deleteProject(p.id));
                setProjects((ps) => ps?.filter((x) => x.id !== p.id) ?? null);
              }}>
                <Icon name="trash" size={16} />
              </button>
            </article>
          );
        })}
      </div>
    </div>
  );
}
