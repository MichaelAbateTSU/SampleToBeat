"""Arrangement schema validation + migration (spec 23/24)."""
import copy

import pytest
from pydantic import ValidationError

from choplab import schemas
from choplab.core import SCHEMA_VERSION, TICKS_PER_BAR


def base():
    return {
        "schema_version": SCHEMA_VERSION, "bpm": 90, "bars": 4, "key": "A minor", "swing": 55, "seed": 1,
        "genre": "boombap", "strategy": "classic", "kit": "boombap", "meta": {}, "sections": [{"name": "Main", "start_bar": 0, "bars": 4}],
        "master": {"target_lufs": -14.0}, "bass": {},
        "patterns": {
            "d": {"id": "d", "kind": "drums", "length_ticks": TICKS_PER_BAR, "events": [{"type": "drum", "tick": 0, "inst": "kick", "vel": 1}]},
            "b": {"id": "b", "kind": "bass", "length_ticks": TICKS_PER_BAR, "events": [{"type": "note", "tick": 0, "dur": 480, "midi": 33, "vel": 1}]},
        },
        "tracks": [
            {"id": "kick", "name": "Kick", "kind": "drums", "clips": [{"id": "c1", "pattern_id": "d", "start_bar": 0, "bars": 4}]},
            {"id": "bass", "name": "808", "kind": "bass", "clips": [{"id": "c2", "pattern_id": "b", "start_bar": 0, "bars": 4}]},
        ],
    }


def test_valid_arrangement_round_trips():
    a = schemas.arrangement(base())
    assert schemas.arrangement(a) == a


@pytest.mark.parametrize("mutate,msg", [
    (lambda a: a["patterns"]["d"]["events"].append({"type": "drum", "tick": TICKS_PER_BAR, "inst": "kick", "vel": 1}), "tick"),
    (lambda a: a["patterns"]["d"]["events"].append({"type": "note", "tick": 0, "dur": 10, "midi": 30, "vel": 1}), "drum"),
    (lambda a: a["tracks"][0]["clips"].append({"id": "c9", "pattern_id": "zz", "start_bar": 0, "bars": 1}), "missing pattern"),
    (lambda a: a["tracks"][0]["clips"][0].update(start_bar=2), "past the end"),
    (lambda a: a["tracks"][1]["clips"].append({"id": "c1", "pattern_id": "b", "start_bar": 0, "bars": 1}), "unique"),
    (lambda a: a["tracks"][0]["clips"][0].update(pattern_id="b"), "bass pattern"),
    (lambda a: a.update(bpm=500), "bpm"),
    (lambda a: a["patterns"]["d"].update(length_ticks=100), "greater than or equal to 120"),
    (lambda a: a["patterns"]["d"].update(length_ticks=130), "16ths"),
    (lambda a: a["tracks"][0].update(fx={"warp_drive": 1}), "fx"),
    (lambda a: a["bass"].update(sample_path="C:/Windows/win.ini", mode="sample"), "808 sample"),
    (lambda a: a["patterns"]["b"]["events"][0].update(midi=200), "midi"),
])
def test_invalid_arrangements_are_rejected(mutate, msg):
    a = copy.deepcopy(base())
    mutate(a)
    with pytest.raises(ValidationError) as e:
        schemas.arrangement(a)
    assert msg.lower() in str(e.value).lower()


def test_migration_from_v0_and_rejection_of_newer():
    a = base()
    del a["schema_version"], a["master"]
    out = schemas.arrangement(a)
    assert out["schema_version"] == SCHEMA_VERSION and out["master"]["target_lufs"] == -14.0
    with pytest.raises(ValueError, match="newer ChopLab"):
        schemas.arrangement({**base(), "schema_version": SCHEMA_VERSION + 1})


def test_request_models_bound_user_input():
    with pytest.raises(ValidationError):
        schemas.GenerateSettings(variations=50)
    with pytest.raises(ValidationError):
        schemas.AnalysisOverride(key="Q flat")
    with pytest.raises(ValidationError):
        schemas.SampleUpdate(transform={"rm_rf": True})
    with pytest.raises(ValidationError):
        schemas.SampleUpdate(transform={"pitch_semitones": 48})
    with pytest.raises(ValidationError):
        schemas.ExportRequest(format="exe")
    assert schemas.GenerateSettings().model_dump()["genre"] == "boombap"
