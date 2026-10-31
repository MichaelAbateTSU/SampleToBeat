import { useQuery } from "@tanstack/react-query";
import { AudioWaveform, FolderOpen, Settings as SettingsIcon, WifiOff } from "lucide-react";
import { api } from "./api/client";
import { NowPlaying } from "./components/Audio";
import { cx } from "./components/ui";
import HomePage from "./pages/Home";
import ProjectPage from "./pages/Project";
import SettingsPage from "./pages/Settings";
import { navigate, useRoute } from "./router";

function Health() {
  const q = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 10_000, retry: false });
  if (!q.isError) return null;
  return (
    <div role="alert" className="flex items-center gap-2 bg-red-600/90 px-4 py-1.5 text-sm text-white">
      <WifiOff className="size-4" /> Cannot reach the ChopLab backend. Start it with the start script, then this page reconnects automatically.
    </div>
  );
}

export default function App() {
  const route = useRoute();
  const [top, id, tab] = route;
  let page;
  if (top === "p" && id) page = <ProjectPage key={id} id={id} tab={tab ?? "source"} />;
  else if (top === "settings") page = <SettingsPage />;
  else page = <HomePage />;
  return (
    <div className="flex h-full flex-col">
      <Health />
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-ink-700 bg-ink-900 px-4">
        <button onClick={() => navigate("/")} className="flex items-center gap-2 font-semibold tracking-tight">
          <AudioWaveform className="size-5 text-accent" /> ChopLab <span className="text-accent">AI</span>
        </button>
        <nav className="flex items-center gap-1 text-sm">
          <a href="#/" className={cx("flex items-center gap-1.5 rounded px-2 py-1 hover:bg-ink-700", (!top || top === "p") && "text-ink-200", top === "settings" && "text-ink-400")}>
            <FolderOpen className="size-4" /> Projects
          </a>
          <a href="#/settings" data-testid="nav-settings" className={cx("flex items-center gap-1.5 rounded px-2 py-1 hover:bg-ink-700", top === "settings" ? "text-ink-200" : "text-ink-400")}>
            <SettingsIcon className="size-4" /> Settings
          </a>
        </nav>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">{page}</main>
      <NowPlaying />
    </div>
  );
}
