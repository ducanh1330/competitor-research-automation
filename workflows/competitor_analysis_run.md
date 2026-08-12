# Competitor Analysis Run

## Objective
The master workflow. Snapshot every tracked competitor page, diff against last month,
analyse across the four dimensions, and render the branded PDF.

Handles both the **baseline** run (no prior snapshots — the diff step is skipped) and every
**recurring** monthly run after it. Same document, one branch.

## Inputs
| Input | Required | Notes |
|---|---|---|
| `research/competitors.json` | yes | Must have `approved_by_user: true` |
| `profile/company.json` | yes | The comparison is *against this*, not generic |
| `brand/brand.json` | yes | Must pass `validate_brand.py` |
| `--date` | no | Defaults to today. Override to re-run a past month. |

## Steps

### 1. Preflight
```
uv run tools/validate_brand.py
```
Confirm `research/competitors.json` has `approved_by_user: true`. If it does not,
stop and run `discover_competitors.md` — never analyse an unapproved set.

### 2. Snapshot every tracked page
For each competitor, for each page in its `pages` map:
```
uv run tools/fetch_page_snapshot.py --url <url> --competitor <slug> --page-type <type>
```
Writes: `research/snapshots/<date>/<slug>/<type>.{html,md,png,json}`

- Exit code **2** on a pricing page means extraction returned no price signals.
  Do not ignore it — inspect the saved `.html` and `.png`. Either the extractor needs
  work, or the page is genuinely gated (set `pricing_gated: true` in
  `competitors.json`).
- Also snapshot the user's own pages under slug `self`. Comparing DOZ.AI against
  competitors requires DOZ.AI to be captured the same way, on the same date.

### 3. Diff against the previous snapshot *(recurring runs only)*
```
uv run tools/diff_snapshots.py --all --latest-two
```
Writes: `.tmp/diff/<slug>_<from>_to_<to>.json`

If a competitor has only one snapshot, the tool says so and skips it. That is the
baseline case, not an error.

### 4. Analyse — the judgment step

Read the extracted `.md` files. **Only analyse pages the diff reports as changed**,
plus everything on a baseline run. Unchanged pages carry last month's conclusions
forward; re-reading them wastes effort and invites inconsistency.

Produce `research/analysis/<YYYY-MM>/analysis.json` covering the four dimensions:

**Pricing & business model** — tiers, price points, what is included, billing model,
discounts, contract terms. Compare to the user's own row from `company.json`.

**Marketing & messaging** — headline claims, stated audience, proof points. Identify
*crowded* language (claims several players make, so worthless as differentiation) and
*whitespace* (claims nobody makes).

**Products, services & features** — build the capability matrix. Cells are strictly
`yes` / `partial` / `no`. Populate gaps in both directions.

**Strengths & weaknesses vs us** — per competitor, grounded in the user's own
`self_assessment`. Generic SWOT is worthless here; the value is in the comparison.

Then derive:
- **What's working for them** — tactics with observable evidence, and why they work.
- **Where we can improve** — prioritised, impact vs effort, each traced to a finding
  above via `traces_to`. A recommendation that traces to nothing is an opinion.

> ### The evidence rule — non-negotiable
> Every claim carries `sources: [{url, snapshot_date}]`, or `inferred: true`.
> A competitor's price is stated **only** if it appears in a snapshot.
> `render_report.py` refuses to render a claim with neither.
>
> This exists because competitor pricing is exactly the detail that feels safe to
> recall and is frequently wrong. A confidently wrong price point is worse than an
> acknowledged gap — the user may act on it.

### 5. Append to the changelog
Add a dated entry to `research/CHANGELOG.md`: what moved, per competitor, one line each.
This is the human-scannable history; the snapshots are the machine-readable one.

### 6. Render the PDF
```
uv run tools/render_report.py \
  --data research/analysis/<YYYY-MM>/analysis.json \
  --out research/reports/<YYYY-MM>/report.pdf
```
- If `brand.json`'s chart ramp is still unapproved this fails by design. Get sign-off
  and set `approved_by_user: true`, or pass `--allow-unapproved-chart-colors` for a
  preview only.
