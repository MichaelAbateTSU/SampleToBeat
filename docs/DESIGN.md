# ChopLab design language: "sampler hardware"

The interface borrows from the machines beatmakers already love: a grey chassis, dark rubber pads, an amber
LCD and one orange key you press to make things happen. It replaced a dark amber-gradient theme that looked
like every other AI music tool.

Guided by the skills in `.claude/skills/` (`frontend-design`, `baseline-ui`, `fixing-accessibility`,
`fixing-motion-performance`, `web-design-guidelines`).

## Tokens (`frontend/src/styles.css`)

| Role | Token | Value |
| --- | --- | --- |
| Page | `--chassis` | `#d5d6d2` |
| Raised surfaces | `--panel`, `--raised` | `#e7e7e3`, `#f3f3f0` |
| Text | `--ink`, `--ink-2`, `--ink-3` | `#17181a`, `#44464a`, `#5c5e62` |
| Pads and wells | `--pad`, `--pad-2` | `#25272b`, `#303338` |
| The one action colour | `--signal` | `#f04e14` (always with ink text, 5.3:1) |
| Readouts | `--lcd` on `--lcd-bg` | `#ffb52e` on `#1b1a12` |

Type: **Archivo** (variable weight and width). Headlines are heavy and expanded, body is regular width, small
labels are slightly condensed. **Doto** (dot matrix) is only for LCD readouts: tempo, key, time, slider values.
Track colours appear only inside the timeline, where they identify tracks.

## Principles

1. **One loud thing per screen.** On the landing page it is the playable sampler in the hero; in the app it is
   the four flip pads. Everything around them is plain text on the chassis with hairline rules, not cards.
2. **Things you press look pressable.** Keys have a thicker bottom edge and move down 2px when pressed. No glows,
   no gradients, no hover lifts.
3. **Dark means "this is the instrument".** Pads, the timeline well, the progress display and the player are dark;
   settings and reading matter are light.
4. **Radius encodes the object**: chips 4, keys 8, pads 10, panels 14, the machine 22.
5. **Motion only answers an action**: key presses, the playhead, progress. Animations use transform and opacity
   and switch off under `prefers-reduced-motion`.
6. **Plain words.** Buttons say what happens ("Make my beat", "Export beat", "Delete flip"). No all-caps labels,
   no decorative arrows.

## Baseline every screen meets

Visible focus, skip link, labelled controls, focus-trapped dialogs that return focus, confirmation before
anything destructive, inline errors tied to their fields, tabs reflected in the URL, a warning before leaving
with unsaved mix changes, no horizontal scroll at 390px, safe-area padding for the fixed player.
