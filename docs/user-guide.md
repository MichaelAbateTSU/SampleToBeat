# User guide

Each project moves left to right through the tabs: **Source → Samples → Generate → Variations → Studio → Export**.

## 1. Create a project and import audio

On **Home**, click **New project** and give it a name. In the **Source** tab, drop an audio file or browse for one
(WAV, FLAC, AIFF, OGG, MP3, AAC/M4A, or anything else ffmpeg can decode; up to 1 GB). The file is decoded to 48 kHz stereo. The original is kept
unchanged.

No song handy? **Use demo sample** generates a copyright-free 92 BPM A-minor test song. It has an 8-bar harmonic
section (keys, pad, bass) and an 8-bar section with drums.

Only sample audio you have the rights to use.

## 2. Analysis

Analysis starts automatically after import. It shows tempo (with half/double-time alternatives), key with
confidence, tempo stability, loudness, peak and bar count. The waveform shows the beat grid.

If the analysis is wrong, use **Correct the analysis**. You can set the BPM, the key, which detected beat is "1",
or a grid offset. Corrections re-derive the grid and re-run sample discovery.

## 3. Stem separation (optional)

Choose a **Model** in the Source tab and click **Separate**:

* **Built-in HPSS**: fast, 2 stems (harmonic, percussive). Always available.
* **Demucs v4 Fast / Quality**: 4 stems (vocals, drums, bass, other). Optional install; see
  [installation.md](installation.md).

Stems can be auditioned. Sample discovery runs again so it can find cleaner candidates in the stems.

## 4. Samples

The **Samples** tab lists discovered loop and chop candidates. Each one has an overall usability score plus
reasons, e.g. "strong harmonic content", "loops cleanly", "low drum bleed". You can:

* play each sample as-is or at the target tempo
* ★ favourite a sample: favourites are kept when discovery re-runs and are preferred by the generator
* **Edit** a sample: name, pitch (semitones + fine), pitch mode, formant preservation, reverse, gain and fades
* delete it
* **Select region manually**: drag a region on the waveform, choose the audio (mix or a stem), optionally snap
  to beats, then create your own sample

## 5. Generate beats

In **Generate**, choose:

* **Style**: genre (Boom Bap, Trap, Drill, Lo-fi), tempo (blank means automatic from the sample), length in bars
  and structure
* **Feel**: energy, chop intensity, 808/bass intensity, drum complexity
* **Samples**: let ChopLab choose the best samples, or choose your own
* **Variations**: how many, and an optional seed

Variations use different strategies:

| Strategy | Idea |
| --- | --- |
| Loop flip | The strongest phrase stretched to tempo and looped, chopped up in the verses |
| Hard chops | Transient-aligned slices resequenced into a 2-bar motif |
| Dark & pitched | Pitched-down chops, a darker filter and a heavier 808 |
| Creative flip | An alternate sample with reversed chops, octave jumps and stutters |

Every variation is rendered with the authoritative renderer and checked automatically: not silent, not clipped,
drums and 808 present, key consistency.

## 6. Compare variations

**Variations** shows each result with its strategy, metrics and check results. Play it, **Regenerate** it with a
new seed, delete it, or **Open in studio** to edit it. Opening replaces the current studio arrangement after you
confirm.

## 7. Edit in the studio

* **Transport**: play/stop (Space), loop (L; shift-click the ruler to set the loop), BPM, bars, swing, key, kit,
  undo/redo (Ctrl+Z / Ctrl+Y), zoom and **Render HQ** (authoritative mastered render). It also shows a master
  meter and save status.
* **Timeline**:
  * Click the ruler to seek; Home returns to the start.
  * Drag a clip to move it, including onto another track. Drag a clip's right edge to resize it.
  * Double-click an empty lane to add a clip.
  * With a clip selected: S splits it at the playhead, Ctrl+D duplicates it, Delete removes it, and **Make
    unique** gives it its own copy of a shared pattern.
  * Tracks have mute (M) and solo buttons. You can add or remove tracks and edit sections.
* **Pattern editor** (bottom panel, follows the selected clip):
  * *Drums*: step grid per instrument. Click toggles a hit; right-click cycles velocity. Randomize from the
    generator, clear, or change the length.
  * *808*: piano roll with the key's scale highlighted. Click adds a note, clicking a note deletes it, and
    shift-click toggles slide. You can transpose, regenerate a bassline that follows the kick, and edit the 808
    sound (decay, glide, pitch envelope, harmonics, drive).
  * *Sample chops*: pads for each slice (click to audition), a step row to place chops, and a chop inspector
    (pitch, velocity, length, reverse).
* **Mixer**:
  * Per-track volume, pan, mute/solo and meters.
  * An FX panel with EQ, filters, compression, saturation, bit-crush, reverb, delay, width and hat choke.
  * Effects marked *(HQ)* (tape wobble and vinyl) are heard only in Render HQ and exports.

Edits autosave. Ctrl+S saves immediately.

## 8. Export

In **Export**, set:

* the file name
* the format: WAV 24-bit, WAV 16-bit, WAV 32-bit float, or MP3 320 kbps
* the range: the full beat, or a loop region (start bar + length)
* whether to also export stems (one file per track) and the project `.json`

Exports are mastered to about -14 LUFS with a -1 dBTP ceiling, then read back and verified (duration, peak, not
silent). Download them from the export list, or open the export folder.

## Settings

**Settings** shows:

* system information: sample rate, ffmpeg availability, free disk space, data folder
* the available separation models and genres
* the drum kits: audition every sound, replace it with your own file, or reset it

**Clear render cache** frees cached renders.
