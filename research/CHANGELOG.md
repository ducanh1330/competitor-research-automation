# Competitor Changelog

Append-only. One dated entry per analysis run, newest first. This is the
human-scannable history; `research/snapshots/` is the machine-readable one.

Written by `workflows/competitor_analysis_run.md`, step 5. Every line here should
be traceable to a diff in `.tmp/diff/` produced from two dated snapshots — nothing
in this file is written from recollection.

---

## 2026-08-12 — Baseline run

First run. No prior snapshots, so no diffs — this records the starting state that
every future month is measured against.

**Set established (7):** Apollo.io, Instantly, Clay, Lemlist, Smartlead (direct) ·
AiSDR (adjacent) · 11x (aspirational). 14 pages captured.

**Baseline pricing, first-party as of today:**

| Competitor | Entry | Metered on |
|---|---|---|
| Smartlead | $39/mo | send volume, verified emails |
| Instantly | $47/mo | flat + credits, unlimited mailboxes |
| Apollo.io | $49/seat/mo | seats + credits (annual grant) |
| Lemlist | $69/mo | flat, **unlimited users** |
| Clay | $54/mo ¹ | Data Credits + Actions |
| AiSDR | $900/mo | managed outcome |
| 11x | not published | — |

¹ **Corrected within the baseline run.** This row first read "not captured" — Clay's
pricing table was invisible to Playwright. Switching the primary fetch path to
Firecrawl captured it properly ($54–486/mo credits, $60–540/mo actions, bundles at
$167 and $446), so the row is sourced rather than blank. Recorded here rather than
silently overwritten, because which conclusions moved is itself worth seeing.

**Watch list for next month:**
- Lemlist raised prices in Feb 2026 per third-party reports; watch for another move.
- Apollo's `/product` URL redirects to home; the tracked page list needs fixing.
- Whether anyone else adopts unlimited-user pricing — that would confirm the shift
  away from per-seat rather than it being Lemlist's tactic alone.

**Corrections to received wisdom:** third-party comparison articles quoted Instantly
at $37; the live page says $47. Blog-sourced pricing in this category is unreliable.
