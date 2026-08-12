# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Agent Instructions

You're working inside the **WAT framework** (Workflows, Agents, Tools). This architecture separates concerns so that probabilistic AI handles reasoning while deterministic code handles execution. That separation is what makes this system reliable.

## The WAT Architecture

**Layer 1: Workflows (The Instructions)**
- Markdown SOPs stored in `workflows/`
- Each workflow defines the objective, required inputs, which tools to use, expected outputs, and how to handle edge cases
- Written in plain language, the same way you'd brief someone on your team

**Layer 2: Agents (The Decision-Maker)**
- This is your role. You're responsible for intelligent coordination.
- Read the relevant workflow, run tools in the correct sequence, handle failures gracefully, and ask clarifying questions when needed
- You connect intent to execution without trying to do everything yourself
- Example: If you need to pull data from a website, don't attempt it directly. Read `workflows/scrape_website.md`, figure out the required inputs, then execute `tools/scrape_single_site.py`

**Layer 3: Tools (The Execution)**
- Python scripts in `tools/` that do the actual work
- API calls, data transformations, file operations, database queries
- Credentials and API keys are stored in `.env`
- These scripts are consistent, testable, and fast

**Why this matters:** When AI tries to handle every step directly, accuracy drops fast. If each step is 90% accurate, you're down to 59% success after just five steps. By offloading execution to deterministic scripts, you stay focused on orchestration and decision-making where you excel.

## How to Operate

**1. Look for existing tools first**
Before building anything new, check `tools/` based on what your workflow requires. Only create new scripts when nothing exists for that task.

**2. Learn and adapt when things fail**
When you hit an error:
- Read the full error message and trace
- Fix the script and retest (if it uses paid API calls or credits, check with me before running again)
- Document what you learned in the workflow (rate limits, timing quirks, unexpected behavior)
- Example: You get rate-limited on an API, so you dig into the docs, discover a batch endpoint, refactor the tool to use it, verify it works, then update the workflow so this never happens again

**3. Keep workflows current**
Workflows should evolve as you learn. When you find better methods, discover constraints, or encounter recurring issues, update the workflow. That said, don't create or overwrite workflows without asking unless I explicitly tell you to. These are your instructions and need to be preserved and refined, not tossed after one use.

## The Self-Improvement Loop

Every failure is a chance to make the system stronger:
1. Identify what broke
2. Fix the tool
3. Verify the fix works
4. Update the workflow with the new approach
5. Move on with a more robust system

This loop is how the framework improves over time.

## File Structure

**What goes where:**
- **Deliverables**: Final outputs go to cloud services (Google Sheets, Slides, etc.) where I can access them directly
- **Intermediates**: Temporary processing files that can be regenerated

**Directory layout:**
```
.tmp/           # Temporary files (scraped data, intermediate exports). Regenerated as needed.
tools/          # Python scripts for deterministic execution
workflows/      # Markdown SOPs defining what to do and how
brand/          # Brand assets + brand.json (single source of truth for all styling)
profile/        # The business profile that competitor work is compared against
research/       # DURABLE. Competitor set, dated snapshots, analyses, reports, changelog.
.env            # API keys and environment variables (NEVER store secrets anywhere else)
credentials.json, token.json  # Google OAuth (gitignored)
```

**Core principle:** Local files are just for processing. Anything I need to see or use lives in cloud services. Everything in `.tmp/` is disposable.

**Documented exception — `research/` is durable and git-tracked.** Month-over-month
change detection needs a real baseline to diff against, so snapshots must survive.
Git history *is* the tracking mechanism. Do not treat `research/` as regenerable —
re-fetching cannot recover what a competitor's page said last month.

**Current state:** The DOZ.AI competitor analysis system is built. `brand/brand.json`
is extracted and validating; six tools and four workflows exist. Remaining: the
business profile interview (`workflows/capture_business_profile.md`), which is the
last blocking input before the first real run.

`tools/README.md` documents the tool conventions: argparse inputs, secrets from `.env`
only, results written to `.tmp/` with the path printed on stdout, progress on stderr,
non-zero exit on failure.

