import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import { queryClient, TERMINAL, useCapabilities, useJobs } from "./hooks";

export const HEALTH_IDLE_MS = 60_000;

/** Hosted health traffic follows interaction, not merely an open visible tab. */
export function useHealth() {
  const hosted = useCapabilities().data?.hosted !== false;
  const busy = useJobs((s) => Object.values(s.jobs).some((job) => !TERMINAL.has(job.status)));
  const [awake, setAwake] = useState(document.visibilityState === "visible");
  const awakeRef = useRef(awake);
  const resume = useRef(false);
  const q = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    enabled: !hosted || awake || busy,
    refetchInterval: !hosted || awake || busy ? 10_000 : false,
    retry: false,
  });
  const refetch = q.refetch;

  useEffect(() => {
    if (!hosted) return;
    let timer: number;
    const sleep = () => {
      awakeRef.current = false;
      setAwake(false);
    };
    const activity = () => {
      if (document.visibilityState !== "visible") return;
      clearTimeout(timer);
      const returning = !awakeRef.current;
      awakeRef.current = true;
      setAwake(true);
      timer = window.setTimeout(sleep, HEALTH_IDLE_MS);
      if (returning) {
        resume.current = true;
        void refetch();
      }
    };
    const visibility = () => {
      if (document.visibilityState === "visible") activity();
      else {
        clearTimeout(timer);
        sleep();
      }
    };
    const events = ["pointerdown", "keydown", "wheel", "touchstart"];
    for (const event of events) window.addEventListener(event, activity, { passive: true });
    window.addEventListener("focus", activity);
    window.addEventListener("online", activity);
    document.addEventListener("visibilitychange", visibility);
    timer = window.setTimeout(sleep, HEALTH_IDLE_MS);
    return () => {
      clearTimeout(timer);
      for (const event of events) window.removeEventListener(event, activity);
      window.removeEventListener("focus", activity);
      window.removeEventListener("online", activity);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [hosted, refetch]);

  useEffect(() => {
    if (resume.current && q.isSuccess && !q.isFetching) {
      resume.current = false;
      // Reconcile mounted data/job lists once after a successful return from idle.
      void queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] !== "health",
        refetchType: "active",
      });
    }
  }, [q.isSuccess, q.isFetching, q.dataUpdatedAt]);

  return { ...q, paused: hosted && !awake && !busy };
}
