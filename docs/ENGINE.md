# How the beat engine decides things

The engine is rules, not a model. Every rule below is something producers do or something that was measured, with
the source beside it. Numbers from tutorials are defaults that disagree between sources; the academic ones are firmer.
None of this replaces listening: `scripts/diagnose.py` measures what can be measured, and people have to judge the rest.

## Drums

Sounds are recorded one-shots from public-domain packs, three kits per style (`backend/src/choplab/assets/drums`,
sources in `SOURCES.md` there, rebuilt by `scripts/build_drum_library.py`). A synthesized kit per style remains as
a fallback and for older saved beats.

| Rule | Where | Source |
| --- | --- | --- |
| Kick patterns are fixed two-bar figures chosen by seed, not probabilities per step | `drums.KICKS` | Native Instruments trap and drill walkthroughs; boom bap pattern guides |
| Trap hats are steady eighth notes. Rolls only close a four-bar phrase (one more allowed at high complexity) | `drums._bar` | NI "How to make a trap beat"; songen.app on hat rolls ("not a wall of stuttering 16ths") |
| Rolls fall away or dip in the middle. They never get louder | `drums._roll` | padwolf.app ("for most trap rolls, use Down"); NI (middle note turned down) |
| Drill hats are 3+3+2, snare on beat 3 then beat 4, no swing | `drums.DRILL_HATS` | NI drill guide; Attack Magazine Beat Dissected: UK Drill |
| Boom bap and lo-fi: snare on 2 and 4, eighth-note hats, an open hat ends the phrase | `drums._bar` | Attack Magazine Beat Dissected: 90s Boom Bap |
| Swing is taken from the sample (54 to 66 percent), never less than 54 in boom bap | `analysis._swing`, `compose.variation` | Roger Linn interview (Attack); Frane 2017 measured a median of about 54.5 percent in classic breaks |
| Each drum keeps a constant lean against the grid; random jitter is a few ticks | `drums.LAY` | Charnas, *Dilla Time* (via the `mc-style-hiphop` skill) |

## Transitions

No risers. A section change is either a **cut** (drums and 808 stop for the last two beats, only the backbeat
stays) or a short **fill**, alternating, and the crash marks only the first time the full beat arrives.
Verses drop the percussion lane and use a second kick pattern. Sources: songen.app on trap arrangement ("a cut
marks a transition more clearly than a riser"), beatsbychemiztry.com on dropouts and fills.

## Arrangement

Sections are whole four-bar phrases when the length allows. Trap and drill are counted half-time, so they get
twice the bars for the same duration (intro 8, hook 16, verse 16 at the default length). Sources: common
"type beat" structures (intro 4 to 8, hook 8, verse 16) from hiphopmakers.com and songen.app.

## 808

Roots only, taken from the sample's own bass register, in E1 to E2 where an 808 is audible and powerful
(LANDR: 30 to 60 Hz power zone; Deruty 2025: median 808 fundamental 49.5 Hz). Notes start on kicks. Trap and
drill hold long notes and may slide into the next root; boom bap and lo-fi play short notes. On a cut the 808
stops with the drums.

## Mix

| Rule | Where | Source |
| --- | --- | --- |
| Track levels are set from a test render of the hook: each track's loudness relative to the kick | `compose.balance`, `compose.BALANCE` | Element loudness guides (integraudio.com); "beginners mix hats 4 to 6 dB too loud" (hitproducerstash.com) |
| A dull sample gets a high shelf (at most 5 dB) so it sits like a record | `compose.PRESENCE` | Pestana et al., AES 135: commercial masters fall about 5 dB per octave from 100 Hz to 4 kHz |
| The sample is high-passed at 100 to 150 Hz when an 808 plays | `compose.SAMPLE_FX` | LANDR mixing beats; songen.app |
| The sample dips under each kick | `compose.SAMPLE_DUCK` | sidechain practice (EDMProd; `mix-engineer` skill) |
| Hats sit 20 percent off centre, drums are dry | `compose.TRACKS` | mixinggpt.com; audiospectra.net |
| Master: gentle bus compression, -11 LUFS, -1 dBTP ceiling | `render._master` | Spotify loudness normalisation; rap masters commonly run -10 to -8 LUFS |

## What is measured

`scripts/diagnose.py` on the recordings in `backend/tests/audio`: detected tempo against the known tempo, timing
of the sample track against the grid, share of 808 notes that agree with the harmony under them, time-stretch
used, levels per track, and the tone of the hook against the 5 dB per octave reference. `backend/tests`
additionally pins the hat rules above (`test_hats_stay_out_of_the_way`).

Not measured: whether a flip is musically interesting. That needs ears.
