import { useState } from "react";
import { cx } from "../components/ui";

/** Range input that previews locally while dragging and commits once on release (one undo step per gesture). */
export function CommitSlider({ label, value, min, max, step = 0.01, onCommit, format, onPreview, vertical, testId, className }: {
  label: string; value: number; min: number; max: number; step?: number; onCommit: (v: number) => void;
  format?: (v: number) => string; onPreview?: (v: number) => void; vertical?: boolean; testId?: string; className?: string;
}) {
  const [draft, setDraft] = useState<number | null>(null);
  const shown = draft ?? value;
  const commit = () => {
    if (draft !== null && draft !== value) onCommit(draft);
    setDraft(null);
  };
  return (
    <label className={cx("flex flex-col gap-0.5 text-[10px] text-ink-400", className)} title={`${label}: ${format ? format(shown) : shown}`}>
      <span className="flex justify-between gap-1 uppercase"><span className="truncate">{label}</span><span className="tabular text-ink-300 normal-case">{format ? format(shown) : shown}</span></span>
      <input type="range" aria-label={label} data-testid={testId} min={min} max={max} step={step} value={shown}
        onChange={(e) => { const v = Number(e.target.value); setDraft(v); onPreview?.(v); }}
        onPointerUp={commit} onKeyUp={commit} onBlur={commit}
        onDoubleClick={(e) => e.preventDefault()}
        className={cx("accent-accent", vertical && "h-24 [writing-mode:vertical-lr] [direction:rtl]")} />
    </label>
  );
}
