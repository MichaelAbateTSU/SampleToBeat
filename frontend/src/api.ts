// Typed client for the ChopLab HTTP API. All calls are same-origin and carry the session cookie.

export type PlanId = "free" | "pro" | "studio";

export interface Plan {
  label: string;
  tagline: string;
  price_monthly: number;
  price_yearly: number;
  generations_per_month: number;
  projects: number;
  max_upload_mb: number;
  max_source_minutes: number;
  formats: string[];
  stems: boolean;
  midi: boolean;
  project_file: boolean;
  separation: boolean;
  commercial_license: boolean;
}

export interface Me {
  user: { id: string; email: string | null; guest: boolean; plan: PlanId; subscription_status: string | null; current_period_end: number | null; cancel_at_period_end: boolean } | null;
  plan: Plan;
  usage: { generations_used: number; generations_limit: number; generations_left: number; period: string; projects_limit: number } | null;
  self_hosted: boolean;
  billing: "off" | "mock" | "stripe";
}

export interface Plans {
  plans: Record<PlanId, Plan>;
  order: PlanId[];
  guest_generations: number;
  billing: Me["billing"];
}

export interface Genre { label: string; bpm: [number, number]; swing: [number, number]; kit: string; half_time?: boolean }

export interface Capabilities {
  genres: Record<string, Genre>;
  strategies: Record<string, string>;
  kits: Record<string, string>;
  formats: string[];
  generate_defaults: GenerateSettings;
}

export interface GenerateSettings {
  genre: string;
  bpm: number | null;
  bars: number;
  structure: string;
  energy: number;
  chop_intensity: number;
  bass_intensity: number;
  drum_complexity: number;
  swing: number | null;
  variations: number;
  sample_mode: "auto" | "manual";
  sample_ids: string[];
  seed: number | null;
}

export interface Job {
  id: string;
  project_id: string;
  job_type: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled" | "interrupted";
  progress: number | null;
  current_stage: string;
  error_message: string | null;
  result: Record<string, unknown> | null;
  created_at: number;
}

export interface Source {
  id: string;
  original_filename: string;
  analysis_status: string;
  duration_seconds?: number;
  peaks?: number[];
  error?: string;
  analysis?: { bpm: number; key: string; key_confidence: number; one_shot: boolean; loop_locked: boolean; duration: number };
}

export interface Clip { id: string; pattern_id: string; start_bar: number; bars: number }

export interface Track {
  id: string;
  name: string;
  kind: "sample" | "drums" | "bass";
  color: string;
  volume_db: number;
  pan: number;
  muted: boolean;
  solo: boolean;
  fx: Record<string, number>;
  clips: Clip[];
}

export interface Section { name: string; start_bar: number; bars: number }

export interface Arrangement {
  bpm: number;
  key: string | null;
  genre: string;
  kit: string;
  swing: number;
  bars: number;
  strategy: string;
  sections: Section[];
  tracks: Track[];
  patterns: Record<string, { id: string; kind: string; length_ticks: number; events: unknown[] }>;
  bass: Record<string, number | string | null>;
  meta: Record<string, unknown>;
  [k: string]: unknown;
}

export interface Project {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  source_ids: string[];
  target_bpm: number | null;
  musical_key: string | null;
  variation_id?: string | null;
  genre?: string | null;
  arrangement: Arrangement | null;
  generator_settings: GenerateSettings;
  sources: Source[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  updated_at: number;
  target_bpm: number | null;
  musical_key: string | null;
  variation_id: string | null;
  genre: string | null;
  has_arrangement: boolean;
}

export interface Variation {
  id: string;
  project_id: string;
  batch: number;
  strategy: string;
  genre: string;
  bpm: number;
  key: string | null;
  bars: number;
  sections: Section[];
  peaks?: number[];
  duration?: number;
  created_at: number;
  meta: { description: string; strategy_detail: string; sample_names: Record<string, string>; main_sample: string; pitch: number };
  evaluation: { passed: boolean };
  metrics: { lufs: number | null; true_peak_dbfs: number };
}

export interface Sample {
  id: string;
  name: string;
  kind: "loop" | "chop" | "oneshot";
  beats: number;
  bars: number;
  duration: number;
  score: number | null;
  tags: string[];
  explanation: string[];
  peaks: number[];
  full?: boolean;
}

export interface ExportFile { kind: string; name: string; check?: { duration_s: number; bytes: number } }
export interface ExportDoc { id: string; status: string; files?: ExportFile[]; error?: string }
export interface ExportRequest { format: string; stems: boolean; midi: boolean; project_json: boolean; filename: string }

export interface PreviewResult {
  url: string;
  peaks: number[];
  duration: number;
  metrics: { lufs: number | null; true_peak_dbfs: number };
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
    public feature?: string,
    public planNeeded?: PlanId,
  ) {
    super(message);
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError("Can't reach ChopLab. Check your connection and try again.", 0);
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : `Request failed (${res.status})`;
    throw new ApiError(msg, res.status, data?.code, data?.feature, data?.plan_needed);
  }
  return data as T;
}

