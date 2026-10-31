"""Explicit offline full-size decoder verification, no provider access."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import soundfile as sf

import client
import fixture


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-gib-fixture", action="store_true", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if shutil.disk_usage(tempfile.gettempdir()).free < (3 << 30):
        raise RuntimeError("need at least 3 GiB free for disposable offline fixture")
    with tempfile.TemporaryDirectory(prefix="valid-audio-offline-") as work:
        path = Path(work) / "full.wav"
        written = fixture.write(path, fixture.FRAMES)
        assert path.stat().st_size == fixture.FILE_BYTES
        assert written["actual_bytes"] == fixture.FILE_BYTES
        assert written["sha256"] == "2f16011c15bf4c0ff6313c154d119030741cf9a6efbd88695fa20da1c7044b46"
        with path.open("rb") as f:
            os.fsync(f.fileno())
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        read_hash = hashlib.sha256()
        expected_body = hashlib.sha256(client.PREFIX)
        with path.open("rb") as f:
            while piece := f.read(737280):
                read_hash.update(piece)
                expected_body.update(piece)
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        assert read_hash.hexdigest() == written["sha256"]
        expected_body.update(client.SUFFIX)
        count, maximum, sumsquares, values = 0, 0.0, 0.0, 0
        with sf.SoundFile(path) as f:
            info = {"frames":len(f),"samplerate":f.samplerate,"channels":f.channels,
                    "subtype":f.subtype,"format":f.format}
            assert info == {"frames":89478480,"samplerate":96000,"channels":4,
                            "subtype":"PCM_24","format":"WAV"}
            for block in f.blocks(blocksize=61440, dtype="float32", always_2d=True):
                assert np.isfinite(block).all()
                count += len(block)
                maximum = max(maximum, float(np.abs(block).max()))
                sumsquares += float(np.square(block, dtype=np.float64).sum())
                values += block.size
                with path.open("rb") as discard:
                    os.posix_fadvise(discard.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        assert count == fixture.FRAMES and maximum > 0.01 and sumsquares > 0
        ffprobe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,sample_rate,channels,bits_per_raw_sample,duration_ts,time_base",
             "-of", "json", str(path)], check=True, capture_output=True, text=True)
        stream = json.loads(ffprobe.stdout)["streams"][0]
        assert stream["codec_name"] == "pcm_s24le"
        assert int(stream["sample_rate"]) == fixture.RATE
        assert stream["channels"] == 4 and int(stream["bits_per_raw_sample"]) == 24
        assert int(stream["duration_ts"]) == fixture.FRAMES
        assert stream["time_base"] == "1/96000"
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
                        "-f", "null", "-"], check=True, stdout=subprocess.DEVNULL)
        # Exact production metadata function, not a rewritten WAV approximation.
        from choplab import audio_io
        meta = audio_io.probe(path)
        assert meta["decoder"] == "libsndfile"
        assert meta["sample_rate"] == fixture.RATE and meta["channels"] == 4
        assert meta["duration"] == fixture.FRAMES / fixture.RATE
        small_internal = Path(work) / "internal-small.wav"
        audio_io.write_wav(small_internal, np.zeros((1001, 2), dtype=np.float32))
        internal_header_bytes = small_internal.stat().st_size - (1001 * 2 * 4)
        raw_internal = Path(work) / "internal.raw"
        error_file = Path(work) / "ffmpeg-error.txt"
        with error_file.open("wb") as errors, raw_internal.open("wb") as raw:
            # Decode/downsample independently with FFmpeg; not the app's SciPy decode.
            proc = subprocess.Popen(
                ["ffmpeg","-v","error","-nostdin","-i",str(path),
                 "-af","pan=stereo|c0=c0|c1=c1","-ar","48000","-c:a","pcm_f32le",
                 "-f","f32le","pipe:1"], stdout=subprocess.PIPE, stderr=errors)
            count_raw = since_flush = 0
            try:
                while piece := proc.stdout.read(737280):
                    raw.write(piece); count_raw += len(piece); since_flush += len(piece)
                    if since_flush >= (8 << 20):
                        raw.flush(); os.fsync(raw.fileno())
                        os.posix_fadvise(raw.fileno(),0,count_raw,os.POSIX_FADV_DONTNEED)
                        since_flush = 0
                assert proc.wait(timeout=30) == 0
                raw.flush(); os.fsync(raw.fileno())
                os.posix_fadvise(raw.fileno(),0,count_raw,os.POSIX_FADV_DONTNEED)
            finally:
                proc.stdout.close()
                if proc.poll() is None:
                    proc.kill(); proc.wait(timeout=30)
        assert count_raw == client.INTERNAL_PCM_BYTES
        before_internal_writer_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mapped = np.memmap(raw_internal, dtype=np.float32, mode="r",
                           shape=(client.INTERNAL_FRAMES,2))
        full_internal = Path(work) / "internal-full.wav"
        audio_io.write_wav(full_internal, mapped)
        del mapped
        full_internal_info = sf.info(full_internal)
        assert full_internal_info.frames == client.INTERNAL_FRAMES
        assert full_internal_info.samplerate == 48000 and full_internal_info.channels == 2
        assert full_internal_info.subtype == "FLOAT"
        actual_internal_bytes = full_internal.stat().st_size
        assert actual_internal_bytes == client.INTERNAL_PCM_BYTES + internal_header_bytes

        observed = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers["Content-Length"])
                h = hashlib.sha256()
                fh = hashlib.sha256()
                received = 0
                while n:
                    piece = self.rfile.read(min(n, 737280))
                    if not piece:
                        raise RuntimeError("early EOF")
                    start = max(0, len(client.PREFIX) - received)
                    end = min(len(piece), len(client.PREFIX) + fixture.FILE_BYTES - received)
                    if end > start:
                        fh.update(piece[start:end])
                    h.update(piece); received += len(piece); n -= len(piece)
                observed.update(body_bytes=received, body_sha256=h.hexdigest(), file_sha256=fh.hexdigest())
                data = b'{"source":{"id":"offline"},"job":{"id":"offline"}}'
                self.send_response(202)
                self.send_header("Content-Length",str(len(data)))
                self.end_headers(); self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            transport = client.Transport(
                f"http://127.0.0.1:{server.server_port}", "owner", "offline-synthetic",
                {"max_http_requests":2,"expires_at":(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()},
                allow_loopback=True)
            result = transport.upload("offline", written["sha256"])
            assert observed["body_bytes"] == 1073742002
            assert result["sent_file_sha256"] == written["sha256"]
            assert result["sent_multipart_sha256"] == observed["body_sha256"]
            assert observed["body_sha256"] == expected_body.hexdigest()
            assert observed["file_sha256"] == written["sha256"]
        finally:
            server.shutdown();server.server_close();thread.join()
        report = {"offline_only":True,"provider_requests":0,"fixture":written,
                  "decoded_frame_count":count,"libsndfile":info,"production_probe":meta,
                  "ffprobe":stream,"ffmpeg_full_decode_exit":0,
                  "soundfile_version":sf.__version__,"libsndfile_version":sf.__libsndfile_version__,
                  "production_audio_io_sha256":hashlib.sha256(Path(audio_io.__file__).read_bytes()).hexdigest(),
                  "actual_small_production_internal_wav_header_bytes":internal_header_bytes,
                  "actual_full_production_written_internal_wav_bytes":actual_internal_bytes,
                  "actual_internal_frame_count":full_internal_info.frames,
                  "internal_decode":"independent FFmpeg pan first two channels/resample; production write_wav, NOT production decode/analysis",
                  "rss_before_full_internal_memmap_writer_kib":before_internal_writer_rss,
                  "non_silent_peak":maximum,"rms":(sumsquares/values)**0.5,
                  "full_loopback_stream":observed,"rss_max_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    report["temporary_fixture_removed"] = not path.exists()
    report["cgroup"] = {name:Path("/sys/fs/cgroup", name).read_text().strip()
                        for name in ("memory.max","memory.swap.max","memory.peak","memory.events")}
    args.out.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
