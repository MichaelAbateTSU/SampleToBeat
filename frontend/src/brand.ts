export const BRAND = "ChopLab";
export const TAGLINE = "Drop a sample. Get a beat.";
// Set these at build time for your deployment (see README, "Before you launch").
export const SUPPORT_EMAIL = (import.meta.env.VITE_SUPPORT_EMAIL as string | undefined) ?? "support@example.com";
export const LEGAL_ENTITY = (import.meta.env.VITE_LEGAL_ENTITY as string | undefined) ?? "ChopLab";

export const GENRE_BLURB: Record<string, string> = {
  boombap: "Swung drums, chopped soul, round low end",
  trap: "Half-time sample, rolling hats, long 808s",
  drill: "Sliding 808s, skippy hats, dark and tense",
  lofi: "Dusty, warm and laid back",
};

export const STRATEGY_NAME: Record<string, string> = {
  loop_flip: "Loop flip",
  hard_chop: "Chopped",
  dark: "Slowed",
  creative: "Sped up",
};

export const FORMAT_LABEL: Record<string, { name: string; note: string }> = {
  mp3: { name: "MP3", note: "320 kbps" },
  wav16: { name: "WAV 16-bit", note: "CD quality" },
  wav24: { name: "WAV 24-bit", note: "Studio standard" },
  wav32f: { name: "WAV 32-bit float", note: "Maximum headroom" },
};
