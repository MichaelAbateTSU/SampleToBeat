"""Synthetic data and exact paired-restore checks, run only in disposable CI containers."""
import argparse
import json
import wave
from pathlib import Path

from choplab import store
from choplab.core import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["seed", "verify", "reset-sqlite", "cleanup"])
    args = parser.parse_args()
    audio = DATA_DIR / "backup-test.wav"
    if args.phase == "seed":
        with wave.open(str(audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b"\x01\x00" * 4410)
        store.put("project", {"id": "backup-test", "name": "Paired recovery", "audio_path": str(audio)})
    elif args.phase == "cleanup":
        store.delete("project", "backup-test")
        audio.unlink()
    elif args.phase == "reset-sqlite":
        assert store.backend_name() == "sqlite"
        for name in ("choplab.sqlite3", "choplab.sqlite3-wal", "choplab.sqlite3-shm"):
            (DATA_DIR / name).unlink(missing_ok=True)
    else:
        import sys

        snapshot = json.load(sys.stdin)
        assert store.export_snapshot()["rows"] == snapshot["rows"], "Restored metadata differs from backup."
        assert store.get("project", "backup-test")["audio_path"] == str(audio)
        with wave.open(str(audio), "rb") as restored:
            assert restored.getframerate() == 44100
            assert restored.readframes(4410) == b"\x01\x00" * 4410
        print("Exact metadata and matching synthetic audio restored together.")


if __name__ == "__main__":
    main()
