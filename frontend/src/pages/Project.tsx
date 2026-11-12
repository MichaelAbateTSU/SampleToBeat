import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, sampleAudio, TERMINAL, variationAudio, watchJob, type Arrangement, type GenerateSettings, type Job, type Project, type Sample, type Variation } from "../api";
import { STRATEGY_NAME } from "../brand";
import { ExportPanel } from "../components/ExportPanel";
import { Icon } from "../components/Icon";
import { Progress } from "../components/Progress";
import { Studio } from "../components/Studio";
import { Waveform } from "../components/Waveform";
import { usePlayer } from "../player";
import { useSession } from "../session";
import { StyleControls } from "./Create";

type Tab = "mix" | "export" | "sample" | "more";

export function ProjectPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const { guard, refresh, toast, caps } = useSession();
  const player = usePlayer();
  const [project, setProject] = useState<Project | null>(null);
  const [variations, setVariations] = useState<Variation[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [arr, setArr] = useState<Arrangement | null>(null);
  const [dirty, setDirty] = useState(false);
  const [applying, setApplying] = useState(false);
  const [mix, setMix] = useState<{ url: string; peaks: number[]; duration: number; lufs: number | null } | null>(null);
  const [tab, setTab] = useState<Tab>("mix");
  const [more, setMore] = useState<Partial<GenerateSettings>>({});
  const [naming, setNaming] = useState<string | null>(null);
  const watcher = useRef<{ stop: () => void } | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, vs, ss] = await Promise.all([api.project(id), api.variations(id), api.samples(id)]);
      setProject(p);
      setVariations(vs);
      setSamples(ss);
      setArr(p.arrangement ? structuredClone(p.arrangement) : null);
      setDirty(false);
      setMix(null);
      setMore((m) => (Object.keys(m).length ? m : p.generator_settings));
      return p;
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 401)) setMissing(true);
      else toast(e instanceof Error ? e.message : "Could not load this beat.", "error");
      return null;
    }
  }, [id, toast]);

  const follow = useCallback(
    (j: Job) => {
      watcher.current?.stop();
      setJob(j);
      setFailed(null);
      const w = watchJob(j.id, setJob);
      watcher.current = w;
      w.done.then(async (final) => {
        setJob(null);
        if (final.status === "failed" || final.status === "interrupted") setFailed(final.error_message ?? "Something went wrong while making this beat.");
        await load();
        refresh();
      });
    },
    [load, refresh],
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      const p = await load();
      if (!p || !alive) return;
      const jobs = await api.jobs(id).catch(() => [] as Job[]);
      if (!alive) return;
      const running = jobs.find((j) => !TERMINAL.has(j.status) && j.job_type !== "export");
      if (running) follow(running);
      else if (!p.arrangement && jobs[0] && jobs[0].status !== "completed") setFailed(jobs[0].error_message ?? "This beat didn't finish.");
    })();
    return () => {
      alive = false;
      watcher.current?.stop();
    };
  }, [id, load, follow]);

  const change = (next: Arrangement) => {
    setArr(next);
    setDirty(true);
  };

  /** Saves the edited arrangement and renders it; export always uses the saved arrangement. */
  const apply = async (listen = true): Promise<boolean> => {
    if (!project || !arr) return false;
    if (!dirty && (mix || !listen)) {
      if (listen && mix) player.play({ id: `mix:${project.id}`, url: mix.url, title: project.name, subtitle: "Your mix", peaks: mix.peaks, duration: mix.duration });
      return true;
    }
    setApplying(true);
    const ok = await guard(async () => {
      await api.saveArrangement(project.id, arr);
      const r = await api.preview(project.id);
      const m = { url: `${r.url}?format=mp3`, peaks: r.peaks, duration: r.duration, lufs: r.metrics.lufs };
      setMix(m);
      setDirty(false);
      if (listen) player.play({ id: `mix:${project.id}`, url: m.url, title: project.name, subtitle: "Your mix", peaks: m.peaks, duration: m.duration });
      return true;
    });
    setApplying(false);
    return !!ok;
  };

  if (missing) {
    return (
      <div className="empty">
        <h1>Beat not found</h1>
        <p className="muted">It may have been deleted, or it belongs to a different account.</p>
        <Link className="btn btn-primary" to="/app">Back to my beats</Link>
      </div>
    );
  }
  if (!project) return <div className="loading">Loading…</div>;

  const src = project.sources[0];
  const building = job && (job.job_type === "beat" || !variations.length);
  const current = variations.find((v) => v.id === project.variation_id);
  const startGeneration = async (settings: Partial<GenerateSettings>) => {
    const j = await guard(() => api.generate(project.id, { ...project.generator_settings, ...settings, seed: null }));
    if (j) follow(j);
  };

  return (
    <div className="project">
      <header className="project-head">
        <div>
          {naming !== null ? (
            <form onSubmit={async (e) => { e.preventDefault(); const p = await guard(() => api.renameProject(project.id, naming.trim() || project.name)); if (p) setProject({ ...project, name: p.name }); setNaming(null); }}>
              <input className="title-input" autoFocus value={naming} maxLength={80} onChange={(e) => setNaming(e.target.value)} onBlur={(e) => e.currentTarget.form?.requestSubmit()} aria-label="Beat name" />
            </form>
          ) : (
            <h1 className="editable" title="Rename" onClick={() => setNaming(project.name)}>{project.name}</h1>
          )}
          <div className="chips">
            {src && <span className="chip"><Icon name="music" size={13} /> {src.original_filename}</span>}
            {src?.analysis && <span className="chip mono">{src.analysis.one_shot ? "One-shot" : `${Math.round(src.analysis.bpm * 10) / 10} BPM`}</span>}
            {src?.analysis && !src.analysis.one_shot && <span className="chip mono">{src.analysis.key}</span>}
          </div>
        </div>
      </header>

      {building && job && <Progress job={job} onCancel={() => api.cancelJob(job.id).catch(() => undefined)} />}

      {failed && !job && (
        <div className="card error-card">
          <h2>That one didn't work</h2>
          <p>{failed}</p>
          <button className="btn btn-primary" onClick={() => nav("/app/new")}>Try another sample</button>
        </div>
      )}

      {variations.length > 0 && (
        <section>
          <div className="section-row">
            <h2>Your flips</h2>
            <span className="muted">Pick the one you like — it opens in the studio below.</span>
          </div>
          <div className="variations">
            {variations.map((v) => {
              const pid = `var:${v.id}`;
              const on = player.isPlaying(pid);
              const selected = v.id === project.variation_id;
              return (
                <article key={v.id} className={`variation${selected ? " selected" : ""}`}>
                  <div className="variation-top">
                    <button className="play-btn" aria-label={on ? "Pause" : `Play ${STRATEGY_NAME[v.strategy]}`} onClick={() => player.toggle({ id: pid, url: variationAudio(v.id), title: `${project.name} — ${STRATEGY_NAME[v.strategy] ?? v.strategy}`, subtitle: `${v.bpm} BPM · ${v.key ?? ""}`, peaks: v.peaks, duration: v.duration })}>
                      <Icon name={on ? "pause" : "play"} />
                    </button>
                    <div>
                      <h3>{STRATEGY_NAME[v.strategy] ?? v.strategy}</h3>
                      <p className="muted">{v.meta.strategy_detail.split(": ")[1] ?? ""}</p>
                    </div>
                  </div>
                  <Waveform peaks={v.peaks} height={44} progress={player.track?.id === pid && player.duration ? player.time / player.duration : 0} onSeek={player.track?.id === pid ? (f) => player.seek(f * player.duration) : undefined} />
                  <div className="chips">
                    <span className="chip mono">{v.bpm} BPM</span>
                    {v.key && <span className="chip mono">{v.key}</span>}
                    <span className="chip mono">{v.bars} bars</span>
                    {v.meta.pitch !== 0 && <span className="chip mono">{v.meta.pitch > 0 ? "+" : ""}{v.meta.pitch} st</span>}
                  </div>
                  <div className="variation-actions">
                    {selected ? (
                      <span className="chip chip-accent"><Icon name="check" size={13} /> In the studio</span>
                    ) : (
                      <button className="btn btn-secondary btn-sm" onClick={async () => { const p = await guard(() => api.openInStudio(v.id)); if (p) { await load(); setTab("mix"); } }}>Use this one</button>
                    )}
                    <button className="btn btn-ghost btn-sm" disabled={!!job} title="Make a new take in the same style" onClick={async () => { const j = await guard(() => api.regenerate(v.id)); if (j) follow(j); }}>
                      <Icon name="refresh" size={15} /> Reroll
                    </button>
                    {!selected && (
                      <button className="btn btn-ghost btn-sm btn-icon" aria-label="Delete this flip" onClick={async () => { await guard(() => api.deleteVariation(v.id)); setVariations((vs) => vs.filter((x) => x.id !== v.id)); }}>
                        <Icon name="trash" size={15} />
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
          {job && !building && <Progress job={job} compact />}
        </section>
      )}

      {arr && current && (
        <section className="card studio-card">
          <div className="studio-bar">
            <div className="tabs" role="tablist">
              {([["mix", "Arrange & mix", "sliders"], ["export", "Export", "download"], ["sample", "Sample", "wave"], ["more", "More flips", "spark"]] as const).map(([key, label, icon]) => (
                <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}><Icon name={icon} size={16} /> {label}</button>
              ))}
            </div>
            {tab === "mix" && (
              <div className="row">
                {dirty && <button className="btn btn-ghost btn-sm" onClick={() => { setArr(structuredClone(project.arrangement!)); setDirty(false); }}>Undo changes</button>}
                <button className="btn btn-primary btn-sm" disabled={applying} onClick={() => apply(true)}>
                  {applying ? "Rendering…" : dirty ? <><Icon name="refresh" size={15} /> Apply &amp; listen</> : <><Icon name="play" size={15} /> Play mix</>}
                </button>
              </div>
            )}
          </div>

          {tab === "mix" && <Studio arr={arr} onChange={change} playerId={!dirty && mix ? `mix:${project.id}` : `var:${current.id}`} />}

          {tab === "export" && <ExportPanel project={project} beforeExport={() => apply(false)} />}

          {tab === "sample" && src && (
            <div className="sample-tab">
              <div className="facts">
                <div><span className="muted">File</span><strong>{src.original_filename}</strong></div>
                <div><span className="muted">Length</span><strong className="mono">{(src.duration_seconds ?? 0).toFixed(1)} s</strong></div>
                <div><span className="muted">Read as</span><strong>{src.analysis?.one_shot ? "One-shot — played as a melody" : src.analysis?.loop_locked ? "Tight loop — exact tempo" : "Recording — tempo tracked"}</strong></div>
                {!src.analysis?.one_shot && <div><span className="muted">Tempo / key</span><strong className="mono">{src.analysis?.bpm} BPM · {src.analysis?.key}</strong></div>}
              </div>
              <h4>Chops and loops found in your sample</h4>
              <p className="muted">Build a new set of flips around a different part.</p>
              <ul className="samples">
                {samples.slice(0, 10).map((s) => {
                  const sid = `smp:${s.id}`;
                  const on = player.isPlaying(sid);
                  return (
                    <li key={s.id}>
                      <button className="play-btn sm" aria-label={`Play ${s.name}`} onClick={() => player.toggle({ id: sid, url: sampleAudio(s.id), title: s.name, subtitle: src.original_filename, peaks: s.peaks, duration: s.duration })}><Icon name={on ? "pause" : "play"} size={15} /></button>
                      <div className="sample-info">
                        <strong>{s.name}</strong>
                        <span className="muted">{s.explanation.slice(0, 2).join(" · ")}</span>
                      </div>
                      <Waveform peaks={s.peaks} height={28} bars={60} />
                      {current.meta.main_sample === s.id ? <span className="chip chip-accent">In use</span> : (
                        <button className="btn btn-secondary btn-sm" disabled={!!job} onClick={() => startGeneration({ sample_mode: "manual", sample_ids: [s.id] })}>Flip this</button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {tab === "more" && caps && (
            <div className="more-tab">
              <StyleControls value={more} onChange={setMore} />
              <button className="btn btn-primary" disabled={!!job} onClick={() => startGeneration({ ...more, sample_mode: "auto", sample_ids: [] })}><Icon name="spark" /> Make 4 new flips</button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
