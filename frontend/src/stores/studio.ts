// Studio document state: the arrangement being edited, undo/redo history, selection, and save status.
import { create } from "zustand";
import type { Arrangement } from "../api/types";
import { validate } from "../lib/arrangement";

const HISTORY = 200;

export interface Selection {
  trackId: string | null;
  clipId: string | null;
}

export type SaveState = "saved" | "dirty" | "saving" | "error";

interface StudioState {
  projectId: string | null;
  arr: Arrangement | null;
  past: { arr: Arrangement; label: string }[];
  future: { arr: Arrangement; label: string }[];
  selection: Selection;
  save: SaveState;
  saveError: string | null;
  lastError: string | null;
  /** bumps on every committed change; autosave watches it */
  revision: number;
  /** JSON of the server copy this document was loaded from / last saved as */
  baseline: string | null;
  setBaseline: (json: string) => void;
  load: (projectId: string, arr: Arrangement) => void;
  /** Apply an edit; returns false (and records the message) if the edit throws or fails validation. */
  apply: (label: string, fn: (a: Arrangement) => Arrangement) => boolean;
  undo: () => void;
  redo: () => void;
  select: (s: Partial<Selection>) => void;
  setSave: (s: SaveState, err?: string | null) => void;
  clearError: () => void;
}

export const useStudio = create<StudioState>((set, get) => ({
  projectId: null,
  arr: null,
  past: [],
  future: [],
  selection: { trackId: null, clipId: null },
  save: "saved",
  saveError: null,
  lastError: null,
  revision: 0,
  baseline: null,
  setBaseline: (baseline) => set({ baseline }),
  load: (projectId, arr) =>
    set({ projectId, arr, baseline: JSON.stringify(arr), past: [], future: [], selection: { trackId: null, clipId: null }, save: "saved", saveError: null, lastError: null, revision: 0 }),
  apply: (label, fn) => {
    const { arr, past, revision } = get();
    if (!arr) return false;
    let next: Arrangement;
    try {
      next = fn(arr);
    } catch (e) {
      set({ lastError: (e as Error).message });
      return false;
    }
    if (next === arr) return true;
    const errs = validate(next);
    if (errs.length) {
      set({ lastError: errs[0] });
      return false;
    }
    set({ arr: next, past: [...past, { arr, label }].slice(-HISTORY), future: [], save: "dirty", lastError: null, revision: revision + 1 });
    return true;
  },
  undo: () => {
    const { arr, past, future, revision } = get();
    const prev = past[past.length - 1];
    if (!arr || !prev) return;
    set({ arr: prev.arr, past: past.slice(0, -1), future: [{ arr, label: prev.label }, ...future], save: "dirty", revision: revision + 1 });
  },
  redo: () => {
    const { arr, past, future, revision } = get();
    const next = future[0];
    if (!arr || !next) return;
    set({ arr: next.arr, past: [...past, { arr, label: next.label }], future: future.slice(1), save: "dirty", revision: revision + 1 });
  },
  select: (s) => set((st) => ({ selection: { ...st.selection, ...s } })),
  setSave: (save, err = null) => set({ save, saveError: err }),
  clearError: () => set({ lastError: null }),
}));