export const api = {
  me: () => req<Me>("GET", "/api/me"),
  plans: () => req<Plans>("GET", "/api/plans"),
  capabilities: () => req<Capabilities>("GET", "/api/system/capabilities"),
  register: (email: string, password: string) => req<Me>("POST", "/api/auth/register", { email, password }),
  login: (email: string, password: string) => req<Me>("POST", "/api/auth/login", { email, password }),
  logout: () => req<{ ok: boolean }>("POST", "/api/auth/logout"),
  forgot: (email: string) => req<{ ok: boolean }>("POST", "/api/auth/forgot", { email }),
  reset: (token: string, password: string) => req<Me>("POST", "/api/auth/reset", { token, password }),
  deleteAccount: () => req<void>("DELETE", "/api/account"),
  checkout: (plan: PlanId, interval: "monthly" | "yearly") => req<{ url: string }>("POST", "/api/billing/checkout", { plan, interval }),
  portal: () => req<{ url: string }>("POST", "/api/billing/portal"),

  projects: () => req<ProjectSummary[]>("GET", "/api/projects"),
  project: (id: string) => req<Project>("GET", `/api/projects/${id}`),
  renameProject: (id: string, name: string) => req<Project>("PATCH", `/api/projects/${id}`, { name }),
  deleteProject: (id: string) => req<void>("DELETE", `/api/projects/${id}`),
  jobs: (projectId: string) => req<Job[]>("GET", `/api/projects/${projectId}/jobs`),

  makeBeat: (file: File | null, settings: Partial<GenerateSettings>) => {
    const fd = new FormData();
    if (file) fd.append("file", file);
    fd.append("settings", JSON.stringify(settings));
    return req<{ project: Project; job: Job }>("POST", "/api/beats", fd);
  },
  generate: (projectId: string, settings: Partial<GenerateSettings>) => req<Job>("POST", `/api/projects/${projectId}/generate`, settings),
  variations: (projectId: string) => req<Variation[]>("GET", `/api/projects/${projectId}/variations`),
  regenerate: (variationId: string) => req<Job>("POST", `/api/variations/${variationId}/regenerate`),
  deleteVariation: (variationId: string) => req<void>("DELETE", `/api/variations/${variationId}`),
  openInStudio: (variationId: string) => req<Project>("POST", `/api/variations/${variationId}/open-in-studio`),
  samples: (projectId: string) => req<Sample[]>("GET", `/api/projects/${projectId}/samples`),
  overrideAnalysis: (sourceId: string, body: { bpm?: number; key?: string }) => req<Job>("PATCH", `/api/sources/${sourceId}/analysis`, body),

  saveArrangement: (projectId: string, arr: Arrangement) => req<Arrangement>("PUT", `/api/projects/${projectId}/arrangement`, arr),
  preview: (projectId: string, arrangement?: Arrangement) => req<PreviewResult>("POST", `/api/projects/${projectId}/preview`, { scope: "full", arrangement }),

  startExport: (projectId: string, body: ExportRequest) => req<{ export_id: string; job: Job }>("POST", `/api/projects/${projectId}/export`, body),
  getExport: (id: string) => req<ExportDoc>("GET", `/api/exports/${id}`),
  cancelJob: (id: string) => req<Job>("POST", `/api/jobs/${id}/cancel`),
  job: (id: string) => req<Job>("GET", `/api/jobs/${id}`),
};

export const TERMINAL = new Set(["completed", "failed", "canceled", "interrupted"]);

/** Follows a job over server-sent events (falling back to polling) until it reaches a terminal state. */
export function watchJob(id: string, onUpdate: (job: Job) => void): { done: Promise<Job>; stop: () => void } {
  let stopped = false;
  let es: EventSource | null = null;
  let timer: number | undefined;
  const done = new Promise<Job>((resolve) => {
    const finish = (job: Job) => {
      stopped = true;
      es?.close();
      window.clearTimeout(timer);
      resolve(job);
    };
    const poll = async () => {
      if (stopped) return;
      try {
        const job = await api.job(id);
        onUpdate(job);
        if (TERMINAL.has(job.status)) return finish(job);
      } catch {
        /* transient: keep polling */
      }
      timer = window.setTimeout(poll, 1000);
    };
    try {
      es = new EventSource(`/api/jobs/${id}/events`);
      es.onmessage = (ev) => {
        const job = JSON.parse(ev.data) as Job;
        onUpdate(job);
        if (TERMINAL.has(job.status)) finish(job);
      };
      es.onerror = () => {
        es?.close();
        es = null;
        if (!stopped) poll();
      };
    } catch {
      poll();
    }
  });
  return {
    done,
    stop: () => {
      stopped = true;
      es?.close();
      window.clearTimeout(timer);
    },
  };
}

export const variationAudio = (id: string) => `/api/variations/${id}/audio?format=mp3`;
export const sampleAudio = (id: string) => `/api/samples/${id}/audio`;
export const exportFileUrl = (id: string, name: string) => `/api/exports/${id}/download?name=${encodeURIComponent(name)}`;
