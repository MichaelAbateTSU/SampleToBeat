import { AlertTriangle, Loader2, X } from "lucide-react";
import { type ButtonHTMLAttributes, type ReactNode, useEffect, useId } from "react";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({ variant = "secondary", size = "md", busy, className, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; busy?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        variant === "primary" && "bg-accent text-ink-950 hover:bg-amber-400",
        variant === "secondary" && "bg-ink-700 text-ink-200 hover:bg-ink-600",
        variant === "ghost" && "text-ink-300 hover:bg-ink-700 hover:text-ink-200",
        variant === "danger" && "bg-red-600/80 text-white hover:bg-red-600",
        className,
      )}
    >
      {busy && <Loader2 className="size-3.5 animate-spin" />}
      {children}
    </button>
  );
}

export function IconButton({ label, active, className, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; active?: boolean }) {
  return (
    <button
      {...rest}
      aria-label={label}
      title={rest.title ?? label}
      aria-pressed={active}
      className={cx(
        "inline-flex size-8 items-center justify-center rounded-md transition-colors disabled:opacity-40",
        active ? "bg-accent-soft text-accent" : "text-ink-300 hover:bg-ink-700 hover:text-ink-200",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg border border-ink-700 bg-ink-900", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-ink-700 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-ink-200">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Slider({ label, value, min, max, step = 0.01, onChange, format, disabled, hint }: {
  label: string; value: number; min: number; max: number; step?: number; onChange: (v: number) => void;
  format?: (v: number) => string; disabled?: boolean; hint?: string;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1" title={hint}>
      <div className="flex items-center justify-between text-xs">
        <label htmlFor={id} className="text-ink-300">{label}</label>
        <span className="tabular text-ink-200">{format ? format(value) : value}</span>
      </div>
      <input id={id} type="range" min={min} max={max} step={step} value={value} disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))} className="w-full" />
    </div>
  );
}

export function Select<T extends string>({ label, value, options, onChange, disabled, className }: {
  label?: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; disabled?: boolean; className?: string;
}) {
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-1", className)}>
      {label && <label htmlFor={id} className="text-xs text-ink-300">{label}</label>}
      <select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as T)}
        className="h-9 rounded-md border border-ink-600 bg-ink-800 px-2 text-sm text-ink-200 disabled:opacity-50">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

export function NumberField({ label, value, onChange, min, max, step = 1, className, placeholder }: {
  label: string; value: number | null; onChange: (v: number | null) => void; min?: number; max?: number; step?: number; className?: string; placeholder?: string;
}) {
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-1", className)}>
      <label htmlFor={id} className="text-xs text-ink-300">{label}</label>
      <input id={id} type="number" value={value ?? ""} min={min} max={max} step={step} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        className="h-9 rounded-md border border-ink-600 bg-ink-800 px-2 text-sm text-ink-200 tabular" />
    </div>
  );
}

export function TextField({ label, value, onChange, className, ...rest }: {
  label: string; value: string; onChange: (v: string) => void; className?: string; maxLength?: number; placeholder?: string; autoFocus?: boolean;
}) {
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-1", className)}>
      <label htmlFor={id} className="text-xs text-ink-300">{label}</label>
      <input id={id} value={value} onChange={(e) => onChange(e.target.value)} {...rest}
        className="h-9 rounded-md border border-ink-600 bg-ink-800 px-2 text-sm text-ink-200" />
    </div>
  );
}

export function Toggle({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm text-ink-200 select-none">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="size-4 accent-amber-500" />
      {label}
    </label>
  );
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "accent"; title?: string }) {
  return (
    <span title={title} className={cx(
      "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium",
      tone === "neutral" && "bg-ink-700 text-ink-300",
      tone === "good" && "bg-emerald-500/15 text-emerald-400",
      tone === "warn" && "bg-amber-500/15 text-amber-400",
      tone === "bad" && "bg-red-500/15 text-red-400",
      tone === "accent" && "bg-accent-soft text-accent",
    )}>{children}</span>
  );
}

export function Progress({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cx("h-1.5 w-full overflow-hidden rounded-full bg-ink-700", className)} role="progressbar" aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function ErrorBanner({ error, onClose }: { error: unknown; onClose?: () => void }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <span className="flex-1">{msg}</span>
      {onClose && <button aria-label="Dismiss" onClick={onClose}><X className="size-4" /></button>}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-ink-600 px-6 py-12 text-center">
      {icon && <div className="text-ink-400">{icon}</div>}
      <div className="font-medium text-ink-200">{title}</div>
      {children && <div className="max-w-md text-sm text-ink-400">{children}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-400">
      <Loader2 className="size-4 animate-spin" /> {label ?? "Loading…"}
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onMouseDown={onClose}>
      <div role="dialog" aria-label={title} className={cx("w-full rounded-lg border border-ink-600 bg-ink-850 shadow-2xl", wide ? "max-w-3xl" : "max-w-md")} onMouseDown={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-ink-700 px-4 py-3">
          <h2 className="font-semibold">{title}</h2>
          <IconButton label="Close" onClick={onClose}><X className="size-4" /></IconButton>
        </header>
        <div className="p-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-ink-700 px-4 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col" title={hint}>
      <span className="text-[11px] uppercase tracking-wide text-ink-400">{label}</span>
      <span className="tabular text-sm font-medium text-ink-200">{value}</span>
    </div>
  );
}
