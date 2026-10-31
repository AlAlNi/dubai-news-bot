# «Что нового с первого числа»

The existing hourly collector prepares one issue on the last Dubai calendar day,
from 17:00 Asia/Dubai (13:00 UTC). The existing publisher sends it from 18:00
Dubai (14:00 UTC), on its first successful scheduled/collector-triggered run.
GitHub schedules are best-effort, not a precise-time delivery SLA. No second
workflow or external automation is added. Regular news and retro remain intact.

## Evidence and editorial policy

- One bounded search for confirmed Dubai/UAE changes taking effect on the exact
  next first-of-month date, including year. No generic monthly events or filler.
- Only HTTPS official primary domains: `*.gov.ae`, `u.ae`, `rta.ae`,
  `mediaoffice.ae`, `wam.ae`. Every redirect must satisfy the same policy and
  existing public-DNS checks. Search snippets/generated prose are never evidence.
- Download dated official HTML announcements from the preceding 366 days.
  The announcement date is **not** the effective date. Undated pages, PDFs,
  inaccessible pages and oversized/full-review-incompatible text fail closed.
- Up to three full notices, no truncated article evidence. The pinned existing
  editor writes the digest; the existing full-claims verifier checks every
  claim, heading, exact effective date/year, scope and exceptions under an
  additional monthly eligibility policy. A proposal/forecast/deadline does not
  become a confirmed change. One genuine change is enough; no qualifying facts
  means no post. This is model-assisted source-fidelity review, not a guarantee
  that an authority's statement is true or exhaustive discovery.

## Budget and next-day carryover

All calls share the existing historical `openai_budget.json`, model and limits.
No ledger resets, bypasses or separate allowance. One search + one writing + one
full verification reserves at most **$0.04612 / 3 calls**. This consumes the same
normal daily/monthly allowance; it is not an additional budget or guaranteed
monthly set-aside. The approved collector profile still allows 12 calls/day
Oct 24–31 2026 UTC, returning to 8 on Nov 1. The $3 monthly hard cap is unchanged.

If an actual daily call/search or monthly budget limit blocks preparation, the
issue is retained and may resume **once on the first, after 04:00 Dubai**, when
UTC budget day/month rolls over. Already discovered URLs are reused and sources
are downloaded again; writing/verification remain mandatory and content-keyed.
The heading uses the absolute effective date, so it is correct on either day.
A ready issue left at midnight can also publish on the first after 04:00.
No new issue is started on the first without an existing pending month slot.
No catch-up from the second onward; exhausted carryover, technical failures,
missing evidence or rejected claims do not lead to unlimited retries.

## Durable state and delivery safety

`storage/dubai_news/monthly_changes.json` stores each target YYYY-MM slot,
attempt, evidence, post, hashes, verifier result and send outcome. Workflows use
existing `dubai-news-storage` concurrency and main checkout. Each paid discovery
attempt and the `sending` marker are committed/pushed **before** its external
side effect. Paid requests keep the existing durable budget reservations.
Publication verifies the exact source/post hashes and re-fetches the official
notices immediately before sending. Changed or unavailable evidence blocks delivery
without reusing stale approval. The marker is never cleared
on timeout: `sending`, `send_unknown`, and `send_failed` require manual
reconciliation, never automatic resend. This favors at-most-once delivery over
unverifiable exactly-once claims; a crash can cause a missed issue.

Only production main Actions schedule/workflow-run/manual events execute the
module; local default functions are dry-run, push events do not run the rubric.
The normal publisher can send a regular news post in the same run; this rubric
is one separate post/month, not a reduction of the regular news quota.

## Verification / activation

Run `python -m pytest tests -q` with `src/requirements.txt`, pytest and
pytest-subtests installed. Tests mock OpenAI, downloads, Git and Telegram.
No live paid check or Telegram test is needed to run this suite.
A draft PR does not activate the rubric. Merge/deployment requires separate
approval. Do not manually dispatch production to test this feature.
