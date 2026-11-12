"""Builds the drum library in backend/src/choplab/assets/drums from public-domain sample packs.

  cd backend && uv run python ../scripts/build_drum_library.py PACKS_DIR

PACKS_DIR holds shallow clones of the source repositories (all CC0 / public domain, see SOURCES below):
  sounds-tr808-fischer   github.com/tidalcycles/sounds-tr808-fischer
  sonic-pi               github.com/sonic-pi-net/sonic-pi  (only etc/samples is needed)
  stargate-sample-pack   github.com/stargatedaw/stargate-sample-pack
  octal-samples          github.com/octalmusic/octal-samples
  virtuosity_drums       github.com/sfzinstruments/virtuosity_drums

Every hit is cut at its attack, shortened to what a beat needs, levelled so the same fader position means the same
loudness in every kit, and written as 48 kHz mono WAV. kits.json lists the kits; SOURCES.md says where each file is from."""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from choplab import audio_io
from choplab.core import SR
from choplab.transform import effects

OUT = Path(__file__).resolve().parents[1] / "backend" / "src" / "choplab" / "assets" / "drums"
SOURCES = {
    "fischer": ("sounds-tr808-fischer", "github.com/tidalcycles/sounds-tr808-fischer", "CC0-1.0", "Roland TR-808 recordings by Michael Fischer"),
    "sonicpi": ("sonic-pi/etc/samples", "github.com/sonic-pi-net/sonic-pi (etc/samples)", "CC0-1.0", "Freesound recordings collected for Sonic Pi"),
    "stargate": ("stargate-sample-pack/stargate-sample-pack", "github.com/stargatedaw/stargate-sample-pack", "CC0-1.0", "Stargate community sample pack"),
    "octal": ("octal-samples/samples", "github.com/octalmusic/octal-samples", "CC0-1.0", "Octal house-core kit"),
    "virtuosity": ("virtuosity_drums/Samples", "github.com/sfzinstruments/virtuosity_drums", "CC0-1.0", "Virtuosity Drums by Versilian Studios and Karoryfer Samples"),
}
MAX_S = {"kick": 0.55, "snare": 0.6, "clap": 0.5, "hat": 0.16, "openhat": 0.45, "rim": 0.25, "perc": 0.3, "ride": 1.0, "crash": 2.2}
RMS_DB = {"kick": -11, "snare": -13, "clap": -14, "hat": -17, "openhat": -18, "rim": -15, "perc": -17, "ride": -20, "crash": -18}

GRIT = {"saturation": 0.25, "bitcrush_bits": 12, "lowpass_hz": 11000}   # the 12-bit sampler sound of 90s boom bap
WARM = {"saturation": 0.3, "bitcrush_bits": 12, "lowpass_hz": 6500}     # lo-fi: dull, soft, slightly crunchy
KNOCK = {"saturation": 0.35}                                             # harmonics so a sub-only kick reads on small speakers
F, P, S, O, V = "fischer", "sonicpi", "stargate", "octal", "virtuosity"

