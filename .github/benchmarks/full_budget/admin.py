"""Separate, uncapped synthetic setup/inventory; never owns the app lease."""
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
import sys
from urllib.parse import urlsplit

from budget_probe import verify_original


def guard():
    target = urlsplit(os.environ["CHOPLAB_DATABASE_URL"])
    if (os.environ.get("CHOPLAB_SYNTHETIC_BUDGET") != "yes"
            or target.hostname != "postgres" or target.path != "/choplab_recovery_test"
            or target.username != "choplab_test" or target.password != "synthetic-ci-password"):
        raise RuntimeError("Only known isolated synthetic services are allowed")


def initialize():
    from choplab.object_storage import S3ByteStorage, S3Settings
    settings = S3Settings("https://s3:9000", "synthetic-durable", "us-east-1",
                          "synthetic-access", "synthetic-secret",
                          prefix="budget-" + os.urandom(8).hex(), max_bytes=1 << 30,
                          ca_bundle="/certs/s3.crt")
    config = Path("/config")
    config.mkdir(exist_ok=True)
    for name, content in (
            ("storage.json", json.dumps(asdict(settings))),
            ("auth.json", json.dumps({"username": "owner", "password": "synthetic-http-only"}))):
        path = config / name
        with path.open("x") as out:
            out.write(content)
        path.chmod(0o600)
    salt = b"synthetic-budget-only-salt"
    digest = hashlib.pbkdf2_hmac("sha256", b"synthetic-http-only", salt, 600000)
    secret = config / "hash"
    with secret.open("x") as out:
        out.write(f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}")
    secret.chmod(0o600)
    S3ByteStorage(settings, Path("/data")).client.create_bucket(Bucket=settings.bucket)


def inventory():
    from choplab import durable, store
    from choplab.object_storage import S3ByteStorage
    durable.configure()
    settings = durable._settings
    adapter = S3ByteStorage(settings, Path("/data"))
    physical = []
    for page in adapter.client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.bucket, Prefix=settings.prefix + "/publications/"):
        physical.extend(page.get("Contents", []))
    with store._conn() as con:
        rows = con.execute("SELECT state,ref FROM choplab_assets").fetchall()
        documents = con.execute("SELECT kind,doc FROM docs").fetchall()
        live = con.execute("SELECT id,relative,ref FROM choplab_assets WHERE state='live'").fetchall()
    states, classes, seen = {}, {}, set()
    for state, raw in rows:
        counts = states.setdefault(state, {"objects": 0, "declared_object_bytes": 0})
        counts["objects"] += 1
        counts["declared_object_bytes"] += json.loads(raw)["size"] if raw else 0
    assets = {id: (relative, json.loads(raw)["size"]) for id, relative, raw in live}
    for kind, raw in documents:
        doc = json.loads(raw)
        for id in doc.get("_assets", {}).values():
            if id in seen:
                continue
            seen.add(id)
            relative, size = assets[id]
            category = kind
            if kind == "source":
                category = "other_source"
                for field, label in (("original_file_path", "original"),
                                     ("internal_audio_path", "decoded"), ("features_path", "features")):
                    if doc.get(field) == "asset-path:" + relative:
                        category = label
                if "asset-path:" + relative in (doc.get("stems") or {}).values():
                    category = "stems"
            counts = classes.setdefault(category, {"objects": 0, "bytes": 0})
            counts["objects"] += 1
            counts["bytes"] += size
    return {"catalog_states": states, "artifact_classes": classes,
            "provider_conservative_recorded_bytes_without_reclamation":
                sum(item["declared_object_bytes"] for item in states.values()),
            "catalog_objects_without_recorded_size": sum(not raw for _, raw in rows),
            "physical_listed_objects": len(physical),
            "physical_listed_object_bytes": sum(item["Size"] for item in physical)}


def main():
    guard()
    action = sys.argv[1]
    if action == "initialize":
        initialize()
    elif action == "inventory":
        print(json.dumps(inventory()))
    elif action == "expire":
        from choplab import store
        with store._conn() as con:
            con.execute("UPDATE choplab_runtime SET expires=0 WHERE id=1")
    elif action == "original":
        manifest = json.loads(Path("/acceptance/manifest.json").read_text())
        original = json.loads(Path("/fixture/fixture-manifest.json").read_text())
        verify_original("/evidence/admin-object-events.jsonl", manifest["ids"]["source"],
                        manifest["sha256"]["original_upload"], original["size"])
    else:
        raise ValueError("Unknown synthetic admin command")


if __name__ == "__main__":
    main()
