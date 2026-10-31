import type {
  AnalysisOverride, Arrangement, Capabilities, ExportRecord, ExportRequest, GenerateSettings, Job, ModelInfo, Pattern,
  PreviewResponse, Project, ProjectSummary, Sample, Source, Stem, Transform, Variation,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((x) => {
          if (x && typeof x === "object" && "msg" in x) {
            const loc = (x as { loc?: unknown[] }).loc?.slice(1).join(".");
            return `${loc ? loc + ": " : ""}${(x as { msg: string }).msg}`;
          }
          return String(x);
        })
        .join("; ");
    }
  }
  return fallback;
}

export async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError(0, "Cannot reach the ChopLab backend. Is it running?");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let parsed: unknown = undefined;
  try {
    parsed = text ? JSON.parse(text) : undefined;
  } catch {
    parsed = text;
  }
  if (!res.ok) throw new ApiError(res.status, detail(parsed, `${res.status} ${res.statusText}`));
  return parsed as T;
}

const get = <T,>(u: string) => request<T>("GET", u);
const post = <T,>(u: string, b?: unknown) => request<T>("POST", u, b ?? {});
const patch = <T,>(u: string, b: unknown) => request<T>("PATCH", u, b);
const put = <T,>(u: string, b: unknown) => request<T>("PUT", u, b);
const del = (u: string) => request<void>("DELETE", u);

function upload<T>(url: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  return request<T>("POST", url, fd);
}

export const api = {
  health: () => get<{ ok: boolean; version: string; engine: string }>("/api/health"),
  capabilities: () => get<Capabilities>("/api/system/capabilities"),
  models: () => get<ModelInfo[]>("/api/system/models"),
  clearCache: () => request<{ freed_mb: number }>("DELETE", "/api/system/cache"),

  projects: () => get<ProjectSummary[]>("/api/projects"),
  project: (id: string) => get<Project>(`/api/projects/${id}`),
  createProject: (name: string) => post<Project>("/api/projects", { name }),
  renameProject: (id: string, name: string) => patch<Project>(`/api/projects/${id}`, { name }),
  deleteProject: (id: string) => del(`/api/projects/${id}`),

  uploadSource: (pid: string, file: File) => upload<{ source: Source; job: Job }>(`/api/projects/${pid}/sources`, file),
  demoSource: (pid: string) => post<{ source: Source; job: Job }>(`/api/projects/${pid}/demo`),
  source: (id: string) => get<Source>(`/api/sources/${id}`),
  waveform: (id: string) => get<{ peaks: number[]; duration: number | null }>(`/api/sources/${id}/waveform`),
  reanalyze: (id: string) => post<Job>(`/api/sources/${id}/analyze`),
  overrideAnalysis: (id: string, body: AnalysisOverride) => patch<Job>(`/api/sources/${id}/analysis`, body),
  separate: (id: string, model_id: string) => post<Job>(`/api/sources/${id}/separate`, { model_id }),
  stems: (id: string) => get<Stem[]>(`/api/sources/${id}/stems`),

  samples: (pid: string) => get<Sample[]>(`/api/projects/${pid}/samples`),
  createSample: (pid: string, body: { source_id: string; start: number; end: number; stems: string[]; name?: string }) =>
    post<Sample>(`/api/projects/${pid}/samples`, body),
  updateSample: (id: string, body: Partial<Pick<Sample, "name" | "start" | "end" | "favorite">> & { transform?: Partial<Transform> }) =>
    patch<Sample>(`/api/samples/${id}`, body),
  deleteSample: (id: string) => del(`/api/samples/${id}`),

  generate: (pid: string, s: GenerateSettings) => post<Job>(`/api/projects/${pid}/generate`, s),
  variations: (pid: string) => get<Variation[]>(`/api/projects/${pid}/variations`),
  regenerate: (id: string) => post<Job>(`/api/variations/${id}/regenerate`),
  deleteVariation: (id: string) => del(`/api/variations/${id}`),
  openInStudio: (id: string) => post<Project>(`/api/variations/${id}/open-in-studio`),

  arrangement: (pid: string) => get<Arrangement>(`/api/projects/${pid}/arrangement`),
  saveArrangement: (pid: string, a: Arrangement) => put<Arrangement>(`/api/projects/${pid}/arrangement`, a),
  renderPreview: (pid: string, body: { scope: "full" | "loop"; loop_start_bar?: number; loop_bars?: number; arrangement?: Arrangement }) =>
    post<PreviewResponse>(`/api/projects/${pid}/preview`, body),
  uploadBassSample: (pid: string, file: File) => upload<{ sample_path: string }>(`/api/projects/${pid}/bass-sample`, file),

  drumPattern: (body: { genre: string; complexity: number; seed: number; bars: number; variant?: string }) =>
    post<Pick<Pattern, "length_ticks" | "events">>("/api/patterns/drums", body),
  bassPattern: (body: { genre: string; roots: number[]; kick_ticks: number[]; key: string | null; seed: number; complexity: number }) =>
    post<Pick<Pattern, "length_ticks" | "events">>("/api/patterns/bass", body),

  exportProject: (pid: string, body: ExportRequest) => post<{ export_id: string; job: Job }>(`/api/projects/${pid}/export`, body),
  exports: (pid: string) => get<ExportRecord[]>(`/api/projects/${pid}/exports`),
  exportRecord: (id: string) => get<ExportRecord>(`/api/exports/${id}`),
  reveal: (id: string) => post<{ folder: string }>(`/api/exports/${id}/reveal`),

  kits: () => get<Record<string, Record<string, boolean>>>("/api/kits"),
  uploadKitSound: (kit: string, inst: string, file: File) => upload<unknown>(`/api/kits/${kit}/${inst}`, file),
  resetKitSound: (kit: string, inst: string) => del(`/api/kits/${kit}/${inst}`),

  job: (id: string) => get<Job>(`/api/jobs/${id}`),
  projectJobs: (pid: string) => get<Job[]>(`/api/projects/${pid}/jobs`),
  cancelJob: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
  retryJob: (id: string) => post<Job>(`/api/jobs/${id}/retry`),
};

export const urls = {
  sourceAudio: (id: string) => `/api/sources/${id}/audio`,
  stemAudio: (sourceId: string, stem: string) => `/api/stems/${sourceId}.${stem}/audio`,
  sampleAudio: (id: string, bpm?: number, v?: number) => {
    const q = new URLSearchParams();
    if (bpm) q.set("bpm", String(bpm));
    if (v) q.set("v", String(v));
    const s = q.toString();
    return `/api/samples/${id}/audio${s ? `?${s}` : ""}`;
  },
  sampleBuffer: (id: string, bpm: number, ratio: number, pitch: number, v?: number) =>
    `/api/samples/${id}/buffer?bpm=${bpm}&ratio=${ratio}&pitch=${pitch}${v ? `&v=${v}` : ""}`,
  variationAudio: (id: string, v?: number) => `/api/variations/${id}/audio${v ? `?v=${v}` : ""}`,
  kitAudio: (kit: string, inst: string, v?: number) => `/api/kits/${kit}/${inst}/audio${v ? `?v=${v}` : ""}`,
  download: (exportId: string, name: string) => `/api/exports/${exportId}/download?name=${encodeURIComponent(name)}`,
  jobEvents: (id: string) => `/api/jobs/${id}/events`,
};
