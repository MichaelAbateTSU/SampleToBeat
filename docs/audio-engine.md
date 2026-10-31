# Audio engine

## Conventions

* Internal audio is **48 kHz stereo float32**. Analysis resamples to 22.05 kHz mono.
* Musical time is **integer ticks**, with 480 per quarter note, in 4/4. A 16th step is 120 ticks and a bar is 1920.
* Everything is non-destructive. Source files are never modified. Derived audio (stems, transformed samples,
  renders) is cached by a hash of its inputs and settings.

## Import (`audio_io.py`)

Validates extension, size and decodability. Decodes WAV/FLAC/OGG with soundfile and MP3/AAC/M4A with the bundled
ffmpeg, resamples to 48 kHz and writes atomically. Also produces waveform peaks for the UI.

## Analysis (`analysis.py`)

* **Tempo**: onset-envelope tempo estimation, with half-time, two-thirds, three-halves and double-time alternatives
  and confidences. The beat tracker is checked for locking onto off-beats.
* **Downbeats**: a per-bar accent score over the four beat phases.
* **Key**: Krumhansl–Kessler profile correlation on chroma, giving key, mode and confidence.
* **Structure**: agglomerative segmentation into sections. Also onsets and integrated loudness (pyloudnorm).
* Per-beat chroma/energy features feed sample discovery.

## Separation (`separation.py`)

Adapters declare `id`, `label`, `stems` and CPU/GPU support:

| id | Stems | Notes |
| --- | --- | --- |
| `builtin-hpss` | harmonic, percussive | librosa HPSS. Always available |
| `demucs-fast` | vocals, drums, bass, other | `htdemucs` via audio-separator (optional extra) |
| `demucs-quality` | vocals, drums, bass, other | `htdemucs_ft`. About 4× slower |

Separation jobs are serialized so two models never run at once.

## Sample discovery (`sampling.py`)

Candidates are bar-aligned loops and onset-aligned chops from the mix and from the separated stems (for
example a "melodic" set such as other+bass, or harmonic).
Each candidate gets weighted, explainable scores:

| Feature | Weight |
| --- | --- |
| harmonic content | 0.20 |
| phrase alignment | 0.20 |
| loopability (seam match) | 0.20 |
| signal quality | 0.15 |
| distinctiveness | 0.15 |
| cleanliness (low drum bleed) | 0.10 |

Maximal-marginal-relevance then picks a diverse top set.

## Transformation (`transform.py`)

Time-stretch and pitch-shift (pedalboard), reverse, gain, fades, filters, EQ, saturation, bit-crush,
reverb/delay. Results are cached by a hash of the source, the region and the settings.

## Drums (`drums.py`)

Four synthesized kits: *Boom Bap Classic*, *Modern Trap*, *Dark Drill* and *Lo-fi Warm*. They are written as WAV files under `data/kits/`, and
any sound can be replaced by uploading your own. The pattern generator is seeded per genre and supports swing,
ghost notes, hat rolls, triplets, open-hat choke and fills before section changes. The instrument → track mapping:
kick → Kick, snare/clap → Snare, rim/perc → Percussion, hats/ride → Hi-hats, crash → FX.

## 808 (`bass.py`)

A synth voice with attack/decay/release, a pitch envelope (the "punch"), glide between slide notes, harmonics and
drive. In *sample* mode it transposes an uploaded 808 from its root note instead. The bassline generator gets one
root per bar from the sample's harmony (chroma), or from the key when chroma is unreliable. It snaps notes to the
scale and places them on or near kick hits.

## Composition (`compose.py`)

For each variation:

1. Pick samples according to the strategy (`loop_flip`, `hard_chop`, `dark`, `creative`; one per variation in
   that order), favouring favourites and key-compatible candidates.
2. Choose the target tempo for the genre: half/double-time aware, or the user BPM. Stretch ratio and pitch shift
   are chosen to keep the sample in a musical key.
3. Build sample patterns (loop or chop sequences), drum patterns and an 808 line, then lay out sections (intro,
   verse, hook, bridge, outro) with drops and fills.
4. Render the variation and **evaluate** it. The checks are: not silent, not clipped, duration, drums present,
   808 present, key consistency. The evaluation also reports musical metrics (LUFS, onset density).
5. If a variation's audio fingerprint is too similar to an earlier one (similarity ≥ 0.97), it is regenerated
   with another seed, up to 2 retries.

## Rendering (`render.py`)

The arrangement becomes per-track buffers (with swing, choke and per-track FX), then a mix, then the master:

* gain to the target loudness (default -14 LUFS, clamped to -24…+12 dB)
* a look-ahead limiter and a true-peak check (4× oversampled) against a **-1 dBTP** ceiling

The renderer returns metrics (duration, LUFS in/out, peak, true peak, clipped samples). Exports write WAV
(16-bit, 24-bit or 32-bit float), MP3 320 kbps (via ffmpeg), optional per-track stems and the project JSON, then
read the files back to verify them.

## Live engine (`frontend/src/audio/engine.ts`)

Tone.js mirrors the arrangement for real-time editing. Sample chops are played from `/api/samples/{id}/buffer`,
drums from the kit WAVs, and the 808 from a MonoSynth approximation. Per-track channels provide volume, pan,
mute/solo, EQ3, filter, reverb/delay sends and meters. The live engine deliberately skips loudness mastering, tape
wobble, vinyl and the sampled-808 mode. Use **Render HQ** to hear the exact export.
