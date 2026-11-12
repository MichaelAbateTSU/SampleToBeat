// Audio on the landing page. Regenerate with `uv run python scripts/make_demo_audio.py` (it renders these with the
// real engine from the built-in synthetic loop and rewrites this file's data).
import data from "./demos.json";

export interface Demo { id: string; url: string; title: string; note: string; source?: boolean; peaks: number[]; duration: number }
export const DEMOS = data as Demo[];
