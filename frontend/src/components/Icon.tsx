const PATHS: Record<string, string> = {
  play: "M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5Z",
  pause: "M7 5h3.5v14H7zM13.5 5H17v14h-3.5z",
  upload: "M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
  download: "M12 4v12m0 0-4.5-4.5M12 16l4.5-4.5M4 19h16",
  check: "m5 12.5 4.5 4.5L19 7.5",
  lock: "M7 11V8a5 5 0 0 1 10 0v3M6 11h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Z",
  x: "M6 6l12 12M18 6 6 18",
  refresh: "M4 12a8 8 0 0 1 13.7-5.6L20 8.5M20 4v4.5h-4.5M20 12a8 8 0 0 1-13.7 5.6L4 15.5M4 20v-4.5h4.5",
  trash: "M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12M10 11v5M14 11v5",
  plus: "M12 5v14M5 12h14",
  arrow: "M5 12h14m0 0-5-5m5 5-5 5",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3ZM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z",
  wave: "M3 12h2M7 8v8M11 4v16M15 9v6M19 6v12M21 12h0",
  sliders: "M5 4v6m0 4v6M12 4v2m0 4v10M19 4v10m0 4v2M3 10h4M10 6h4M17 14h4",
  music: "M9 18V6l10-2v12M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Zm10-2a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8a7 7 0 0 1 14 0",
  layers: "m12 4 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5",
  file: "M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm7 0v5h5",
  shield: "M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.500 7-10V6l-7-3Z",
};
const FILLED = new Set(["play", "pause"]);

export function Icon({ name, size = 18 }: { name: keyof typeof PATHS | string; size?: number }) {
  const filled = FILLED.has(name);
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill={filled ? "currentColor" : "none"} stroke={filled ? "none" : "currentColor"} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}
