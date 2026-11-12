import { useState } from "react";
import { api, exportFileUrl, watchJob, type ExportDoc, type Project } from "../api";
import { FORMAT_LABEL } from "../brand";
import { useSession } from "../session";
import { Icon } from "./Icon";

const KIND_LABEL: Record<string, string> = { mix: "Mastered beat", zip: "All stems (zip)", midi: "MIDI file", project: "Project file", license: "License" };

export function ExportPanel({ project, beforeExport }: { project: Project; beforeExport: () => Promise<boolean> }) {
  const { me, plans, guard, askUpgrade } = useSession();
  const plan = me?.plan;
  const [format, setFormat] = useState(plan?.formats.includes("wav24") ? "wav24" : "mp3");
  const [stems, setStems] = useState(false);
  const [midi, setMidi] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ExportDoc | null>(null);
  if (!plan || !plans) return null;

  const needs = (feature: "stems" | "midi" | "formats", value?: string) => {
    const id = plans.order.find((p) => (feature === "formats" ? plans.plans[p].formats.includes(value!) : plans.plans[p][feature]))!;
    return { id, label: plans.plans[id].label };
  };
  const locked = (feature: "stems" | "midi" | "formats", value?: string) => (feature === "formats" ? !plan.formats.includes(value!) : !plan[feature]);
  const upsell = (feature: "stems" | "midi" | "formats", value?: string) => {
    const n = needs(feature, value);
    const what = feature === "formats" ? `${FORMAT_LABEL[value!].name} export` : feature === "stems" ? "Stem export" : "MIDI export";
    askUpgrade({ message: `${what} is part of the ${n.label} plan.`, planNeeded: n.id });
  };

  const run = async () => {
    setBusy("Saving your mix…");
    setResult(null);
    if (!(await beforeExport())) return setBusy(null);
    setBusy("Rendering…");
    const started = await guard(() => api.startExport(project.id, { format, stems, midi, project_json: false, filename: project.name }));
    if (!started) return setBusy(null);
    const job = await watchJob(started.job.id, (j) => setBusy(j.current_stage === "writing stems" ? "Writing stems…" : j.current_stage === "verifying" ? "Checking the files…" : "Rendering…")).done;
    const doc = await guard(() => api.getExport(started.export_id));
    setBusy(null);
    if (doc && job.status === "completed") setResult(doc);
    else if (doc) setResult({ ...doc, status: "failed", error: job.error_message ?? "Export failed." });
  };

  const files = (result?.files ?? []).filter((f) => f.kind !== "stem");

  return (
    <div className="export">
      <div className="export-options">
        <fieldset>
          <legend>Format</legend>
          <div className="format-grid">
            {Object.entries(FORMAT_LABEL).map(([id, f]) => {
              const lock = locked("formats", id);
              return (
                <button key={id} type="button" className={`format${format === id ? " on" : ""}${lock ? " locked" : ""}`} aria-pressed={format === id} onClick={() => (lock ? upsell("formats", id) : setFormat(id))}>
                  <span className="format-name">{f.name}</span>
                  <span className="format-note">{lock ? <><Icon name="lock" size={12} /> {needs("formats", id).label}</> : f.note}</span>
                </button>
              );
            })}
          </div>
        </fieldset>
        <fieldset>
          <legend>Extras</legend>
          {([["stems", "Track stems", "Sample, kick, snare, hats and 808 as separate WAVs", stems, setStems], ["midi", "MIDI", "Drums, 808 line and chop triggers at 480 PPQ", midi, setMidi]] as const).map(([key, title, note, on, set]) => {
            const lock = locked(key);
            return (
              <label key={key} className={`toggle-row${lock ? " locked" : ""}`} onClick={(e) => { if (lock) { e.preventDefault(); upsell(key); } }}>
                <input type="checkbox" checked={on && !lock} disabled={lock} onChange={(e) => set(e.target.checked)} />
                <span className="toggle" aria-hidden="true" />
                <span className="toggle-text"><strong>{title}</strong><span>{note}</span></span>
                {lock && <span className="chip chip-lock"><Icon name="lock" size={12} /> {needs(key).label}</span>}
              </label>
            );
          })}
        </fieldset>
      </div>
      <div className="export-side">
        <div className="license-note">
          <Icon name="shield" />
          <div>
            <strong>{plan.commercial_license ? "Commercial license included" : "Personal-use license"}</strong>
            <p>{plan.commercial_license ? "Release and monetise what ChopLab generated. The license file is saved with your export." : "Fine for demos and non-monetised posts. Upgrade to release and sell."} Rights to your own sample are separate.</p>
          </div>
        </div>
        <button className="btn btn-primary btn-lg btn-block" disabled={!!busy} onClick={run}>
          {busy ?? "Export beat"}
        </button>
        {result?.status === "failed" && <div className="form-error">{result.error}</div>}
        {result && result.status !== "failed" && (
          <ul className="files">
            {files.map((f) => (
              <li key={f.name}>
                <a className="file" href={exportFileUrl(result.id, f.name)} download={f.name}>
                  <Icon name={f.kind === "license" ? "shield" : f.kind === "zip" ? "layers" : "download"} />
                  <span><strong>{KIND_LABEL[f.kind] ?? f.kind}</strong><small>{f.name}</small></span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
