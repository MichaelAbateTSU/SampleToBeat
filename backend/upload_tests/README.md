# Hosted upload cache regression and measurements

The hosted Linux API uses a route-scoped Starlette multipart parser and spool
subclass in `choplab.uploads`. Authentication middleware runs before the route.
The three existing `file: UploadFile = File(...)` endpoints are unchanged;
`_save_upload` still checks size/free-space reserve and cleans partial output.
Local mode/Windows retain the standard parser.

This mechanism keeps Starlette's 1 MiB memory spool threshold and threadpool
I/O. After rollover, dirty spool cache is flushed, fsynced and advised away at
8 MiB windows using the existing `_release_file_cache` helper. Reading the spool
also advises cache away in bounded windows. This trades more flush/syscall I/O
for predictable page-cache charges; it does not change samples or encoding.

The cache parser uses subclass callbacks and a custom Request/APIRoute, not
global monkeypatches. Starlette has no public parser/file-factory registration
API: its `_current_part`, `_files_to_close_on_error` and Request `_get_form`
hooks are necessarily encapsulated here. Regression-test these hooks against
every dependency upgrade (current validation: Starlette 1.9.0).

The original per-file cap is additionally enforced while parsing so authenticated
oversize parts cannot fill `/tmp` before `_save_upload`. No guard is removed.
Spool ENOSPC now produces a logged 507 (`Temporary upload storage is full.`),
instead of FastAPI's generic parser 400; other spool OSErrors produce a logged
500. Malformed multipart remains 400, missing/truncated required file remains
422, existing reserve/size guards remain 507/413. Parser failure, disconnect and
cancellation close its files; unfinished orphan parts are explicitly closed.
No backing file is persisted by the cache wrapper.

Orphan cleanup uses Starlette's synchronous close convention with no additional
await/cancellation point after successful parsing, so a retained FormData cannot
be stranded by cancellation while closing a separate unfinished part.

Run on Linux Docker:

```
bash backend/upload_tests/ci.sh
```

The Docker harness imports the real API/save function and uses the actual
OwnerAccess authentication, but skips product lifespan/DB/jobs and sets only
the durable-enabled predicate to a test sentinel. It first runs failure/cleanup
unit tests, then measures baseline versus cache-managed parsing and saving of
an exact 1,073,741,824-byte deterministic synthetic file within a
1,073,742,002-byte Content-Length multipart envelope. Host-side generation is
bounded; full body and saved-file SHA-256 are independently verified.

Both measurement containers have **512 MiB memory, 0.1 CPU, no swap**. Results
retain memory.stat anonymous/file/dirty/writeback charges, sampled current
memory, kernel lifetime peak and memory.events. Independent host-side HTTP
requests measure `/api/health` throughout the full upload with a **five-second
timeout**; report timeouts even if uploading succeeded.

Controlled tmpfs cases test reserve 507 and actual spool ENOSPC 507 without
filling the CI host. Authentication is checked with a declared 1 GiB body and
no bytes sent: response 401 must occur before the receive stream is called.

This is not product readiness: warm jobs/DSP/durable publication, existing
library and real-provider filesystem occupancy are absent. Do not infer those
properties from synthetic parser/save success. Retain exact measured memory
headroom and health latencies, not a blanket low-memory claim.
