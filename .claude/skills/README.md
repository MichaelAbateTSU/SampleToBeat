# Design skills used on this project

Third-party agent skills, vendored unmodified from GitHub so every Claude Code session on this repo has them.
They guided the "sampler hardware" redesign of the web app (see `docs/DESIGN.md`).

| Skill | Source | Commit | License |
| --- | --- | --- | --- |
| `frontend-design` | github.com/anthropics/skills | 2c5958e | Apache-2.0 (`LICENSE.txt`) |
| `baseline-ui` | github.com/ibelick/ui-skills | ba966a3 | MIT |
| `fixing-accessibility` | github.com/ibelick/ui-skills | ba966a3 | MIT |
| `fixing-motion-performance` | github.com/ibelick/ui-skills | ba966a3 | MIT |
| `web-design-guidelines` | github.com/vercel-labs/agent-skills | 1a2c79d | MIT |
| `mix-engineer`, `mastering-engineer` | github.com/bitwize-music-studio/claude-ai-music-skills | 597d928 | CC0-1.0 |
| `audio-dsp-review`, `audio-artifacts-debug` | github.com/kunitoki/sonic-skills | 8b6dcfe | Public domain |
| `mc-style-hiphop`, `mc-rhythm-section`, `mc-mix-intent` | github.com/jtydhr88/music-composition-skills | e283141 | MIT |

`web-design-guidelines` normally downloads its rule list at run time; a copy from
github.com/vercel-labs/web-interface-guidelines (434b7f9, MIT) is kept in `references/` so reviews work offline
and the rules applied here are pinned.

`baseline-ui` assumes Tailwind. This app uses plain CSS with design tokens, so its rules were applied in spirit
(no decorative gradients or glows, compositor-only animation, balanced headings, tabular numbers, one accent).

## Music skills

Added for the beat engine (`backend/src/choplab`). What they changed there:

- `mc-style-hiphop`, `mc-rhythm-section` (written in Chinese): feel is programmed, not random. Each drum keeps a
  constant distance from the grid for the whole beat (`drums.LAY`), random jitter is small, the kick is short and
  the 808 carries the harmony, arrangements work by taking parts away.
- `mix-engineer`, `mc-mix-intent`: the sample dips on every kick (`compose.SAMPLE_DUCK`), hats sit off centre,
  drums stay dry, gentle bus compression before loudness normalisation.
- `audio-dsp-review`, `audio-artifacts-debug`: checklists for clicks, aliasing and gain staging when touching
  `render.py`, `transform.py` or the drum synthesis.

Considered and left out: FerroxLabs/murage (`beat-maker`), whose repository licence (AGPL-3.0) conflicts with
the licence named in its skill files.
