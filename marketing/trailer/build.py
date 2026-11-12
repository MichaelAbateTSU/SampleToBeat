"""Cuts the ChopLab launch trailer (1920x1080, 24 fps, ~44 s) with ffmpeg.

Inputs (see marketing/README.md for how each is produced):
  --hf       Higgsfield clips: crate.mp4, stylus.mp4, producer.mp4
  --cap      UI recordings from capture.mjs: create/, flips/, mix/, export/, export_done/ (frames + list.txt)
  --frames   Blender frames from blender/scenes.py: chop_####.png, logo_####.png
  --captions PNGs from captions.mjs
  --music    music.wav + music.json from make_music.py
  --out      output folder (trailer.mp4, trailer_poster.jpg)

Every cut lands on a bar line of the soundtrack, which the ChopLab engine made from the sample shown on screen."""
import argparse
import json
import subprocess
from pathlib import Path

FPS = 24
# blend in RGB: screen-blending YUV chroma planes tints everything magenta
GLOW = "format=gbrp,split[a][b];[b]gblur=sigma=26[g];[a][g]blend=all_mode=screen:all_opacity=0.5,format=yuv420p"


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    for name in ("hf", "cap", "frames", "captions", "music", "out"):
        ap.add_argument(f"--{name}", type=Path, required=True)
    a = ap.parse_args()
    work = a.out / "work"
    work.mkdir(parents=True, exist_ok=True)
    music = json.loads((a.music / "music.json").read_text())
    bar = music["bar_seconds"]
    total = music["duration"]

    # --- sources that need assembling first
    for name in ("create", "flips", "mix", "export", "export_done"):
        run("-f", "concat", "-safe", "0", "-i", str(a.cap / name / "list.txt"), "-vf", f"fps={FPS},scale=1920:1080:flags=lanczos",
            "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(work / f"ui_{name}.mp4"))
    run("-i", str(work / "ui_export.mp4"), "-i", str(work / "ui_export_done.mp4"), "-filter_complex", "[0][1]concat=n=2:v=1[v]",
        "-map", "[v]", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(work / "ui_export_all.mp4"))

    def frames_clip(shot: str, out: Path) -> None:
        """Blender frames -> clip. Missing frames (slow passages rendered on twos) are filled by blending."""
        files = sorted(a.frames.glob(f"{shot}_*.png"))
        nums = [int(f.stem.split("_")[1]) for f in files]
        lst = work / f"{shot}.txt"
        lines = ["ffconcat version 1.0"]
        for f, n, nxt in zip(files, nums, nums[1:] + [nums[-1] + 1]):
            lines += [f"file {f}", f"duration {(nxt - n) / FPS:.5f}"]
        lines.append(f"file {files[-1]}")
        lst.write_text("\n".join(lines) + "\n")
        run("-f", "concat", "-safe", "0", "-i", str(lst), "-vf", f"scale=1920:1080:flags=lanczos,framerate=fps={FPS},{GLOW}",
            "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(out))

    frames_clip("chop", work / "chop.mp4")
    frames_clip("logo", work / "logo.mp4")

    # --- the edit: (name, start bar, source, seconds into the source, caption, fit)
    #     fit "speed" plays the whole source in the slot; "cut" plays it at normal speed from the offset
    edit = [
        ("crate", 0.0, a.hf / "crate.mp4", 1.3, "c1", "cut"),
        ("stylus", 1.0, a.hf / "stylus.mp4", 1.2, "c2", "cut"),
        ("chop", 2.0, work / "chop.mp4", 0.0, "c3", "speed"),
        ("create", 3.5, work / "ui_create.mp4", 0.0, "c4", "speed"),
        ("flips", 5.0, work / "ui_flips.mp4", 0.0, "c5", "speed"),
        ("mix", 8.0, work / "ui_mix.mp4", 0.0, "c6", "speed"),
        ("export", 10.0, work / "ui_export_all.mp4", 0.0, "c7", "speed"),
        ("producer", 11.5, a.hf / "producer.mp4", 0.6, "c8", "cut"),
        ("logo", 13.0, work / "logo.mp4", 0.0, "end", "hold"),
    ]
    starts = [round(e[1] * bar * FPS) for e in edit] + [round(total * FPS)]
    parts = []
    for i, (name, _, src, offset, cap, fit) in enumerate(edit):
        n = starts[i + 1] - starts[i]
        secs = n / FPS
        if fit == "speed":
            v = f"setpts=PTS*{secs / duration(src):.6f},fps={FPS}"
        elif fit == "hold":  # play once, then freeze on the last frame while the end card comes up
            # the stage is black, so the mark can slide left to make room for the wordmark beside it
            v = f"fps={FPS},tpad=stop_mode=clone:stop_duration={secs:.3f},crop=1450:1080:470:0,pad=1920:1080:0:0:black"
        else:
            v = f"fps={FPS}"
        v += ",scale=1920:1080:flags=lanczos,setsar=1"
        cap_in = 3.4 if fit == "hold" else 0.12
        fc = (f"[0:v]{v}[bg];[1:v]format=rgba,fade=t=in:st={cap_in}:d={0.6 if fit == 'hold' else 0.2}:alpha=1"
              + ("" if fit == "hold" else f",fade=t=out:st={secs - 0.22:.3f}:d=0.2:alpha=1") + "[cap];[bg][cap]overlay=0:0,format=yuv420p[v]")
        out = work / f"part{i:02d}_{name}.mp4"
        run(*(["-ss", str(offset)] if fit == "cut" else []), "-i", str(src), "-loop", "1", "-t", f"{secs + 1:.3f}", "-i", str(a.captions / f"{cap}.png"),
            "-filter_complex", fc, "-map", "[v]", "-frames:v", str(n), "-r", str(FPS), "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(out))
        parts.append(out)

    lst = work / "parts.txt"
    lst.write_text("".join(f"file {p}\n" for p in parts))
    final = a.out / "trailer.mp4"
    run("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(a.music / "music.wav"),
        "-filter_complex", f"[0:v]fade=t=in:st=0:d=0.5,fade=t=out:st={total - 0.7:.3f}:d=0.7[v];[1:a]afade=t=out:st={total - 1.5:.3f}:d=1.5[au]",
        "-map", "[v]", "-map", "[au]", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k", "-shortest", str(final))
    run("-ss", f"{total - 2:.2f}", "-i", str(final), "-frames:v", "1", "-q:v", "2", str(a.out / "trailer_poster.jpg"))
    print(f"{final}  {duration(final):.2f}s  {final.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