- `--keep-html` also writes the intermediate HTML, which is far quicker to iterate on
  than re-rendering the PDF.

### 7. Verify before delivering
- Spot-check five claims against their cited source URLs.
- Confirm no colour outside the brand palette appears anywhere.
- Confirm the "what changed" section is present (recurring) or absent (baseline).

## Output
`research/reports/<YYYY-MM>/report.pdf` — the deliverable — plus `analysis.json`,
the dated snapshots, and a new `CHANGELOG.md` entry.

## Edge cases & failure handling
- **Competitor site is down** → do not save a failed fetch. The tool exits non-zero on
  HTTP ≥ 400 by design. Retry once; if still down, note it in the report as not
  captured this month rather than carrying stale data forward silently.
- **A page 404s that worked last month** → that *is* a finding. A removed pricing page
  usually means repackaging. Record it in the changes section.
- **Extraction returns almost nothing** → the page is JS-gated behind interaction.
  Check the screenshot; consider whether a different URL exposes the same content.
- **Hash changed but diff shows nothing** → volatile markup only. The tool already
  reports these as unchanged; no action needed.
- **Every page shows as changed** → suspect the extractor, not the market. Something
  volatile is leaking into the normalised text. Fix `VOLATILE_PATTERNS` in
  `fetch_page_snapshot.py` rather than accepting the noise.
- **Competitor redirects to a new domain** → `meta.json` records `redirected: true` and
  the `final_url`. Update `competitors.json`.

## Learned constraints

- **2026-08-12 — Block-level extraction misses pricing entirely.** Extracting only
  `<p>/<li>/<h*>` captured Vercel's feature list but none of its prices or plan
  names, which live in `<div>`/`<span>`. Extraction now emits *text leaves* — any
  element with no descendant carrying its own text — which reaches those without
  duplicating ancestor text. Verified: Hobby/$0, Pro/$20 now captured.
- **2026-08-12 — Never blanket-strip `<header>`/`<footer>`.** A `<header>` nested in a
  pricing card holds the plan name. Only page-level ones (direct children of `<body>`)
  are chrome.
- **2026-08-12 — A pricing page with zero price signals is a failure, not a result.**
  `fetch_page_snapshot.py` exits 2 rather than saving a snapshot that would make the
  analysis report "no pricing found" for a competitor that plainly publishes pricing.
- **2026-08-12 — `networkidle` is unreachable on many marketing sites.** Long-poll
  connections and analytics beacons keep the network busy indefinitely. The tool waits
  8s then continues; treat the note in stderr as normal, not as a problem.
- **2026-08-12 — Firecrawl is now the primary fetch path; Playwright is the fallback.**
  Local Chromium missed content that mattered: Apollo's homepage gave 225 characters
  against Firecrawl's 9,182, and Clay's pricing table — the whole reason to track Clay —
  was invisible to it entirely. Firecrawl captured Clay's real tiers ($54-486/mo credits,
  $60-540/mo actions, bundles at $167 and $446), turning a "not captured" row into a
  finding.
- **2026-08-12 — Firecrawl needs `onlyMainContent: true` and markdown cleanup.**
  With it off, Smartlead's pricing page returned 52,052 characters against about 6,000
  of real content; the rest was mega-menu markup and CDN image URLs. Those URLs carry
  cache-busting hashes that change between fetches, so leaving them in would flag every
  page as changed every month. `clean_markdown()` additionally strips image embeds and
  link hrefs while keeping link text.
- **2026-08-12 — Never mix fetch methods inside one baseline.** The two extractors
  differ enough that a cross-method diff reports extraction artifacts as competitor
  changes. When the primary path changes, re-capture the whole set. `meta.json` records
  `fetched_via` and `diff_snapshots.py` emits a loud warning on mismatch.