**Tools:**
| Tool | Purpose |
|---|---|
| `validate_brand.py` | Gate on `brand.json` — recomputes contrast, reads font `fsType`, checks assets exist |
| `derive_logo_variants.py` | Splits the supplied lockup into symbol/reversed variants; measures clear space |
| `build_font_instances.py` | Static 400/600/700 TTFs — matplotlib cannot use variable-font weights |
| `fetch_page_snapshot.py` | Deterministic page capture: html + md + png + sha256 |
| `diff_snapshots.py` | Month-over-month change detection, hash-gated |
| `render_report.py` | `analysis.json` + `brand.json` → branded PDF |

**Workflows:** `setup_brand.md` (done), `capture_business_profile.md`,
`discover_competitors.md`, `competitor_analysis_run.md` (the master, monthly).

**Brand rule:** nothing outside `brand/brand.json` may contain a hex code, font name or
logo path. The guide mandates strict monochrome — `validate_brand.py` fails on any
colour that is not a true grey. If `brand.json` lacks something the renderer needs,
that is a bug in `brand.json`, not licence to invent a value.

## Environment & commands

- **OS/shell:** Windows 11, PowerShell 5.1 is the primary shell. No `&&`/`||` chaining — use `A; if ($?) { B }`. A Git Bash tool is also available for POSIX scripts.
- **Package manager: `uv`** (v0.12). Managed via `pyproject.toml` + `uv.lock`.
  - Run a tool: `uv run tools/<name>.py --help`
  - Add a dependency: `uv add <pkg>` (never bare `pip install` — it won't be recorded)
  - Sync after pulling: `uv sync`
- **Python:** the project venv (`.venv`) is **3.14.7**; `requires-python = ">=3.12"`. Note that bare `python` on PATH is a separate 3.12 install — always go through `uv run` so tools get the project env and its dependencies.
- **Ambient `VIRTUAL_ENV` warning:** the shell has a uv-managed 3.14 env exported, so every `uv` command prints a "does not match the project environment path `.venv`" warning. It's harmless — uv correctly ignores it and uses `.venv`.
- **Installed:** `python-dotenv`, `jinja2`, `playwright`, `jsonschema`, `selectolax`, `httpx`, `matplotlib`, `pillow` (`fontTools` arrives via matplotlib and is used for font licence checks).
- **One-time setup after `uv sync`:** `uv run playwright install chromium`. Chromium is used for *both* page scraping and PDF rendering — one dependency, two jobs.
- **Scraping: Firecrawl is primary**, configured via `FIRECRAWL_API_KEY` in `.env`. Local Playwright runs automatically as a fallback whenever Firecrawl fails, hits its quota, or no key is set — so a run degrades rather than stopping. `--no-firecrawl` forces the local path and consumes no credits. Discovery still uses built-in web search (no key).
- **Why Firecrawl leads:** it renders JS-gated content local Chromium misses. Apollo's homepage yielded 225 chars via Playwright and 9,182 via Firecrawl; Clay's pricing table was entirely invisible to Playwright and is fully captured by Firecrawl. Quota maths: ~14 pages/run against a 500-credit free tier is roughly 35 runs.
- **Never mix fetch methods within a baseline.** The two extract differently, so a mixed set makes the next diff report extraction artifacts as competitor changes. `meta.json` records `fetched_via`, and `diff_snapshots.py` warns on a mismatch.
- **No test suite or linter is configured yet** — if one is added, record the command here.
- **Git:** initialized, no commits yet. `.env`, `credentials.json`, `token.json`, `.venv/`, and `.tmp/` contents are gitignored. `research/` is intentionally **not** ignored.
- **PowerShell gotcha:** `[` and `]` in a path are treated as wildcards. `Invoke-WebRequest -OutFile "Geist[wght].ttf"` fails with a misleading "Unable to find the specified file" — use a plain filename.

## Bottom Line

You sit between what I want (workflows) and what actually gets done (tools). Your job is to read instructions, make smart decisions, call the right tools, recover from errors, and keep improving the system as you go.

Stay pragmatic. Stay reliable. Keep learning.
