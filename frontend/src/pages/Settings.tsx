import { useMutation } from "@tanstack/react-query";
import { Play, RotateCcw, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api, urls } from "../api/client";
import { keys, queryClient, useCapabilities, useKits, useModels } from "../api/hooks";
import { usePreview } from "../audio/player";
import { engine } from "../audio/engine";
import { Badge, Button, Card, ErrorBanner, IconButton, Spinner, Stat } from "../components/ui";

export default function SettingsPage() {
  const caps = useCapabilities();
  const models = useModels();
  const kits = useKits();
  const play = usePreview((s) => s.play);
  const [kitV, setKitV] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const [target, setTarget] = useState<{ kit: string; inst: string } | null>(null);
  const [freed, setFreed] = useState<number | null>(null);

  const clear = useMutation({ mutationFn: api.clearCache, onSuccess: (r) => setFreed(r.freed_mb) });
  const afterKitChange = () => {
    queryClient.invalidateQueries({ queryKey: keys.kits });
    engine.reloadKit();
    setKitV(Date.now());
  };
  const upload = useMutation({ mutationFn: ({ kit, inst, file }: { kit: string; inst: string; file: File }) => api.uploadKitSound(kit, inst, file), onSuccess: afterKitChange });
  const reset = useMutation({ mutationFn: ({ kit, inst }: { kit: string; inst: string }) => api.resetKitSound(kit, inst), onSuccess: afterKitChange });

  const c = caps.data;
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <ErrorBanner error={caps.error ?? models.error ?? kits.error} />

      <Card title="System">
        {!c ? <Spinner /> : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Sample rate" value={`${c.sample_rate / 1000} kHz`} />
            <Stat label="FFmpeg (MP3)" value={c.ffmpeg ? <Badge tone="good">available</Badge> : <Badge tone="warn">missing</Badge>} hint="Needed for MP3 import/export" />
            <Stat label="Free disk" value={`${c.disk_free_gb.toFixed(1)} GB`} />
            <Stat label="Data folder" value={<span className="break-all text-xs">{c.data_dir}</span>} />
          </div>
        )}
        <div className="mt-4 flex items-center gap-3">
          <Button onClick={() => clear.mutate()} busy={clear.isPending}><Trash2 className="size-4" /> Clear render cache</Button>
          {freed !== null && <span className="text-sm text-ink-400">Freed {freed.toFixed(1)} MB</span>}
          <ErrorBanner error={clear.error} />
        </div>
      </Card>

      <Card title="Stem separation models">
        {!models.data ? <Spinner /> : (
          <div className="flex flex-col divide-y divide-ink-700">
            {models.data.map((m) => (
              <div key={m.id} data-testid={`model-${m.id}`} className="flex items-center gap-3 py-2 text-sm">
                <div className="flex-1">
                  <div className="font-medium">{m.label}</div>
                  <div className="text-xs text-ink-400">Stems: {m.stems.join(", ")} · ~{m.est_memory_gb} GB RAM · {m.gpu ? "GPU accelerated when available" : "CPU"}</div>
                </div>
                <Badge tone={m.available ? "good" : "warn"} title={m.status}>{m.available ? "available" : "not installed"}</Badge>
              </div>
            ))}
            <p className="pt-2 text-xs text-ink-400">Demucs models need the optional ML dependencies: run the setup script with <code>--with-separation</code> (or <code>uv sync --extra separation</code> in <code>backend/</code>).</p>
          </div>
        )}
      </Card>

      <Card title="Drum kits" actions={<span className="text-xs text-ink-400">Replace any built-in sound with your own one-shot (WAV/MP3/FLAC).</span>}>
        <input ref={fileRef} type="file" accept="audio/*" className="hidden" onChange={(e) => {
          const f = e.target.files?.[0];
          if (f && target) upload.mutate({ ...target, file: f });
          e.target.value = "";
        }} />
        <ErrorBanner error={upload.error ?? reset.error} />
        {!kits.data ? <Spinner /> : (
          <div className="flex flex-col gap-4">
            {Object.entries(kits.data).map(([kit, insts]) => (
              <div key={kit}>
                <div className="mb-1.5 text-sm font-medium capitalize">{kit}</div>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(insts).map(([inst, custom]) => (
                    <div key={inst} className="flex items-center gap-1 rounded-md border border-ink-700 bg-ink-850 py-1 pr-1 pl-2.5 text-xs">
                      <span className="w-14">{inst}</span>
                      {custom && <Badge tone="accent">custom</Badge>}
                      <IconButton label={`Play ${kit} ${inst}`} onClick={() => play(urls.kitAudio(kit, inst, kitV), `${kit} ${inst}`)}><Play className="size-3.5" /></IconButton>
                      <IconButton label={`Replace ${kit} ${inst}`} onClick={() => { setTarget({ kit, inst }); fileRef.current?.click(); }}><Upload className="size-3.5" /></IconButton>
                      {custom && <IconButton label={`Reset ${kit} ${inst}`} onClick={() => reset.mutate({ kit, inst })}><RotateCcw className="size-3.5" /></IconButton>}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {c && (
        <Card title="Genres">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {Object.entries(c.genres).map(([g, info]) => (
              <div key={g} className="text-sm">
                <span className="font-medium">{info.label}</span>
                <span className="text-ink-400"> · {info.bpm[0]}–{info.bpm[1]} BPM · swing {info.swing[0]}–{info.swing[1]}% · {info.kit} kit</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
