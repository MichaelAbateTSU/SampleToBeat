"""Separation engine. Adapters expose capabilities so models can be swapped; stems are cached by
content hash + model identity. A built-in HPSS adapter (honestly labelled 2-stem) keeps the app usable
without model weights."""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
import librosa

from . import audio_io
from .core import SR, cache_key, data_path, log


class Adapter:
    id: str
    label: str
    stems: list[str]
    cpu = True
    gpu = False
    est_memory_gb: float | None = None
    model_files: list[str] = []

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def info(self) -> dict:
        ok, why = self.available()
        return {"id": self.id, "label": self.label, "stems": self.stems, "cpu": self.cpu, "gpu": self.gpu,
                "model_files": self.model_files, "available": ok, "status": why, "est_memory_gb": self.est_memory_gb}

    def separate(self, wav: Path, out_dir: Path, progress) -> dict[str, Path]:
        raise NotImplementedError


class HPSSAdapter(Adapter):
    id, label, stems = "builtin-hpss", "Built-in harmonic/percussive split (fast, 2 stems)", ["harmonic", "percussive"]
    est_memory_gb = 0.5

    def separate(self, wav, out_dir, progress):
        y = audio_io.load(wav)
        progress("hpss", 0.3)
        out = {"harmonic": [], "percussive": []}
        for ch in range(2):
            stft = librosa.stft(y[:, ch])
            h, p = librosa.decompose.hpss(stft, margin=2.0)
            out["harmonic"].append(librosa.istft(h, length=len(y)))
            out["percussive"].append(librosa.istft(p, length=len(y)))
        return {k: audio_io.write_wav(out_dir / f"{k}.wav", np.stack(v, axis=1)) for k, v in out.items()}


class AudioSeparatorAdapter(Adapter):
    """Wraps python-audio-separator. Stem names are read from the model's real outputs."""

    def __init__(self, id: str, label: str, model_filename: str, stems: list[str]):
        self.id, self.label, self.model_filename, self.stems = id, label, model_filename, stems
        self.model_files = [model_filename]
        self.est_memory_gb = 3.0

    def available(self):
        try:
            from audio_separator.separator import Separator  # noqa: F401
        except Exception as e:
            return False, f"audio-separator not installed ({type(e).__name__}); run setup with separation extra"
        try:
            import torch

            self.gpu = bool(torch.cuda.is_available())
        except Exception:
            pass
        return True, "ok (model weights download on first use)"

    def separate(self, wav, out_dir, progress):
        from audio_separator.separator import Separator

        progress(f"loading model {self.model_filename}", 0.05)
        tmp = Path(tempfile.mkdtemp(dir=out_dir.parent))
        try:
            sep = Separator(model_file_dir=str(data_path("models", "x").parent), output_dir=str(tmp),
                            sample_rate=SR, use_soundfile=True)
            try:
                sep.load_model(model_filename=self.model_filename)
            except Exception as e:
                raise RuntimeError(f"Model '{self.model_filename}' could not be loaded/downloaded: {e}") from e
            progress("separating (no fine-grained progress from model)", 0.2)
            try:
                files = sep.separate(str(wav))
            except RuntimeError as e:
                if "out of memory" in str(e).lower():
                    raise RuntimeError("GPU out of memory; retry the job - the CPU will be used.") from e
                raise
            result = {}
            for f in files:
                m = re.search(r"\(([^)]+)\)", Path(f).name)
                stem = (m.group(1) if m else Path(f).stem).lower()
                y = audio_io.load(tmp / Path(f).name if not Path(f).is_absolute() else Path(f))
                result[stem] = audio_io.write_wav(out_dir / f"{stem}.wav", y)
            return result
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


ADAPTERS: dict[str, Adapter] = {a.id: a for a in [
    AudioSeparatorAdapter("demucs-fast", "Fast: Demucs v4 htdemucs (4 stems)", "htdemucs.yaml",
                          ["vocals", "drums", "bass", "other"]),
    AudioSeparatorAdapter("demucs-quality", "Quality: Demucs v4 htdemucs_ft (4 stems, ~4x slower)",
                          "htdemucs_ft.yaml", ["vocals", "drums", "bass", "other"]),
    HPSSAdapter(),
]}


def separate(source: dict, model_id: str, progress) -> dict[str, str]:
    adapter = ADAPTERS[model_id]
    ok, why = adapter.available()
    if not ok:
        raise RuntimeError(why)
    key = cache_key("sep", source["content_hash"], model_id)
    out_dir = data_path("cache", "stems", key, "x").parent
    done = out_dir / "done.txt"
    if done.exists():  # cached; never rerun identical separation
        log.info("stems cache hit %s", key)
        return {p.stem: str(p) for p in out_dir.glob("*.wav")}
    files = adapter.separate(Path(source["internal_audio_path"]), out_dir, progress)
    done.write_text(model_id)
    return {k: str(v) for k, v in files.items()}
