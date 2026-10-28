"""Validation at the trust boundary (spec 21): everything the UI or an imported project file sends is
checked here before it reaches the renderer or the store."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import bass, drums, transform
from .core import DATA_DIR, SCHEMA_VERSION, STEP_TICKS, TICKS_PER_BAR, parse_key

Tick = Annotated[int, Field(ge=0, le=TICKS_PER_BAR * 512)]
Vel = Annotated[float, Field(ge=0, le=1.5)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DrumEvent(_M):
    type: Literal["drum"]
    tick: Tick
    inst: Literal[tuple(drums.INSTRUMENTS)]  # type: ignore[valid-type]
    vel: Vel = 1.0
    roll: bool | None = None
    reverse: bool | None = None
    dur: Annotated[int, Field(gt=0)] | None = None


class NoteEvent(_M):
    type: Literal["note"]
    tick: Tick
    dur: Annotated[int, Field(gt=0)]
    midi: Annotated[int, Field(ge=12, le=96)]
    vel: Vel = 1.0
    slide: bool = False


class ChopEvent(_M):
    type: Literal["chop"]
    tick: Tick
    dur: Annotated[int, Field(gt=0)]
    sample_id: str
    s0: Annotated[float, Field(ge=0)]
    s1: float
    ratio: Annotated[float, Field(gt=0.1, le=8)] = 1.0
    pitch: Annotated[float, Field(ge=-24, le=24)] = 0.0
    vel: Vel = 1.0
    reverse: bool | None = None
    gain_db: Annotated[float, Field(ge=-60, le=24)] | None = None
    pan: Annotated[float, Field(ge=-1, le=1)] | None = None

    @model_validator(mode="after")
    def _order(self):
        if self.s1 <= self.s0:
            raise ValueError("chop end must be after its start")
        return self


Event = Annotated[DrumEvent | NoteEvent | ChopEvent, Field(discriminator="type")]
KIND_EVENT = {"sample": "chop", "drums": "drum", "bass": "note"}


class Pattern(BaseModel):
    model_config = ConfigDict(extra="allow")  # e.g. analysed bass roots ride along for display
    id: str
    kind: Literal["sample", "drums", "bass"]
    length_ticks: Annotated[int, Field(ge=STEP_TICKS, le=TICKS_PER_BAR * 64)]
    events: Annotated[list[Event], Field(max_length=20000)]

    @model_validator(mode="after")
    def _check(self):
        if self.length_ticks % STEP_TICKS:
            raise ValueError(f"pattern {self.id}: length must be a whole number of 16ths")
        for e in self.events:
            if e.type != KIND_EVENT[self.kind]:
                raise ValueError(f"pattern {self.id}: {e.type} event in a {self.kind} pattern")
            if e.tick >= self.length_ticks:
                raise ValueError(f"pattern {self.id}: event at tick {e.tick} is past the pattern end")
        return self


class Clip(_M):
    id: str
    pattern_id: str
    start_bar: Annotated[int, Field(ge=0)]
    bars: Annotated[int, Field(ge=1)]


class Track(_M):
    id: str
    name: Annotated[str, Field(max_length=80)]
    kind: Literal["sample", "drums", "bass"]
    color: str = "#888888"
    volume_db: Annotated[float, Field(ge=-60, le=12)] = 0.0
    pan: Annotated[float, Field(ge=-1, le=1)] = 0.0
    muted: bool = False
    solo: bool = False
    choke: bool = False
    fx: dict = {}
    clips: list[Clip] = []

    @field_validator("fx")
    @classmethod
    def _fx(cls, v):
        bad = set(v) - set(transform.DEFAULT_FX)
        if bad:
            raise ValueError(f"unknown effect settings: {sorted(bad)}")
        return v


class Section(_M):
    name: str
    start_bar: Annotated[int, Field(ge=0)]
    bars: Annotated[int, Field(ge=1)]


class Arrangement(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: int = SCHEMA_VERSION
    bpm: Annotated[float, Field(ge=40, le=220)]
    time_signature: Literal["4/4"] = "4/4"
    key: str | None = None
    genre: Literal[tuple(drums.GENRES)]  # type: ignore[valid-type]
    kit: Literal[tuple(drums.KITS)]  # type: ignore[valid-type]
    swing: Annotated[float, Field(ge=50, le=75)] = 50
    bars: Annotated[int, Field(ge=1, le=256)]
    seed: int = 0
    strategy: str = "manual"
    sections: list[Section] = []
    tracks: Annotated[list[Track], Field(max_length=32)]
    patterns: dict[str, Pattern]
    bass: dict = {}
    master: dict = {"target_lufs": -14.0}
    meta: dict = {}

    @field_validator("bass")
    @classmethod
    def _bass(cls, v):
        bad = set(v) - set(bass.DEFAULT_808) - {"duck"}
        if bad:
            raise ValueError(f"unknown 808 settings: {sorted(bad)}")
        p = v.get("sample_path")
        if p and DATA_DIR.resolve() not in Path(p).resolve().parents:
            raise ValueError("808 sample must be a file imported into ChopLab")
        return v

    @model_validator(mode="after")
    def _refs(self):
        if len({t.id for t in self.tracks}) != len(self.tracks):
            raise ValueError("track ids must be unique")
        clip_ids = [c.id for t in self.tracks for c in t.clips]
        if len(set(clip_ids)) != len(clip_ids):
            raise ValueError("clip ids must be unique")
        for pid, p in self.patterns.items():
            if p.id != pid:
                raise ValueError(f"pattern key {pid} does not match its id {p.id}")
        for t in self.tracks:
            for c in t.clips:
                p = self.patterns.get(c.pattern_id)
                if p is None:
                    raise ValueError(f"clip {c.id} on {t.name} uses missing pattern {c.pattern_id}")
                if p.kind != t.kind:
                    raise ValueError(f"clip {c.id}: {p.kind} pattern on a {t.kind} track")
                if c.start_bar + c.bars > self.bars:
                    raise ValueError(f"clip {c.id} on {t.name} runs past the end of the arrangement")
        return self


def migrate(doc: dict) -> dict:
    """Upgrade stored/imported arrangements to the current schema; refuse files from a newer ChopLab."""
    v = int(doc.get("schema_version", 0))
    if v > SCHEMA_VERSION:
        raise ValueError(f"This project was saved by a newer ChopLab (schema {v}); this version reads up to {SCHEMA_VERSION}.")
    if v == 0:  # pre-release drafts had no version and no master section
        doc = {**doc, "schema_version": 1, "master": doc.get("master") or {"target_lufs": -14.0}}
    return doc


def arrangement(doc: dict) -> dict:
    """Migrate + validate; returns a plain dict without unset optional fields."""
    return Arrangement.model_validate(migrate(doc)).model_dump(exclude_none=True)


class GenerateSettings(_M):
    genre: Literal[tuple(drums.GENRES)] = "boombap"  # type: ignore[valid-type]
    bpm: Annotated[float, Field(ge=60, le=180)] | None = None
    bars: Annotated[int, Field(ge=4, le=128)] = 36
    structure: Literal["auto", "standard", "verse_hook", "loop"] = "auto"
    energy: Annotated[float, Field(ge=0, le=1)] = 0.6
    chop_intensity: Annotated[float, Field(ge=0, le=1)] = 0.5
    bass_intensity: Annotated[float, Field(ge=0, le=1)] = 0.6
    drum_complexity: Annotated[float, Field(ge=0, le=1)] = 0.5
    swing: Annotated[float, Field(ge=50, le=75)] | None = None
    variations: Annotated[int, Field(ge=1, le=8)] = 4
    sample_mode: Literal["auto", "manual"] = "auto"
    sample_ids: Annotated[list[str], Field(max_length=8)] = []
    seed: Annotated[int, Field(ge=0, le=2 ** 31)] | None = None


class ExportRequest(_M):
    filename: Annotated[str, Field(max_length=120)] = "beat"
    format: Literal["wav16", "wav24", "wav32f", "mp3"] = "wav24"
    scope: Literal["full", "loop"] = "full"
    loop_start_bar: Annotated[int, Field(ge=0)] = 0
    loop_bars: Annotated[int, Field(ge=1, le=64)] = 4
    stems: bool = False
    project_json: bool = True


class ProjectCreate(_M):
    name: Annotated[str, Field(min_length=1, max_length=80)] = "Untitled beat"


class ProjectPatch(_M):
    name: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    generator_settings: GenerateSettings | None = None
    export_settings: ExportRequest | None = None


class PreviewRequest(_M):
    scope: Literal["full", "loop"] = "full"
    loop_start_bar: Annotated[int, Field(ge=0)] = 0
    loop_bars: Annotated[int, Field(ge=1, le=64)] = 4
    arrangement: dict | None = None  # render unsaved edits without persisting them


class AnalysisOverride(_M):
    bpm: Annotated[float, Field(ge=40, le=220)] | None = None
    grid_offset: Annotated[float, Field(ge=0, le=30)] | None = None
    key: str | None = None
    downbeat_phase: Annotated[int, Field(ge=0, le=3)] | None = None

    @field_validator("key")
    @classmethod
    def _key(cls, v):
        if v and parse_key(v) is None:
            raise ValueError("key must look like 'A minor' or 'C# major'")
        return v


class SampleCreate(_M):
    source_id: str
    start: Annotated[float, Field(ge=0)]
    end: Annotated[float, Field(gt=0)]
    stems: Annotated[list[str], Field(min_length=1)] = ["mix"]
    name: Annotated[str, Field(max_length=80)] | None = None


class SampleUpdate(_M):
    name: Annotated[str, Field(max_length=80)] | None = None
    start: Annotated[float, Field(ge=0)] | None = None
    end: Annotated[float, Field(gt=0)] | None = None
    favorite: bool | None = None
    transform: dict | None = None

    @field_validator("transform")
    @classmethod
    def _t(cls, v):
        if v is not None and set(v) - set(transform.DEFAULT_TRANSFORM):
            raise ValueError(f"unknown transform settings: {sorted(set(v) - set(transform.DEFAULT_TRANSFORM))}")
        if v and abs(float(v.get("pitch_semitones", 0))) > 24:
            raise ValueError("pitch must be within ±24 semitones")
        limits = {"pitch_cents": (-100, 100), "gain_db": (-48, 24), "fade_in_ms": (0, 5000), "fade_out_ms": (0, 5000)}
        for k, (lo, hi) in limits.items():
            if v and k in v and not lo <= float(v[k]) <= hi:
                raise ValueError(f"{k} must be within {lo}..{hi}")
        if v and v.get("stretch_mode", "stretch") not in ("stretch", "tape"):
            raise ValueError("stretch_mode must be 'stretch' or 'tape'")
        return v
