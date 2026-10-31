import { useSyncExternalStore } from "react";

// Hash routing keeps the app a single static bundle the backend can serve from any path.
// Routes: #/  ·  #/settings  ·  #/p/<projectId>/<tab>

function subscribe(cb: () => void) {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
}

const snapshot = () => window.location.hash.replace(/^#/, "") || "/";

export function useRoute(): string[] {
  const path = useSyncExternalStore(subscribe, snapshot, () => "/");
  return path.split("?")[0].split("/").filter(Boolean).map(decodeURIComponent);
}

export function navigate(path: string) {
  window.location.hash = path;
}

export const projectPath = (id: string, tab = "source") => `/p/${encodeURIComponent(id)}/${tab}`;
