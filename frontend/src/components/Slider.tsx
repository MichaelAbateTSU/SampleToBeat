import { useId } from "react";
import { LcdText } from "./Lcd";

interface Props {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  low?: string;
  high?: string;
  format?: (v: number) => string;
}

export function Slider({ label, value, onChange, min = 0, max = 1, step = 0.01, low, high, format }: Props) {
  const id = useId();
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="slider">
      <div className="slider-head">
        <label htmlFor={id}>{label}</label>
        {format && <span className="slider-value"><LcdText>{format(value)}</LcdText></span>}
      </div>
      <input id={id} type="range" min={min} max={max} step={step} value={value} style={{ "--pct": `${pct}%` } as React.CSSProperties} onChange={(e) => onChange(Number(e.target.value))} />
      {(low || high) && (
        <div className="slider-ends"><span>{low}</span><span>{high}</span></div>
      )}
    </div>
  );
}