KITS = {
    # ---- boom bap: acoustic drums, as if lifted from a record and run through an old sampler
    "bb_crate": ("Crate acoustic", "boombap", GRIT, {
        "kick": (V, "kickmic/kick/kickmic_kick_snoff_vl4_rr1.flac"), "snare": (V, "snaremic/snare/snaremic_snare_center_vl34.flac"),
        "hat": (V, "oh/hh/oh_hh_closed_vl4_rr1.flac"), "openhat": (V, "oh/hh/oh_hh_open_vl3_rr1.flac"),
        "rim": (V, "snaremic/snare/snaremic_snare_crossstick_vl15.flac"), "clap": (S, "freesound/drums/clap/388042__sami-kullstrom__clap.wav"),
        "perc": (O, "percs/shaker-01.wav"), "ride": (S, "karoryfer/cymbals/ride_BRD_22.wav"), "crash": (S, "karoryfer/cymbals/crash_BRD_17.wav")}),
    "bb_break": ("Break kit", "boombap", GRIT, {
        "kick": (P, "drum_heavy_kick.flac"), "snare": (P, "drum_snare_hard.flac"), "hat": (P, "drum_cymbal_closed.flac"),
        "openhat": (P, "drum_cymbal_open.flac"), "rim": (S, "freesound/drums/rimshot/132418__sajmund__rimshot-sweet.aiff"),
        "clap": (S, "freesound/drums/clap/493707__bastianpusch__handclap01.wav"), "perc": (P, "drum_cowbell.flac"),
        "ride": (P, "ride_via.flac"), "crash": (P, "drum_splash_hard.flac")}),
    "bb_tape": ("Tape kit", "boombap", GRIT, {
        "kick": (S, "karoryfer/kicks/kick_Szpaderski_24_dampened.wav"), "snare": (S, "freesound/drums/snare/212234__alexthegr81__tapesnare-21.wav"),
        "hat": (S, "karoryfer/hihats/hihat_BRD_tight.wav"), "openhat": (S, "karoryfer/hihats/hihat_BRD_open.wav"),
        "rim": (S, "freesound/drums/rimshot/511382__pjhedman__se1-rim.wav"), "clap": (S, "fugue-state-audio/drums/claps/x0xproc1-clap.wav"),
        "perc": (O, "percs/shaker-02.wav"), "ride": (S, "karoryfer/cymbals/ride_UD_20.wav"), "crash": (S, "karoryfer/cymbals/crash_UD_16.wav")}),
    # ---- lo-fi: the same kind of drums played softly and dulled
    "lofi_soft": ("Soft and dusty", "lofi", WARM, {
        "kick": (P, "drum_bass_soft.flac"), "snare": (P, "drum_snare_soft.flac"), "hat": (P, "drum_cymbal_pedal.flac"),
        "openhat": (P, "drum_cymbal_soft.flac"), "rim": (V, "snaremic/snare/snaremic_snare_crossstick_vl12.flac"),
        "clap": (P, "perc_snap.flac"), "perc": (O, "percs/shaker-02.wav"), "ride": (P, "ride_via.flac"), "crash": (P, "drum_splash_soft.flac")}),
    "lofi_tape": ("Tape saturated", "lofi", WARM, {
        "kick": (P, "bd_jazz.flac"), "snare": (S, "freesound/drums/snare/212233__alexthegr81__tapesnare-5.wav"), "hat": (P, "hat_zan.flac"),
        "openhat": (S, "fugue-state-audio/drums/hihats/x0xproc1-hatopen.wav"), "rim": (S, "freesound/drums/rimshot/132418__sajmund__rimshot-sweet.aiff"),
        "clap": (S, "freesound/drums/clap/493707__bastianpusch__handclap01.wav"), "perc": (O, "percs/shaker-01.wav"),
        "ride": (S, "karoryfer/cymbals/ride_BRD_22.wav"), "crash": (P, "drum_splash_soft.flac")}),
    "lofi_room": ("Bedroom kit", "lofi", WARM, {
        "kick": (V, "kickmic/kick/kickmic_kick_snoff_vl3_rr1.flac"), "snare": (V, "snaremic/snare/snaremic_snare_muted_vl12.flac"),
        "hat": (V, "oh/hh/oh_hh_closed_vl3_rr1.flac"), "openhat": (V, "oh/hh/oh_hh_half_vl3_rr1.flac"),
        "rim": (V, "snaremic/snare/snaremic_snare_crossstick_vl10.flac"), "clap": (P, "perc_snap.flac"),
        "perc": (O, "percs/shaker-01.wav"), "ride": (S, "karoryfer/cymbals/ride_UD_20.wav"), "crash": (P, "drum_splash_soft.flac")}),
    # ---- trap: drum machine sounds. The kick is short because the 808 bass owns the sub
    "trap_808": ("Classic 808", "trap", KNOCK, {
        "kick": (F, "bd8/BD7525.WAV"), "snare": (F, "sd8/SD5075.WAV"), "clap": (F, "cp8/CP.WAV"), "hat": (F, "ch8/CH.WAV"),
        "openhat": (F, "oh8/OH25.WAV"), "rim": (F, "rs8/RS.WAV"), "perc": (F, "cb8/CB.WAV"), "ride": (F, "cy8/CY7525.WAV"), "crash": (F, "cy8/CY5075.WAV")}),
    "trap_clean": ("Clean and tight", "trap", KNOCK, {
        "kick": (O, "kicks/punch-03.wav"), "snare": (O, "snares/body-01.wav"), "clap": (O, "claps/classic-01.wav"), "hat": (O, "hats/closed-04.wav"),
        "openhat": (O, "hats/open-01.wav"), "rim": (S, "freesound/drums/rimshot/455640__uberproduktion__fd808a-rimshot.wav"),
        "perc": (O, "percs/tick-01.wav"), "ride": (O, "cymbals/ride-01.wav"), "crash": (O, "cymbals/crash-01.wav")}),
    "trap_hard": ("Hard hitting", "trap", KNOCK, {
        "kick": (S, "freesound/drums/kick/347166__dvbeats__kick-drum-1.wav"), "snare": (S, "fugue-state-audio/drums/snares/x0xproc2-snare.wav"),
        "clap": (S, "freesound/drums/clap/448221__toxicfilmzsrsounds__hip-hop-clap.wav"), "hat": (S, "fugue-state-audio/drums/hihats/x0xproc2-hatclsd.wav"),
        "openhat": (S, "fugue-state-audio/drums/hihats/x0xproc1-hatopen.wav"), "rim": (F, "rs8/RS.WAV"), "perc": (F, "ma8/MA.WAV"),
        "ride": (F, "cy8/CY7525.WAV"), "crash": (S, "fugue-state-audio/drums/cymbals/x0xproc1-crash.wav")}),
    # ---- drill: tight, bright, dry
    "drill_dark": ("Dark and tight", "drill", KNOCK, {
        "kick": (O, "kicks/tight-02.wav"), "snare": (F, "sd8/SD7575.WAV"), "clap": (S, "fugue-state-audio/drums/claps/distkit-clap.wav"),
        "hat": (P, "hat_star.flac"), "openhat": (F, "oh8/OH25.WAV"), "rim": (F, "rs8/RS.WAV"), "perc": (F, "hc8/HC50.WAV"),
        "ride": (F, "cy8/CY7525.WAV"), "crash": (F, "cy8/CY5075.WAV")}),
    "drill_uk": ("Cold steel", "drill", KNOCK, {
        "kick": (S, "fugue-state-audio/drums/kicks/sdbkit-kick.wav"), "snare": (P, "elec_hi_snare.flac"), "clap": (O, "claps/classic-03.wav"),
        "hat": (O, "hats/closed-06.wav"), "openhat": (O, "hats/open-04.wav"), "rim": (S, "freesound/drums/rimshot/511382__pjhedman__se1-rim.wav"),
        "perc": (P, "perc_snap.flac"), "ride": (O, "cymbals/ride-01.wav"), "crash": (O, "cymbals/crash-01.wav")}),
    "drill_ny": ("Street kit", "drill", KNOCK, {
        "kick": (S, "microlag/One-Shots/Drums/Kick2.wav"), "snare": (S, "fugue-state-audio/drums/snares/distkit-snare.wav"),
        "clap": (S, "microlag/One-Shots/Drums/Clap1.wav"), "hat": (P, "hat_bdu.flac"), "openhat": (S, "fugue-state-audio/drums/hihats/x0xproc1-hatopen.wav"),
        "rim": (S, "freesound/drums/rimshot/455640__uberproduktion__fd808a-rimshot.wav"), "perc": (F, "cl8/CL.WAV"),
        "ride": (F, "cy8/CY7525.WAV"), "crash": (S, "fugue-state-audio/drums/cymbals/x0xproc1-crash.wav")}),
}


