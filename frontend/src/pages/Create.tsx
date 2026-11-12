import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type GenerateSettings } from "../api";
import { GENRE_BLURB } from "../brand";
import { Dropzone } from "../components/Dropzone";
import { Icon } from "../components/Icon";
import { Slider } from "../components/Slider";
import { takePendingSample } from "../handoff";
import { useSession } from "../session";

const LENGTHS = [16, 24, 36, 48, 64];

export function StyleControls({ value, onChange, showLength = true }: { value: Partial<GenerateSettings>; onChange: (v: Partial<GenerateSettings>) => void; showLength?: boolean }) {
  const { caps } = useSession();
  const genres = caps?.genres ?? {};
  const genre = value.genre ?? "boombap";
  const g = genres[genre];
  const bpm = value.bpm ?? (g ? Math.round((g.bpm[0] + g.bpm[1]) / 2) : 90);
  const seconds = (bars: number) => Math.round((bars * 240) / bpm);
  const set = (patch: Partial<GenerateSettings>) => onChange({ ...value, ...patch });
  return (
    <div className="style-controls">
      <fieldset>
        <legend>Style</legend>
        <div className="genre-grid">
          {Object.entries(genres).map(([id, info]) => (
            <button type="button" key={id} className={`genre${genre === id ? " on" : ""}`} aria-pressed={genre === id} onClick={() => set({ genre: id, bpm: null })}>
              <span className="genre-name">{info.label}</span>
              <span className="genre-bpm mono">{info.bpm[0]}–{info.bpm[1]} BPM</span>
              <span className="genre-blurb">{GENRE_BLURB[id]}</span>
            </button>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend>Feel</legend>
        <div className="slider-grid">
          <Slider label="Energy" low="Laid back" high="Full on" value={value.energy ?? 0.6} onChange={(energy) => set({ energy })} />
          <Slider label="Chops" low="Keep the loop" high="Slice it up" value={value.chop_intensity ?? 0.5} onChange={(chop_intensity) => set({ chop_intensity })} />
          <Slider label="Drums" low="Simple" high="Busy" value={value.drum_complexity ?? 0.5} onChange={(drum_complexity) => set({ drum_complexity })} />
          <Slider label="808" low="Light" high="Heavy" value={value.bass_intensity ?? 0.6} onChange={(bass_intensity) => set({ bass_intensity })} />
        </div>
      </fieldset>
      {showLength && (
        <fieldset>
          <legend>Length &amp; tempo</legend>
          <div className="row wrap">
            <div className="seg" role="radiogroup" aria-label="Length">
              {LENGTHS.map((b) => (
                <button type="button" key={b} role="radio" aria-checked={(value.bars ?? 36) === b} className={(value.bars ?? 36) === b ? "on" : ""} onClick={() => set({ bars: b })}>
                  {b} bars <span className="seg-sub">~{Math.floor(seconds(b) / 60)}:{String(seconds(b) % 60).padStart(2, "0")}</span>
                </button>
              ))}
            </div>
            <label className="tempo">
              <span>Tempo</span>
              <select value={value.bpm == null ? "auto" : "custom"} onChange={(e) => set({ bpm: e.target.value === "auto" ? null : bpm })}>
                <option value="auto">Auto (fits the sample)</option>
                <option value="custom">Set BPM</option>
              </select>
              {value.bpm != null && <input type="number" className="mono" min={60} max={180} value={value.bpm} onChange={(e) => set({ bpm: Math.min(180, Math.max(60, Number(e.target.value) || 60)) })} aria-label="BPM" />}
            </label>
          </div>
        </fieldset>
      )}
    </div>
  );
}

export function Create() {
  const nav = useNavigate();
  const { guard, me, refresh } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [demo, setDemo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Partial<GenerateSettings>>({ genre: "boombap", bars: 36, energy: 0.6, chop_intensity: 0.5, drum_complexity: 0.5, bass_intensity: 0.6, variations: 4 });

  useEffect(() => {
    const p = takePendingSample();
    if (p === "demo") setDemo(true);
    else if (p) setFile(p);
  }, []);

  const tooBig = useMemo(() => !!file && !!me && file.size > me.plan.max_upload_mb * 1024 * 1024, [file, me]);
  const ready = (file || demo) && !tooBig;

  const submit = async () => {
    setBusy(true);
    const r = await guard(() => api.makeBeat(demo ? null : file, settings));
    if (r) {
      refresh();
      nav(`/app/p/${r.project.id}`);
    } else setBusy(false);
  };

  return (
    <div className="create">
      <h1>New beat</h1>
      <p className="muted">Pick a sample and a direction. You'll get four different flips to choose from.</p>
      <div className="card">
        <h2 className="card-title"><span className="num">1</span> Your sample</h2>
        {demo ? (
          <div className="dropzone has-file compact">
            <div className="dropzone-icon"><Icon name="music" size={26} /></div>
            <div className="dropzone-title">Demo loop — 16 bars of keys, pad and bass at 92 BPM</div>
            <div className="dropzone-sub">Synthesised for ChopLab, free to use · <button type="button" className="link" onClick={() => setDemo(false)}>use my own sample</button></div>
          </div>
        ) : (
          <Dropzone file={file} onFile={setFile} onDemo={() => setDemo(true)} compact />
        )}
        {tooBig && me && <div className="form-error">This file is over the {me.plan.max_upload_mb} MB your plan allows. Trim it or upgrade for bigger uploads.</div>}
      </div>
      <div className="card">
        <h2 className="card-title"><span className="num">2</span> Direction</h2>
        <StyleControls value={settings} onChange={setSettings} />
      </div>
      <div className="create-actions">
        <button className="btn btn-primary btn-lg" disabled={!ready || busy} onClick={submit}>
          {busy ? "Uploading…" : <>Make my beat <Icon name="arrow" /></>}
        </button>
        <p className="fineprint">Only upload audio you have the right to use. <a href="/terms" target="_blank" rel="noreferrer">Terms</a></p>
      </div>
    </div>
  );
}
