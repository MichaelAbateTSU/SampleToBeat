// Mirrors backend/src/choplab/schemas.py and the JSON returned by backend/src/choplab/api.py.

export type Genre = "boombap" | "trap" | "drill" | "lofi";
export type TrackKind = "sample" | "drums" | "bass";
export type JobStatus = "queued" | "running" | "completed" | "failed" | "canceled" | "interrupted";

export interface Job {
  id: string;
  project_id: string;
  job_type: string;
  status: JobStatus;
  progress: number;
  current_stage: string | null;
  error_message: string | null;
  result: Record<string, unknown> | null;
  created_at?: number;
}

export interface GenerateSettings {
  genre: Genre;
  bpm: number | null;
  bars: number;
  structure: "auto" | "standard" | "verse_hook" | "loop";
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

export type ExportFormat = "wav16" | "wav24" | "wav32f" | "mp3";
export interface ExportRequest {
  filename: string;
  format: ExportFormat;
  scope: "full" | "loop";
  loop_start_bar: number;
  loop_bars: number;
  stems: boolean;
  project_json: boolean;
}

export interface ProjectSummary {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  source_ids: string[];
  target_bpm: number | null;
  musical_key: string | null;
  has_arrangement: boolean;
}

export interface Project {
  id: string;
  name: string;
  schema_version: number;
  source_ids: string[];
  target_bpm: number | null;
  musical_key: string | null;
  arrangement: Arrangement | null;
  generator_settings: GenerateSettings;
  export_settings: ExportRequest;
  variation_id?: string;
  sources?: Source[];
  created_at: number;
  updated_at: number;
}

export interface Segment {
  start: number;
  end: number;
  label: string;
  energy?: number;
}

export interface Analysis {
  bpm: number;
  detected_bpm: number;
  bpm_candidates: { bpm: number; interpretation: string; score: number }[];
  beats: number[];
  downbeats: number[];
  downbeat_phase: number;
  grid_offset: number;
  key: string | null;
  detected_key: string | null;
  key_candidates: { key: string; score: number }[];
  key_confidence: number;
  segments: Segment[];
  tempo_stability: number;
  weak_grid: boolean;
  loudness_lufs: number;
  peak_dbfs: number;
  override?: Record<string, unknown> | null;
}

export interface AnalysisOverride {
  bpm?: number;
  grid_offset?: number;
  key?: string;
  downbeat_phase?: number;
}

export interface Source {
  id: string;
  project_id: string;
  original_filename: string;
  analysis_status: "queued" | "analyzing" | "analyzed" | "failed" | string;
  separation_status: string | null;
  stems: Record<string, string>;
  stem_model?: string | null;
  duration_seconds?: number;
  sample_rate?: number;
  channel_count?: number;
  peaks?: number[];
  error?: string | null;
  analysis?: Analysis;
}

export interface Transform {
  pitch_semitones: number;
  pitch_cents: number;
  stretch_mode: "stretch" | "tape";
  preserve_formants: boolean;
  reverse: boolean;
  gain_db: number;
  fade_in_ms: number;
  fade_out_ms: number;
}

export interface Sample {
  id: string;
  source_id: string;
  project_id?: string;
  origin: "auto" | "manual";
  kind: string;
  stems: string[];
  start: number;
  end: number;
  duration: number;
  beats: number;
  bars: number;
  pickup: number;
  source_bpm: number;
  key: string | null;
  score: number | null;
  metrics?: Record<string, number>;
  explanation?: string[];
  tags?: string[];
  peaks?: number[];
  name: string;
  favorite?: boolean;
  transform?: Partial<Transform>;
  slices?: [number, number][];
}

export interface DrumEvent {
  type: "drum";
  tick: number;
  inst: string;
  vel: number;
  roll?: boolean;
  reverse?: boolean;
  dur?: number;
}
export interface NoteEvent {
  type: "note";
  tick: number;
  dur: number;
  midi: number;
  vel: number;
  slide: boolean;
}
export interface ChopEvent {
  type: "chop";
  tick: number;
  dur: number;
  sample_id: string;
  s0: number;
  s1: number;
  ratio: number;
  pitch: number;
  vel: number;
  reverse?: boolean;
  gain_db?: number;
  pan?: number;
}
export type ArrEvent = DrumEvent | NoteEvent | ChopEvent;

export interface Pattern {
  id: string;
  kind: TrackKind;
  length_ticks: number;
  events: ArrEvent[];
  roots?: number[];
  [extra: string]: unknown;
}

export interface Clip {
  id: string;
  pattern_id: string;
  start_bar: number;
  bars: number;
}

export interface Fx {
  highpass_hz: number;
  lowpass_hz: number;
  saturation: number;
  bitcrush_bits: number;
  reverb: number;
  delay: number;
  delay_beats: number;
  width: number;
  compress: boolean;
  wobble: number;
  vinyl: number;
  eq_low_db: number;
  eq_mid_db: number;
  eq_high_db: number;
}

export interface Track {
  id: string;
  name: string;
  kind: TrackKind;
  color: string;
  volume_db: number;
  pan: number;
  muted: boolean;
  solo: boolean;
  choke: boolean;
  fx: Partial<Fx>;
  clips: Clip[];
}

export interface Section {
  name: string;
  start_bar: number;
  bars: number;
}

export interface BassSettings {
  mode: "synth" | "sample";
  decay: number;
  sustain: number;
  release_ms: number;
  attack_ms: number;
  pitch_env_semis: number;
  pitch_env_ms: number;
  glide_ms: number;
  harmonics: number;
  drive: number;
  gain_db: number;
  sample_path: string | null;
  root_midi: number;
  duck?: number;
}

export interface Arrangement {
  schema_version: number;
  bpm: number;
  time_signature?: "4/4";
  key: string | null;
  genre: Genre;
  kit: string;
  swing: number;
  bars: number;
  seed: number;
  strategy: string;
  sections: Section[];
  tracks: Track[];
  patterns: Record<string, Pattern>;
  bass: Partial<BassSettings>;
  master: { target_lufs: number; [k: string]: unknown };
  meta: Record<string, unknown> & { sample_names?: Record<string, string>; description?: string };
}

export interface RenderMetrics {
  duration_s: number;
  lufs: number;
  lufs_in?: number;
  peak_dbfs: number;
  true_peak_dbfs: number;
  clipped_samples: number;
  master_gain_db?: number;
  rms_dbfs?: number;
}

export interface EvalCheck {
  name: string;
  ok: boolean;
  detail: string;
  kind: string;
}

export interface Variation {
  id: string;
  project_id: string;
  batch: number;
  seed: number;
  genre: Genre;
  strategy: string;
  source_samples: string[];
  render_status: string;
  metrics: RenderMetrics;
  evaluation: { passed: boolean; checks: EvalCheck[]; musical?: Record<string, unknown>; attempts?: number };
  meta: Record<string, unknown> & { description?: string; strategy_detail?: string; sample_preset?: string; sample_names?: Record<string, string> };
  bpm: number;
  key: string | null;
  bars: number;
  sections: Section[];
  created_at: number;
  updated_at: number;
}

export interface ExportFile {
  kind: "mix" | "stem" | "zip" | "project";
  name: string;
  path: string;
  track?: string;
  check?: { duration_s: number; peak_dbfs: number; silent: boolean; channels: number; bytes: number };
}

export interface ExportRecord {
  id: string;
  project_id: string;
  request: ExportRequest;
  status: string;
  files?: ExportFile[];
  metrics?: RenderMetrics;
  folder?: string;
  created_at: number;
}

export interface GenreInfo {
  label: string;
  bpm: [number, number];
  swing: [number, number];
  kit: string;
}

export interface Capabilities {
  hosted?: boolean;
  metadata_store?: "sqlite" | "postgres";
  max_upload_mb?: number;
  max_audio_seconds?: number;
  ffmpeg: boolean;
  sample_rate: number;
  data_dir: string;
  disk_free_gb: number;
  schema_version: number;
  genres: Record<Genre, GenreInfo>;
  strategies: Record<string, string>;
  kits: string[];
  instruments: string[];
  track_of: Record<string, string>;
  fx_defaults: Fx;
  transform_defaults: Transform;
  bass_defaults: BassSettings;
  generate_defaults: GenerateSettings;
  formats: string[];
}

export interface ModelInfo {
  id: string;
  label: string;
  stems: string[];
  cpu: boolean;
  gpu: boolean;
  available: boolean;
  status: string;
  est_memory_gb: number;
}

export interface Stem {
  id: string;
  source_id: string;
  stem_type: string;
  model_id: string | null;
}

export interface PreviewResponse {
  url: string;
  metrics: RenderMetrics;
  start_bar: number;
}
