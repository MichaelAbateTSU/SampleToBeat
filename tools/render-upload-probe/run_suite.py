"""Run the bounded upload-boundary matrix against a probe URL.

The token comes from PROBE_TOKEN, --token-file or --token-dpapi-file and is
passed to client.py only through the child environment; it is never printed.
Results are written as JSON lines to --out and summarised on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from client import load_token  # noqa: E402
MIB = 1024 * 1024


def cases(max_bytes: int, include_stream_over_max: bool):
    yield "auth: missing token rejected by app", ["--size", "1MiB", "--no-auth",
                                                  "--expect-status", "401"]
    yield "limit: declared Content-Length > probe max rejected pre-body", [
        "--size", str(max_bytes + 1), "--expect-status", "413"]
    yield "100MiB raw content-length two-phase", ["--size", "100mib", "--two-phase"]
    yield "100MiB raw chunked two-phase", ["--size", "100mib", "--framing", "chunked", "--two-phase"]
    yield "fixture raw content-length", ["--size", "fixture"]
    yield "fixture multipart content-length", ["--size", "fixture", "--multipart"]
    yield "cap(1024MiB) multipart content-length two-phase", ["--size", "cap", "--multipart",
                                                             "--two-phase"]
    yield "cap(1024MiB) multipart chunked", ["--size", "cap", "--multipart", "--framing", "chunked"]
    if include_stream_over_max:
        yield "limit: chunked body > probe max aborted mid-stream", [
            "--size", str(max_bytes + MIB), "--framing", "chunked", "--expect-status", "413"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--allow-http", action="store_true")
    ap.add_argument("--include-stream-over-max", action="store_true")
    ap.add_argument("--only", help="substring filter on case names")
    ap.add_argument("--out", default="probe-results.jsonl")
    ap.add_argument("--cafile")
    ap.add_argument("--token-file")
    ap.add_argument("--token-dpapi-file")
    args = ap.parse_args()
    token = load_token(args.token_file, args.token_dpapi_file)
    if not token:
        sys.exit("no token (PROBE_TOKEN, --token-file or --token-dpapi-file)")
    env = dict(os.environ, PROBE_TOKEN=token)

    base = [sys.executable, os.path.join(HERE, "client.py"), "--url", args.url]
    if args.allow_http:
        base.append("--allow-http")
    if args.cafile:
        base += ["--cafile", args.cafile]

    health = subprocess.run(base + ["--health"], capture_output=True, text=True, env=env)
    print(health.stdout.strip())
    if health.returncode:
        print("health check failed", file=sys.stderr)
        return 1
    max_bytes = json.loads(health.stdout)["body"]["max_bytes"]

    failures = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for name, extra in cases(max_bytes, args.include_stream_over_max):
            if args.only and args.only not in name:
                continue
            proc = subprocess.run(base + extra + ["--label", name], capture_output=True, text=True, env=env)
            try:
                res = json.loads(proc.stdout)
            except ValueError:
                res = {"label": name, "outcome": "client_crash", "stderr": proc.stderr[-2000:]}
            res["passed"] = proc.returncode == 0
            out.write(json.dumps(res) + "\n")
            out.flush()
            failures += not res["passed"]
            body = (res.get("response") or {}).get("body") or {}
            print(f"[{'PASS' if res['passed'] else 'FAIL'}] {name}: outcome={res.get('outcome')} "
                  f"status={(res.get('response') or {}).get('status')} "
                  f"sent={res.get('bytes_sent')} body={res.get('body_bytes')} "
                  f"server_rx={body.get('received_bytes', body.get('received_at_abort'))} "
                  f"framing_seen={body.get('framing_seen')} rss_kib={body.get('server_max_rss_kib')} "
                  f"streaming={(res.get('two_phase') or {}).get('streaming', '-')} "
                  f"elapsed={res.get('elapsed_s')}s", flush=True)
    print(f"{failures} failure(s); details in {args.out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
