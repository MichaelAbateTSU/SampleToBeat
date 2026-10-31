import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { engine } from "../audio/engine";

/** Re-render when the engine's status or transport state changes. */
export function useEngineState() {
  return useSyncExternalStore(
    (cb) => engine.subscribe(cb),
    () => engine.status,
  );
}

export function useEnginePlaying() {
  return useSyncExternalStore(
    (cb) => engine.subscribe(cb),
    () => engine.isPlaying,
  );
}

/** Calls `fn(positionSeconds)` every animation frame (no React re-render), e.g. to move the playhead. */
export function useAnimationFrame(fn: (pos: number) => void) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      ref.current(engine.position);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);
}

/** Position sampled ~15×/s for text displays. */
export function usePosition(hz = 15) {
  const [pos, setPos] = useState(engine.position);
  useEffect(() => {
    const t = window.setInterval(() => setPos(engine.position), 1000 / hz);
    return () => clearInterval(t);
  }, [hz]);
  return pos;
}

/** Per-track meter levels (dB), sampled ~20×/s. */
export function useLevels(enabled = true) {
  const [lv, setLv] = useState<Record<string, number>>({});
  useEffect(() => {
    if (!enabled) return;
    const t = window.setInterval(() => setLv(engine.levels()), 50);
    return () => clearInterval(t);
  }, [enabled]);
  return lv;
}
