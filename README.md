# competitor-research-automation

Automated monthly competitor research where **every claim in the report traces to a dated snapshot of the page it came from** — or is explicitly flagged as inferred. It discovers competitors from a business profile, captures their pages on a fixed cadence, diffs them month over month, and renders a brand-styled PDF and web report.

Built on a **WAT** architecture — Workflows, Agents, Tools — which keeps probabilistic AI on the judgment calls and deterministic Python on everything that has to be repeatable.

---

## Why the sourcing matters

Competitor pricing is exactly the kind of detail that feels safe to recall and is frequently wrong. A confidently wrong price point is worse than an acknowledged gap, because someone will act on it.

So the pipeline enforces one rule end to end:

> Every claim carries `sources: [{url, snapshot_date}]`, or `inferred: true`.
> A competitor's price is stated **only** if it appears in a captured snapshot.

`render_report.py` refuses to render a claim that has neither. In the August 2026 run this rule did real work: Clay's plan prices were initially unreachable, third-party figures were freely available, and the report said *"not captured"* rather than quoting them. (A later scraper change captured them properly, and they went in with a source.)

## Why month-over-month diffing needs deterministic tooling

Change detection is only meaningful if every capture is taken the same way. That is why fetching, hashing, diffing and rendering are all plain Python, not model calls:

- Every page capture stores a SHA-256 of its **normalised** extracted text. Volatile content — cookie banners, rotating testimonials, injected timestamps, CSRF tokens — is stripped before hashing, so a page only reads as "changed" when it actually changed.
- On a recurring run, pages whose hash is unchanged are **skipped entirely**. A typical month moves 2–3 pages out of ~25.
- `meta.json` records `fetched_via`. Comparing a Firecrawl snapshot against a Playwright one surfaces extraction differences that look exactly like competitor changes, so `diff_snapshots.py` warns loudly on a mismatch rather than letting the analysis report a rewrite that never happened.

---

## Architecture

```
Business profile ─► AI discovery ─► YOU APPROVE the set ─┐
                                                          ▼
                    ┌──────────────── monthly run ────────────────┐
                    │ 1. fetch_page_snapshot.py   (deterministic) │
                    │ 2. diff_snapshots.py        (deterministic) │
                    │ 3. analysis across 4 dimensions  (judgment) │
                    │ 4. render_report.py         (deterministic) │
                    └─────────────────────────────────────────────┘
                                                          ▼
                                          Branded PDF + web report
```

**Workflows** (`workflows/`) are markdown SOPs — the objective, inputs, tool sequence, expected output, and how to handle each failure mode. **Agents** read the workflow and orchestrate. **Tools** (`tools/`) do the execution and never make judgment calls.

### Tools

| Tool | Does |
|---|---|
| `fetch_page_snapshot.py` | Captures one page → raw HTML, extracted markdown, full-page screenshot, and metadata with a content hash. Firecrawl primary, local Playwright as automatic fallback. |
| `diff_snapshots.py` | Compares two snapshot dates. Hash-equal pages are skipped; changed pages get a unified diff plus a dedicated price-change delta. |
| `render_report.py` | `analysis.json` + `brand.json` → branded PDF, charts drawn with matplotlib. |
| `render_report_web.py` | Same data → a self-contained, theme-aware HTML page. |
| `validate_brand.py` | Gate on `brand.json`. Recomputes contrast, reads font embedding permission out of the binary, verifies assets exist. |
| `derive_logo_variants.py` | Splits the supplied lockup into symbol-only and reversed variants; measures the clear-space unit. |
| `build_font_instances.py` | Generates static weight instances from the variable fonts. |

### Workflows

| Workflow | When |
|---|---|
| `setup_brand.md` | Once — guidelines → `brand.json` |
| `capture_business_profile.md` | Once, refresh as needed — interview → profile |
| `discover_competitors.md` | Setup, then quarterly — find and tier the set |
| `competitor_analysis_run.md` | **Monthly** — the master document |

---

## Getting started

```bash
uv sync
uv run playwright install chromium          # one-time, ~150MB

cp .env.example .env                        # then add FIRECRAWL_API_KEY
uv run tools/validate_brand.py              # confirm brand.json is sound
```

