import type { Job } from "../api";
import { Icon } from "./Icon";

const STEPS = ["Listening to your sample", "Finding tempo and key", "Picking the best chops", "Programming drums and 808", "Mixing and mastering"];

function describe(job: Job): { step: number; text: string } {
  const s = job.current_stage ?? "";
  const p = job.progress ?? 0;
  if (job.status === "queued") return { step: 0, text: "Waiting for a free engine" };
  const m = /variation (\d+)\/(\d+)/.exec(s);
  if (m) return { step: Number(m[1]) === Number(m[2]) && p > 0.9 ? 4 : 3, text: `Building flip ${m[1]} of ${m[2]}` };
  if (s.startsWith("regenerating")) return { step: 3, text: "Rolling a new take" };
  if (s === "decoding") return { step: 0, text: STEPS[0] };
  if (s === "finding samples") return { step: 2, text: STEPS[2] };
  if (["tempo", "key", "downbeats", "onsets", "structure", "loudness"].includes(s)) return { step: 1, text: s === "tempo" ? "Finding the tempo" : s === "key" ? "Working out the key" : s === "downbeats" ? "Locating beat one" : "Mapping the groove" };
  if (s === "done") return { step: 4, text: "Finishing up" };
  return { step: 0, text: "Starting" };
}

export function Progress({ job, onCancel, compact }: { job: Job; onCancel?: () => void; compact?: boolean }) {
  const { step, text } = describe(job);
  const pct = Math.round((job.progress ?? 0) * 100);
  const full = job.job_type === "beat";
  return (
    <div className={`progress-card${compact ? " compact" : ""}`} role="status" aria-live="polite">
      <div className="chopper" aria-hidden="true">
        {Array.from({ length: 40 }, (_, i) => (
          <span key={i} style={{ animationDelay: `${(i % 7) * 0.09}s`, height: `${30 + ((i * 37) % 60)}%` }} />
        ))}
      </div>
      <div className="lcd progress-text">{text}… {pct}%</div>
      <div className="progress-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Progress"><span style={{ transform: `scaleX(${Math.max(0.03, pct / 100)})` }} /></div>
      {full && !compact && (
        <ol className="progress-steps">
          {STEPS.map((label, i) => (
            <li key={label} className={i < step ? "done" : i === step ? "now" : ""}>
              <span className="dot">{i < step ? <Icon name="check" size={12} /> : null}</span>
              {label}
            </li>
          ))}
        </ol>
      )}
      {onCancel && <div className="on-dark"><button className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel this beat</button></div>}
    </div>
  );
}
