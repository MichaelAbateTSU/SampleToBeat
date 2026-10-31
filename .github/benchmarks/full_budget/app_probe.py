"""Test-only S3 accounting inside the otherwise installed application."""
import os
from pathlib import Path

from budget_probe import Ledger, instrument_s3

if os.environ.get("CHOPLAB_SYNTHETIC_BUDGET") != "yes":
    raise RuntimeError("Explicit synthetic budget opt-in required")
instrument_s3(Ledger(Path("/evidence/object-events.jsonl")))

import uvicorn

uvicorn.run("choplab.api:app", host="0.0.0.0", port=8765,
            proxy_headers=True, forwarded_allow_ips="*", access_log=False)
