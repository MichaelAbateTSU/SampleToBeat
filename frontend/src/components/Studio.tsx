import { type Arrangement, type Track } from "../api";
import { usePlayer } from "../player";
import { useSession } from "../session";
import { Slider } from "./Slider";

const PATTERN_NAME: Record<string, string> = {
  s_main: "Hook", s_var: "Verse flip", s_filt: "Filtered",
  b_main: "808 main", b_var: "808 variation", fx_crash: "Crash",
};

function patternLabel(id: string): string {
  if (PATTERN_NAME[id]) return PATTERN_NAME[id];
  const m = /^d_(\w+?)_/.exec(id);
  return m ? { main: "Main", alt: "Alt", sparse: "Sparse", fill: "Fill" }[m[1]] ?? m[1] : id;
}

interface Props {
  arr: Arrangement;
  onChange: (next: Arrangement) => void;
  playerId: string; // id of the audio that corresponds to this arrangement, for the playhead
}

export function Studio({ arr, onChange, playerId }: Props) {
  const { caps } = useSession();
  const player = usePlayer();
  const secPerBar = 240 / arr.bpm;
  const active = player.track?.id === playerId;
  const head = active ? Math.min(1, player.time / (arr.bars * secPerBar)) : null;

  const setTrack = (id: string, patch: Partial<Track>) => onChange({ ...arr, tracks: arr.tracks.map((t) => (t.id === id ? { ...t, ...patch } : t)) });
  const setFx = (id: string, key: string, v: number) => {
    const t = arr.tracks.find((x) => x.id === id);
    if (t) setTrack(id, { fx: { ...t.fx, [key]: v } });
  };
  const sample = arr.tracks.find((t) => t.id === "sample");
  const tracks = arr.tracks.filter((t) => t.clips.length > 0);
  const anySolo = arr.tracks.some((t) => t.solo);
  const bass = arr.bass as Record<string, number>;

  return (
    <div className="studio">
      <div className="well timeline" style={{ "--bars": arr.bars } as React.CSSProperties}>
        <div className="tl-row tl-ruler">
          <div className="tl-head">Sections</div>
          <div className="tl-lane" onClick={(e) => {
            if (!active) return;
            const r = e.currentTarget.getBoundingClientRect();
            player.seek(((e.clientX - r.left) / r.width) * arr.bars * secPerBar);
          }}>
            {arr.sections.map((s, i) => (
              <div key={i} className={`tl-section sec-${s.name.toLowerCase()}`} style={{ left: `${(s.start_bar / arr.bars) * 100}%`, width: `${(s.bars / arr.bars) * 100}%` }}>
                <span>{s.name}</span><span className="tl-bars">{s.bars}</span>
              </div>
            ))}
          </div>
        </div>
        {tracks.map((t) => {
          const off = t.muted || (anySolo && !t.solo);
          return (
            <div className={`tl-row${off ? " off" : ""}`} key={t.id}>
              <div className="tl-head">
                <span className="tl-dot" style={{ background: t.color }} />
                <span className="tl-name" title={t.name}>{t.name}</span>
                <button className={`ms${t.muted ? " on-m" : ""}`} aria-pressed={t.muted} aria-label={`Mute ${t.name}`} onClick={() => setTrack(t.id, { muted: !t.muted })}>M</button>
                <button className={`ms${t.solo ? " on-s" : ""}`} aria-pressed={t.solo} aria-label={`Solo ${t.name}`} onClick={() => setTrack(t.id, { solo: !t.solo })}>S</button>
                <input className="tl-vol" type="range" min={-24} max={6} step={0.5} value={t.volume_db} aria-label={`${t.name} volume`} title={`${t.volume_db > 0 ? "+" : ""}${t.volume_db} dB`}
                  style={{ "--pct": `${((t.volume_db + 24) / 30) * 100}%` } as React.CSSProperties} onChange={(e) => setTrack(t.id, { volume_db: Number(e.target.value) })} />
              </div>
              <div className="tl-lane">
                {t.clips.map((c) => (
                  <div key={c.id} className="tl-clip" title={`${patternLabel(c.pattern_id)} · bars ${c.start_bar + 1}–${c.start_bar + c.bars}`} style={{ left: `${(c.start_bar / arr.bars) * 100}%`, width: `${(c.bars / arr.bars) * 100}%`, "--c": t.color } as React.CSSProperties}>
                    <span>{patternLabel(c.pattern_id)}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
        {head !== null && (
          <div style={{ position: "absolute", top: 0, bottom: 0, left: "var(--tl-head)", right: 0, pointerEvents: "none", transform: `translateX(${head * 100}%)` }}>
            <div className="tl-playhead" />
          </div>
        )}
      </div>

      <div className="studio-controls">
        <div className="control-group">
          <h4>Groove</h4>
          <Slider label="Tempo" min={60} max={180} step={1} value={arr.bpm} onChange={(bpm) => onChange({ ...arr, bpm })} format={(v) => `${v} BPM`} />
          <Slider label="Swing" min={50} max={75} step={1} value={arr.swing} onChange={(swing) => onChange({ ...arr, swing })} format={(v) => (v <= 50 ? "Straight" : `${v}%`)} />
          <label className="field">
            <span>Drum kit</span>
            <select value={arr.kit} onChange={(e) => onChange({ ...arr, kit: e.target.value })}>
              {Object.entries(caps?.kits ?? {}).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
        </div>
        {sample && (
          <div className="control-group">
            <h4>Sample</h4>
            <Slider label="Brightness" min={6.9} max={9.9} step={0.05} value={Math.log(sample.fx.lowpass_hz || 20000)} onChange={(v) => setFx("sample", "lowpass_hz", Math.exp(v) >= 19000 ? 0 : Math.round(Math.exp(v)))} format={(v) => (Math.exp(v) >= 19000 ? "Open" : `${(Math.exp(v) / 1000).toFixed(1)} kHz`)} />
            <Slider label="Reverb" min={0} max={0.6} value={sample.fx.reverb ?? 0} onChange={(v) => setFx("sample", "reverb", v)} format={(v) => `${Math.round((v / 0.6) * 100)}%`} />
            <Slider label="Grit" min={0} max={0.8} value={sample.fx.saturation ?? 0} onChange={(v) => setFx("sample", "saturation", v)} format={(v) => `${Math.round((v / 0.8) * 100)}%`} />
          </div>
        )}
        <div className="control-group">
          <h4>808</h4>
          <Slider label="Length" min={0.2} max={2} step={0.05} value={Number(bass.decay ?? 0.9)} onChange={(decay) => onChange({ ...arr, bass: { ...arr.bass, decay } })} format={(v) => (v < 0.6 ? "Short" : v < 1.2 ? "Medium" : "Long")} />
          <Slider label="Drive" min={0} max={1} value={Number(bass.drive ?? 0.35)} onChange={(drive) => onChange({ ...arr, bass: { ...arr.bass, drive } })} format={(v) => `${Math.round(v * 100)}%`} />
          <Slider label="Kick ducking" min={0} max={0.9} value={Number(bass.duck ?? 0.45)} onChange={(duck) => onChange({ ...arr, bass: { ...arr.bass, duck } })} format={(v) => `${Math.round(v * 100)}%`} />
        </div>
      </div>
      <p className="fineprint">M mutes a track and S solos it. Click the section bar to jump while your mix is playing.</p>
    </div>
  );
}
