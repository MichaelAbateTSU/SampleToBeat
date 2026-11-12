import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, sampleAudio, TERMINAL, variationAudio, watchJob, type Arrangement, type GenerateSettings, type Job, type Project, type Sample, type Variation } from "../api";
import { STRATEGY_NAME } from "../brand";
import { ExportPanel } from "../components/ExportPanel";
import { Icon } from "../components/Icon";
import { LcdText } from "../components/Lcd";
import { ConfirmDialog } from "../components/Modal";
import { Progress } from "../components/Progress";
import { Studio } from "../components/Studio";
import { Waveform } from "../components/Waveform";
import { usePlayer } from "../player";
import { useSession } from "../session";
import { StyleControls } from "./Create";

type Tab = "mix" | "export" | "sample" | "more";
const TABS: Tab[] = ["mix", "export", "sample", "more"];

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
  const [params, setParams] = useSearchParams();
  const tab: Tab = TABS.includes(params.get("tab") as Tab) ? (params.get("tab") as Tab) : "mix";
  const setTab = (t: Tab) => setParams(t === "mix" ? {} : { tab: t }, { replace: true });
  const [removing, setRemoving] = useState<Variation | null>(null);
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

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();  // unsaved mix changes
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

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
  if (!project) return <div aria-busy="true" aria-label="Loading beat"><div className="skeleton" /><div className="skeleton tall" /></div>;

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
        <div style={{ minWidth: 0 }}>
          {naming !== null ? (
            <form onSubmit={async (e) => { e.preventDefault(); const p = await guard(() => api.renameProject(project.id, naming.trim() || project.name)); if (p) setProject({ ...project, name: p.name }); setNaming(null); }}>
              <input className="title-input" name="beat-name" autoComplete="off" autoFocus value={naming} maxLength={80} onChange={(e) => setNaming(e.target.value)} onBlur={(e) => e.currentTarget.form?.requestSubmit()} aria-label="Beat name" />
            </form>
          ) : (
            <h1><button className="title-edit" aria-label={`Rename ${project.name}`} onClick={() => setNaming(project.name)}>{project.name}</button></h1>
          )}
          {src && <p className="muted" style={{ margin: 0 }}>Made from {src.original_filename}</p>}
        </div>
        {src?.analysis && (
          <dl className="readouts" aria-label="What ChopLab heard in your sample">
            {src.analysis.one_shot ? (
              <div className="readout"><dt>Sample</dt><dd>One-shot</dd></div>
            ) : (
              <>
                <div className="readout"><dt>Sample tempo</dt><dd><LcdText>{String(Math.round(src.analysis.bpm * 10) / 10)}</LcdText></dd></div>
                <div className="readout"><dt>Key</dt><dd>{src.analysis.key}</dd></div>
              </>
            )}
            {current && <div className="readout"><dt>Beat tempo</dt><dd>{arr?.bpm ?? current.bpm}</dd></div>}
          </dl>
        )}
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
            <span className="muted">Pick the one you like. It opens in the studio below.</span>
          </div>
          <ul className="variations">
            {variations.map((v) => {
              const pid = `var:${v.id}`;
              const on = player.isPlaying(pid);
              const selected = v.id === project.variation_id;
              return (
                <li key={v.id} className={`variation pad${selected ? " selected" : ""}`}>
                  <div className="variation-top">
                    <button className="play-btn" aria-label={on ? "Pause" : `Play ${STRATEGY_NAME[v.strategy]}`} onClick={() => player.toggle({ id: pid, url: variationAudio(v.id), title: `${project.name}: ${STRATEGY_NAME[v.strategy] ?? v.strategy}`, subtitle: [`${v.bpm} BPM`, v.key].filter(Boolean).join(", "), peaks: v.peaks, duration: v.duration })}>
                      <Icon name={on ? "pause" : "play"} />
                    </button>
                    <div>
                      <h3>{STRATEGY_NAME[v.strategy] ?? v.strategy}</h3>
                      <p className="muted">{v.meta.strategy_detail.split(": ")[1] ?? ""}</p>
                    </div>
                  </div>
                  <div className="variation-wave">
                    <Waveform peaks={v.peaks} height={40} progress={player.track?.id === pid && player.duration ? player.time / player.duration : 0} onSeek={player.track?.id === pid ? (f) => player.seek(f * player.duration) : undefined} />
                  </div>
                  <div className="lcd">
                    <span>{v.bpm} BPM</span>
                    {v.key && <span>{v.key}</span>}
                    <span className="lcd-dim">{v.bars} bars</span>
                  </div>
                  <div className="variation-actions">
                    {selected ? (
                      <span className="in-studio">In the studio</span>
                    ) : (
                      <button className="btn btn-sm" onClick={async () => { const p = await guard(() => api.openInStudio(v.id)); if (p) { await load(); setTab("mix"); } }}>Use this one</button>
                    )}
                    <button className="btn btn-ghost btn-sm" disabled={!!job} title="Make a new take in the same style" onClick={async () => { const j = await guard(() => api.regenerate(v.id)); if (j) follow(j); }}>
                      <Icon name="refresh" size={15} /> Reroll
                    </button>
                    {!selected && (
                      <button className="btn btn-ghost btn-sm btn-icon" aria-label={`Delete the ${STRATEGY_NAME[v.strategy] ?? v.strategy} flip`} onClick={() => setRemoving(v)}>
                        <Icon name="trash" size={15} />
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
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
              <dl className="readouts">
                <div className="readout"><dt>Length</dt><dd><LcdText>{`${(src.duration_seconds ?? 0).toFixed(1)} s`}</LcdText></dd></div>
                <div className="readout"><dt>Read as</dt><dd>{src.analysis?.one_shot ? "One-shot, played as a melody" : src.analysis?.loop_locked ? "Tight loop, exact tempo" : "Recording, tempo tracked"}</dd></div>
                {!src.analysis?.one_shot && <div className="readout"><dt>Tempo</dt><dd><LcdText>{`${src.analysis?.bpm} BPM`}</LcdText></dd></div>}
                {!src.analysis?.one_shot && <div className="readout"><dt>Key</dt><dd>{src.analysis?.key}</dd></div>}
              </dl>
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
                        <span className="muted">{s.explanation.slice(0, 2).join(", ")}</span>
                      </div>
                      <div className="wave-well"><Waveform peaks={s.peaks} height={26} bars={60} /></div>
                      {current.meta.main_sample === s.id ? <span className="chip chip-accent">In use</span> : (
                        <button className="btn btn-sm" disabled={!!job} onClick={() => startGeneration({ sample_mode: "manual", sample_ids: [s.id] })}>Flip this</button>
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
              <button className="btn btn-primary" disabled={!!job} onClick={() => startGeneration({ ...more, sample_mode: "auto", sample_ids: [] })}>Make 4 new flips</button>
            </div>
          )}
        </section>
      )}
      {removing && (
        <ConfirmDialog
          title="Delete this flip?"
          body={`The ${STRATEGY_NAME[removing.strategy] ?? removing.strategy} flip will be removed from this beat. This can't be undone.`}
          confirmLabel="Delete flip"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            const id = removing.id;
            await guard(() => api.deleteVariation(id));
            setVariations((vs) => vs.filter((x) => x.id !== id));
          }}
        />
      )}
    </div>
  );
}
