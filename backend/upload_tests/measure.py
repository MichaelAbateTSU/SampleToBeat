"""Host-side capped generator and concurrent five-second health measurements."""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
parser.add_argument("--size", type=int, default=1073741824)
parser.add_argument("--expect", type=int, default=200)
parser.add_argument("--no-auth", action="store_true")
args = parser.parse_args()
if not 0 < args.size <= 1073741825:
    parser.error("bounded at 1024 MiB + 1 byte")
token = os.environ["TEST_TOKEN"]
auth = base64.b64encode(("owner:" + token).encode()).decode()
boundary = "----chopLabUploadProbeBoundary7d3f1c"
prefix = (f"--{boundary}\r\n"
          'Content-Disposition: form-data; name="file"; filename="probe.wav"\r\n'
          "Content-Type: audio/wav\r\n\r\n").encode()
suffix = f"\r\n--{boundary}--\r\n".encode()
body_size = len(prefix) + args.size + len(suffix)
headers = {"Content-Length": str(body_size),
           "Content-Type": f"multipart/form-data; boundary={boundary}",
           "X-Forwarded-Proto": "https"}
if not args.no_auth:
    headers["Authorization"] = "Basic " + auth

observations = []
stop = threading.Event()

def health():
    while not stop.is_set():
        start = time.monotonic()
        c = http.client.HTTPConnection("127.0.0.1", 10000, timeout=5)
        try:
            c.request("GET", "/api/health")
            r = c.getresponse()
            r.read()
            observations.append({"latency_s": time.monotonic()-start, "status": r.status})
        except (OSError, http.client.HTTPException) as e:
            observations.append({"latency_s": time.monotonic()-start, "error": type(e).__name__})
        finally:
            c.close()
        stop.wait(0.5)

t = threading.Thread(target=health, daemon=True)
t.start()
c = http.client.HTTPConnection("127.0.0.1",10000,timeout=600)
c.putrequest("POST", "/api/test/upload")
for k,v in headers.items():
    c.putheader(k,v)
c.endheaders()
body_hash,file_hash = hashlib.sha256(),hashlib.sha256()
block = bytearray(hashlib.shake_256(b"choplab-upload-probe-v1").digest(1<<20))
start = time.monotonic()
send_error = None
try:
    if args.no_auth:
        raise StopIteration
    c.send(prefix); body_hash.update(prefix)
    remaining,index = args.size,0
    while remaining:
        block[:8] = index.to_bytes(8,"big")
        piece = bytes(block[:min(len(block),remaining)])
        c.send(piece)
        body_hash.update(piece);file_hash.update(piece)
        remaining -= len(piece);index += 1
    c.send(suffix);body_hash.update(suffix)
except StopIteration:
    pass
except (OSError,http.client.HTTPException) as e:
    send_error=type(e).__name__
send_s=time.monotonic()-start
try:
    response=c.getresponse()
    result=json.loads(response.read())
    status=response.status
finally:
    c.close(); stop.set();t.join(timeout=6)
result.update(client_elapsed_s=time.monotonic()-start,send_s=send_s,
              expected_body_bytes=body_size,expected_file_bytes=args.size,
              expected_file_sha256=file_hash.hexdigest(), expected_body_sha256=body_hash.hexdigest(),
              health=observations,send_error=send_error)
if args.expect==200:
    assert result["saved_bytes"]==args.size and result["received_bytes"]==body_size
    assert result["file_sha256"]==file_hash.hexdigest()
    assert result["body_sha256"]==body_hash.hexdigest()
assert status==args.expect,(status,result)
text=json.dumps(result,indent=2)
assert token not in text
Path(args.out).write_text(text)
print(json.dumps({"status":status,"elapsed_s":result["client_elapsed_s"],
                  "health_count":len(observations),
                  "health_errors":[o for o in observations if o.get("error")],
                  "max_health_latency_s":max(o["latency_s"] for o in observations),
                  "metrics":result.get("after_request")}))
