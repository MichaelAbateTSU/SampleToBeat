# ChopLab

**Drop a sample. Get a beat.** ChopLab turns any piece of audio (a loop, a one-shot, a piece of a song) into
four finished instrumentals with drums, an 808 line in key and a full arrangement, then exports WAV, stems
and MIDI.

It is a complete product: a web app, accounts, free and paid plans with Stripe billing, and a deterministic
audio engine. No generative model is involved, so every sound traces back to the uploaded sample or the
built-in drum kits.

| | |
| --- | --- |
| `backend/` | Python engine and HTTP API (FastAPI): analysis, chopping, drums, 808, arrangement, mastering, accounts, plans, billing |
| `frontend/` | React + TypeScript web app (landing page, create flow, studio, export, account) |
| `marketing/` | Launch trailer and brand assets, with the scripts that made them |
| `docs/` | Product research, design language |
| `.claude/skills/` | Third-party design skills used for the UI |

## Run it

```bash
# 1. web app
cd frontend && npm ci && npm run build

# 2. API + engine (serves the built web app too). Needs Python 3.12, uv and ffmpeg.
cd ../backend && uv sync
CHOPLAB_SELF_HOSTED=1 uv run choplab        # everything unlocked, for your own machine
# open http://localhost:8765
```

Without `CHOPLAB_SELF_HOSTED=1` the app runs as the hosted product: visitors start as guests, plans and
limits apply. Add `CHOPLAB_BILLING=mock` to try upgrades without Stripe.

For front-end work run `npm run dev` in `frontend/` (port 5173, proxies `/api` to 8765).

### Docker

```bash
cp .env.example .env    # fill in what you need
docker compose up --build
```

The image has not been built in CI yet; treat the Dockerfile as a starting point and build it once before
relying on it.

## How a beat is made

1. **Read the sample.** Tempo, beat grid, downbeat, key, onsets and structure. A tightly cut loop gets its
   exact tempo from its length; a single hit is recognised as a one-shot.
2. **Find the material.** Short uploads are used whole. Longer recordings are searched for the best loops and
   chops (harmonic stability, phrase coherence, how cleanly it loops, level, distinctiveness, bleed).
3. **Compose four flips**, one per strategy: loop flip, hard chops, dark and pitched, creative. Each has
   genre-specific drums with swing and fills, an 808 line that follows the chords of the flipped sample, and an
   arrangement with intro, hooks, verses, breakdown and outro. One-shots are played as a minor-key melody.
4. **Render and check.** Every flip is mixed, mastered to -14 LUFS with a true-peak ceiling and run through
   technical checks (not silent, not clipped, no dead sections, distinct from its siblings). Failures are
   regenerated with a new seed.

Everything is seeded, so a flip can be reproduced exactly.

## Plans

Defined in `backend/src/choplab/accounts.py` and enforced by the API; the UI reads them from `/api/plans`.

| | Free | Pro ($9.99/mo) | Studio ($24.99/mo) |
| --- | --- | --- | --- |
| Generations a month | 15 | 300 | 2,000 |
| Export | MP3 | WAV 16/24-bit, MP3 | + WAV 32-bit float |
| Stems, MIDI, project file | no | yes | yes |
| License for generated material | personal | commercial | commercial |
| Upload limit | 50 MB / 6 min | 200 MB / 12 min | 1 GB / 20 min |

Guests can make 3 beats before creating an account. Downloading needs an account.

## Configuration

See `.env.example`. The important ones:

| Variable | Purpose |
| --- | --- |
| `CHOPLAB_BASE_URL` | Public URL. `https` turns on Secure cookies and HSTS. |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_*` | Billing. Point a Stripe webhook at `/api/billing/webhook` for `checkout.session.completed` and `customer.subscription.*`. |
| `CHOPLAB_SMTP_*`, `CHOPLAB_MAIL_FROM` | Password-reset email. Without it, reset links only appear in the server log. |
| `CHOPLAB_DATA` | Where uploads, renders and the SQLite database live. |
| `CHOPLAB_WORKERS` | Background workers for analysis and generation. |
| `CHOPLAB_TRUST_PROXY` | Set to 1 behind your own reverse proxy so rate limits see real client IPs. |

## Tests

```bash
cd backend && uv run pytest            # engine, API, accounts, billing webhooks, tenant isolation
cd frontend && npm run build           # type-check and build
# full journey in a real browser (needs the server running with CHOPLAB_BILLING=mock):
cd frontend && node e2e/smoke.mjs
```

## Before you launch

These are decisions and accounts only the owner can supply:

- **Stripe**: create the four prices, set the env vars, register the webhook.
- **Email**: an SMTP provider for password resets.
- **Legal**: the Terms and Privacy pages are written to match what the software does, but have a lawyer
  review them, and set `VITE_SUPPORT_EMAIL` and `VITE_LEGAL_ENTITY` at build time (they default to placeholders).
- **Name**: check that "ChopLab" is free to use as a trademark and domain in your markets.
- **Scale**: storage is local disk plus SQLite and jobs run in-process, which suits one server. Past that,
  move files to object storage and jobs to a queue.
- **Sample rights**: the product tells users they are responsible for what they upload and has a takedown
  contact. Decide how you will handle complaints.