Every tool runs standalone and documents itself:

```bash
uv run tools/<name>.py --help
```

### A monthly run

```bash
# 1. Capture each tracked page (repeat per competitor/page in competitors.json)
uv run tools/fetch_page_snapshot.py \
  --url https://competitor.com/pricing --competitor acme --page-type pricing

# 2. Diff against last month — hash-equal pages are skipped
uv run tools/diff_snapshots.py --all --latest-two

# 3. (analysis step — see workflows/competitor_analysis_run.md)

# 4. Render
uv run tools/render_report.py \
  --data research/analysis/2026-08/analysis.json \
  --out  research/reports/2026-08/report.pdf
```

`--no-firecrawl` forces the local Playwright path and consumes no API credits.

---

## Scraping

**Firecrawl is the primary fetch path; local Playwright is an automatic fallback** whenever Firecrawl fails, hits its quota, or no key is configured — so a run degrades rather than stopping.

Firecrawl leads because it reaches JS-gated content local Chromium misses. Measured on the same pages: Apollo's homepage gave **225 characters** via Playwright and **9,182** via Firecrawl, and Clay's pricing table — the entire reason for tracking Clay — was invisible to Playwright and fully captured by Firecrawl.

Two settings matter and are easy to get wrong:

- **`onlyMainContent` must be on.** With it off, Smartlead's pricing page returned 52,052 characters against roughly 6,000 of real content; the rest was mega-menu markup and CDN image URLs. Those URLs carry cache-busting hashes that change between fetches, so leaving them in flags every page as changed every month.
- **Never mix fetch methods inside one baseline.** The two extractors differ enough that a cross-method diff reports extraction artifacts as competitor changes. When the primary path changes, re-capture the whole set.

---

## Branding

`brand/brand.json` is the single source of truth. Nothing else in the repo contains a hex code, font name, or logo path — if the renderer needs a value that is missing there, that is a bug in `brand.json`, not licence to invent one.

`validate_brand.py` does not trust the file. Contrast ratios are **recomputed** from the hex codes rather than read from the stored numbers, and font embedding permission is read from the OS/2 table rather than from an `embeddable: true` flag. It was tested against nine injected defects — missing assets, raw hex in tokens, non-grey colours under a monochrome rule, a deliberately *lying* contrast ratio — and catches all nine.

The bundled example brand is strictly monochrome. Where that genuinely conflicted with a chart needing to separate several series, the resolution was a documented, user-approved extension recorded in `brand.json` with its own scope and restrictions — not a silently-added colour.

---

## Cost

The pipeline needs no paid API to run. Discovery uses built-in web search, and Playwright renders locally. Firecrawl is used because it captures materially more, and its free tier covers roughly 35 monthly runs at ~14 pages each; without a key the pipeline falls back to Playwright and still works.

The main lever on model cost is hash-gating: unchanged pages are never re-analysed, so recurring runs cost a fraction of the baseline.

---

## Repository layout

```
brand/       Brand assets + brand.json (single source of truth for all styling)
profile/     The business profile competitor work is compared against
research/    DURABLE. Competitor set, dated snapshots, analyses, reports, changelog.
tools/       Deterministic Python — one script, one job
workflows/   Markdown SOPs
```

`research/` is deliberately git-tracked. Month-over-month diffing needs a real baseline, and re-fetching cannot recover what a page said last month — git history *is* the tracking mechanism.

Snapshot `.html` and `.png` payloads are gitignored: `diff_snapshots.py` reads only the extracted `.md` and the hash in `.json`, so the pipeline and the evidence trail stay intact while the repo avoids carrying 40MB of competitors' full pages.

---

## Notes on the bundled example

The included run analyses seven real companies in AI lead generation and outbound, captured 2026-08-12. Those competitor figures are real and sourced.

**DOZ.AI itself is a demonstration company.** Its pricing tiers, credit unit and feature set are fictional, invented to exercise the pipeline end to end, and are flagged as such wherever they surface in the report.

The analysis was also revised twice as its inputs were corrected — a per-seat pricing assumption, then an Australian-market assumption, both withdrawn. Both reversals are recorded in the report's own limitations section rather than quietly deleted, on the principle that a reader should be able to see which conclusions moved and why.
