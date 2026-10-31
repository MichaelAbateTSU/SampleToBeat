"""Capped synthetic Content-Length multipart client. Token comes from env/private file."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "render-upload-probe"))
from client import Payload, get_json, load_token, parse_size, run_probe  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--size", type=parse_size, default=1024 * (1 << 20))
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--token-file")
    parser.add_argument("--token-dpapi-file")
    parser.add_argument("--expect-status", type=int, default=200)
    parser.add_argument("--status", action="store_true", help="authenticated disk/source inspection only")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != "https" and not (args.allow_http and url.hostname in ("127.0.0.1", "localhost")):
        parser.error("HTTPS required except explicitly allowed loopback HTTP")
    if not 0 < args.size <= 1024 * (1 << 20) + 1:
        parser.error("file size must be positive and at most 1024 MiB + 1 byte")
    token = load_token(args.token_file, args.token_dpapi_file)
    if not token:
        parser.error("no private token source")
    if args.status:
        result = get_json(url, "/status", token, 120)
        text = json.dumps(result, indent=2)
        if token in text:
            raise RuntimeError("refusing to persist token-bearing output")
        Path(args.out).write_text(text, encoding="utf-8")
        print(json.dumps({"status": result["status"], "from_app": result["from_app"]}))
        return 0 if result["status"] == args.expect_status and result["from_app"] else 1
    payload = Payload(args.size, True)
    file_hash = hashlib.sha256()
    for block in payload.chunks(len(payload.prefix), len(payload.prefix) + args.size):
        file_hash.update(block)
    options = SimpleNamespace(url=args.url, size=args.size, multipart=True,
                              two_phase=False, timeout=180, label="actual multipart spool/save",
                              framing="content-length", expect_status=args.expect_status,
                              expect_from="app")
    result = run_probe(options, token)
    result["expected_file_sha256"] = file_hash.hexdigest()
    body = result.get("response", {}).get("body", {})
    result["file_digest_matches"] = body.get("file_sha256") == file_hash.hexdigest()
    result["saved_length_matches"] = body.get("saved_bytes") == args.size
    result["passed"] = bool(result.get("matched_expectation")) and (
        args.expect_status != 200 or (
            result["file_digest_matches"] and result["saved_length_matches"]
            and result["outcome"] == "ok"
        )
    )
    text = json.dumps(result, indent=2)
    if token in text:
        raise RuntimeError("refusing to persist token-bearing output")
    Path(args.out).write_text(text, encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "outcome": result["outcome"],
                      "status": result.get("response", {}).get("status"),
                      "saved_bytes": body.get("saved_bytes"),
                      "file_digest_matches": result["file_digest_matches"],
                      "errors": body.get("errors"), "phase": body.get("phase")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
