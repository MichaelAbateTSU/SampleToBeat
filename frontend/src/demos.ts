// Audio behind the pads on the landing page. Regenerate with
//   cd backend && uv run python ../scripts/make_demo_audio.py
// which renders these with the real engine from the built-in synthetic loop and rewrites demos.json.
import data from "./demos.json";

export interface Demo { id: string; url: string; title: string; bpm: number; key: string; style?: string; source?: boolean; peaks: number[]; duration: number }
export const DEMOS = data as Demo[];
