# Going live: what is ready and what needs you

Everything that can be prepared without your accounts is in the repository and tested. The steps below need
your accounts or your money, in this order. Expect about an hour.

## 1. Host it on Render (about $26 a month)

The engine peaks at 1.1 to 1.4 GB of memory while rendering (measured; it grows with upload length, which is
why uploads are capped at 5 minutes on this server) and needs a disk that survives restarts. Render's free plan
has 512 MB, a tenth of a CPU and no disk, so it cannot run the product; earlier measurements on this repo's
hosting branch showed it running out of memory. `render.yaml` therefore asks for the Standard plan
(2 GB, 1 CPU, $25) plus a 10 GB disk (about $1).

1. Merge the pull request so `main` has the app.
2. In Render: **New > Blueprint**, pick this repository. Leave the secret fields empty for now and deploy.
3. When it is live you get an address like `https://choplab.onrender.com`. Open it and make a beat.

A custom domain is optional: add it in Render, then set `CHOPLAB_BASE_URL` to it.

## 2. Take payments with Stripe

1. Create or open your Stripe account. Start in **test mode**.
2. Run, with your test secret key:
   `STRIPE_SECRET_KEY=sk_test_... python scripts/stripe_setup.py https://<your address>`
   It creates the Pro and Studio products, four prices and the webhook, and prints the variables to set.
3. Paste those into Render (Environment tab). The service restarts.
4. In Stripe: Settings > Billing > Customer portal, switch it on.
5. On the site: sign up, choose Pro, pay with card `4242 4242 4242 4242`. Your account page should show Pro
   and WAV export should unlock. Cancel from "Manage billing" and check it drops back to Free at period end.
6. Repeat step 2 and 3 with your **live** key. Make one real purchase yourself and refund it.

## 3. Email for password resets

Any SMTP provider works (Resend, Postmark, SES). Set `CHOPLAB_SMTP_HOST`, `CHOPLAB_SMTP_USER`,
`CHOPLAB_SMTP_PASSWORD` and `CHOPLAB_MAIL_FROM`. Until then, "Forgot password" does not send anything.

## 4. Before you tell anyone

- Build-time values: set `VITE_SUPPORT_EMAIL` and `VITE_LEGAL_ENTITY` (Render: Docker build args) so Terms,
  Privacy and Contact show your details instead of placeholders.
- Have the Terms and Privacy pages reviewed. They describe what the software does, but they are not legal advice.
- Check the name "ChopLab" for trademark conflicts and buy the domain.
- Set up backups of the Render disk (snapshots are in the disk's settings).

## 5. Post

`marketing/launch/POSTS.md` has copy for YouTube, TikTok/Reels/Shorts, X, Reddit and Product Hunt; the
trailer is in `marketing/trailer/` (16:9) and `marketing/launch/` (9:16).

## What one server handles

One Standard instance renders one beat at a time (about 30 to 60 seconds each) and queues the rest. That is
fine for the first few hundred users. When the queue gets long: raise the plan and `CHOPLAB_WORKERS`, then
move files to object storage and jobs to a queue (the `michaelabatetsu-*` hosting branches explore this).
