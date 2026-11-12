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

`web-design-guidelines` normally downloads its rule list at run time; a copy from
github.com/vercel-labs/web-interface-guidelines (434b7f9, MIT) is kept in `references/` so reviews work offline
and the rules applied here are pinned.

`baseline-ui` assumes Tailwind. This app uses plain CSS with design tokens, so its rules were applied in spirit
(no decorative gradients or glows, compositor-only animation, balanced headings, tabular numbers, one accent).
