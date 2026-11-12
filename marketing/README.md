# Launch trailer and brand assets

`trailer/trailer.mp4` is a 44-second launch trailer (1920x1080, 24 fps). Its soundtrack was made by ChopLab
from the same sample you see uploaded on screen, and every cut lands on a bar line of that beat.

| Part of the trailer | Made with |
| --- | --- |
| Record crate, stylus and producer shots | Higgsfield (Seedance 2.5, 1080p, 5 s each, 80 credits each) |
| Waveform being chopped; logo reveal | Blender 5.0 (Cycles), `blender/scenes.py` |
| The app in use | The real app, recorded by `trailer/capture.mjs` |
| Captions and end card | `trailer/captions.mjs`, in the app's own typefaces |
| Music | The ChopLab engine, `trailer/make_music.py` |
| Edit | ffmpeg, `trailer/build.py` |

Unreal Engine was not used: it was not available in the environment this was built in, and Blender covers the
3D shots.

## Rebuild

```bash
W=/tmp/trailer                                        # any scratch folder
python3.11 -m venv $W/b && $W/b/bin/pip install bpy   # Blender as a Python module
$W/b/bin/python blender/scenes.py chop $W/frames --scale 67
$W/b/bin/python blender/scenes.py logo $W/frames
(cd ../backend && uv run python ../marketing/trailer/make_music.py $W/music)
(cd ../backend && CHOPLAB_BILLING=mock uv run choplab &)   # app on :8765, with frontend built
node trailer/capture.mjs $W/cap $W/music/dusty_keys.wav
node trailer/captions.mjs $W/captions
python3 trailer/build.py --hf trailer/footage --cap $W/cap --frames $W/frames --captions $W/captions --music $W/music --out trailer
```

The three Higgsfield clips are kept in `trailer/footage/` so the trailer can be re-cut without spending
credits again. Their prompts are in `trailer/footage/PROMPTS.md`.

## Brand assets

`brand/` has the logo mark, press screenshots of the app and the script that regenerates them along with the
app icons and social card in `frontend/public/`.