def prepare(path: Path, inst: str, post: dict) -> np.ndarray:
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)
    m = audio_io.resample(y.mean(axis=1, keepdims=True), sr, SR)[:, 0]
    m = m - m.mean()
    peak = np.abs(m).max()
    start = max(0, int(np.argmax(np.abs(m) > 0.02 * peak)) - int(0.0005 * SR))  # cut at the attack, half a millisecond early
    m = m[start:start + int(MAX_S[inst] * SR)]
    quiet = np.where(np.abs(m) > 0.003 * peak)[0]
    m = m[: quiet[-1] + 1] if len(quiet) else m
    if post and inst in ("kick", "snare", "clap", "hat", "openhat", "rim", "perc"):
        fx = dict(post)
        if inst == "kick":
            fx.pop("lowpass_hz", None)  # a kick keeps its click
        elif post is KNOCK:
            fx = {}
        if fx:
            m = effects(np.stack([m, m], 1), fx)[:, 0]
    n = min(len(m), int(0.012 * SR))
    m[-n:] *= np.linspace(1, 0, n) ** 2
    body = m[: int(0.12 * SR)]
    gain = 10 ** (RMS_DB[inst] / 20) / (np.sqrt(np.mean(body ** 2)) + 1e-9)
    gain = min(gain, 0.89 / (np.abs(m).max() + 1e-9))  # never above -1 dBFS
    return (m * gain).astype(np.float32)


def main() -> None:
    packs = Path(sys.argv[1])
    kits, rows = {}, []
    for kid, (label, genre, post, sounds) in KITS.items():
        kits[kid] = {"label": label, "genre": genre, "sounds": {}}
        for inst, (src, pattern) in sounds.items():
            root = packs / SOURCES[src][0]
            found = sorted(root.glob(pattern))
            if not found:
                sys.exit(f"missing {src}: {pattern}")
            path = found[-1]  # for velocity-layer globs: the hardest hit
            y = prepare(path, inst, post)
            (OUT / kid).mkdir(parents=True, exist_ok=True)
            sf.write(str(OUT / kid / f"{inst}.wav"), y, SR, subtype="PCM_16")
            kits[kid]["sounds"][inst] = f"{kid}/{inst}.wav"
            rows.append((f"{kid}/{inst}.wav", SOURCES[src][1], str(path.relative_to(root)), SOURCES[src][2]))
            print(f"{kid:12s}{inst:8s} {len(y) / SR:5.2f}s  peak {20 * np.log10(np.abs(y).max()):6.1f} dB  <- {path.name}")
    (OUT / "kits.json").write_text(json.dumps(kits, indent=1) + "\n")
    lines = ["# Drum library sources", "",
             "Every file here is derived from a public-domain (CC0 1.0) recording: cut at the attack, shortened, levelled and,",
             "for some kits, coloured (saturation, 12-bit reduction, low-pass). Rebuild with `scripts/build_drum_library.py`.", "",
             "| Pack | Repository | Licence |", "| --- | --- | --- |"]
    lines += [f"| {v[3]} | {v[1]} | {v[2]} |" for v in SOURCES.values()]
    lines += ["", "| File | Repository | Original file | Licence |", "| --- | --- | --- | --- |"]
    lines += [f"| `{a}` | {b} | `{c}` | {d} |" for a, b, c, d in rows]
    (OUT / "SOURCES.md").write_text("\n".join(lines) + "\n")
    print(f"{len(rows)} sounds in {len(kits)} kits -> {OUT}")


if __name__ == "__main__":
    main()
