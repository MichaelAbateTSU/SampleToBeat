# Product research (November 2026)

What the market looks like, where ChopLab fits, and why it is priced the way it is. Figures are from public
web sources found on the dates searched; several are vendor or blog claims and are marked as such.

## The gap

There are two crowded categories and little between them.

**Text-to-song generators** (Suno, Udio, Soundraw, Soundful) make a whole track from a prompt. They are cheap
and fast, but the producer does not control the source material, and rights are unsettled: UMG and Warner
settled with Udio in late 2025 and Warner with Suno, with licensed models promised for 2026, while other
label suits against Suno were still reported active. One comparison site reported Udio suspending WAV and
stem downloads during its licensing transition.

**Sampling tools for producers** (Serato Sample, Tracklib, Splice) help you find and chop a sample, then leave
the beat to you. Splice's 2026 AI tools (Variations, Craft, Magic Fit) alter samples from its own library;
coverage does not describe them as turning a user's sample into a finished beat.

ChopLab sits in the gap: **bring your own sample, get a finished, editable beat.** It is not a prompt box and
it is not a blank sampler.

## What producers say they want

- A 2026 Sonarworks survey of 1,194 creators: 77% worry AI will damage creativity and only 21% use it for
  composition; originality was the most consistent concern.
- Trade guides repeatedly name stems and standard MIDI as the things that make AI output usable in a DAW.
  (These are vendor and blog sources, not survey data.)

Design consequences: the engine is deterministic signal processing and music theory rather than a model
trained on other people's music; every part is editable; exports are stems and MIDI, not only a mixdown.

## Pricing anchors

| Product | Price | Note |
| --- | --- | --- |
| Serato Sample 2 | $129 once, or $9.99/mo in Producer Suite | Serato's pricing page |
| Suno Pro | about $8 to $10/mo | sources disagree |
| Udio Standard | $10/mo | third-party comparison |
| Splice Sounds+ | $12.99/mo | about 600,000 subscribers and over $100M revenue (FT report, April 2025; not audited) |
| Tracklib | about $8 to $9/mo plus per-release clearance | sources vary by date |
| BandLab Membership | about $99/yr | Songstarter tools are free |

Producers already pay around $10 a month for a tool like this, so Pro is $9.99. Studio at $24.99 is for
volume and the highest-quality files.

Free-to-paid conversion for self-serve freemium is commonly quoted at 3 to 5% as good and 8% or more as
strong (ChartMogul, First Page Sage; B2B-heavy samples). The funnel is built to earn that: hear four beats
from your own sample before any sign-up, create an account to download, pay for release-quality files and a
commercial license.

Compute per generation is roughly 30 CPU-seconds on a 4-core machine for four 36-bar flips, so a generous
free tier is affordable and the paid gate is on file quality and rights rather than on listening.

## Rights position

ChopLab licenses what it generates (drums, 808, arrangement, mix). It cannot license the uploaded sample, and
says so on the pricing page, in the FAQ, in the Terms and in the license file saved with every export.
Tracklib's whole business is clearance, which shows how much weight this carries; a clearance partnership is
a natural later step.

## Sources

- https://www.sonarworks.com/blog/research/future-music-production-human-producer-survey-2026
- https://www.musicbusinessworldwide.com/25-of-music-producers-are-now-using-ai-survey-says/
- https://www.musicbusinessworldwide.com/splice-launches-ai-tools-that-compensate-sample-creators-when-their-sounds-are-used/
- https://splice.com/blog/letter-from-our-ceo-september-2025/
- https://www.musicbusinessworldwide.com/universal-music-settles-udio-lawsuit-strikes-deal-for-licensed-ai-music-platform
- https://www.musicbusinessworldwide.com/warner-music-settles-udio-lawsuit-strikes-licensing-deal-with-ai-platform
- https://www.musicbusinessworldwide.com/warner-music-group-settles-with-suno/
- https://production-1.serato.com/sample/pricing
- https://musicradar.com/news/music-sampling-service-tracklib-switches-to-a-subscription-model-and-adds-more-songs-and-features
- https://costbench.com/stats/ai-music-generators-pricing-2026/
- https://www.costbench.com/compare/suno-vs-udio/
- https://siliconvalleyinvestclub.com/companies/splice/
- https://chartmogul.com/reports/saas-conversion-report/
- https://userpilot.com/blog/freemium-saas/
