"""Capture a deterministic snapshot of one competitor page.

Month-over-month change detection is only meaningful if every capture is taken
the same way, so this tool is the single entry point for fetching. It writes
four artefacts per page:

    <page-type>.html   raw HTML exactly as delivered
    <page-type>.md     extracted main content, normalised for diffing
    <page-type>.png    full-page screenshot
    <page-type>.json   metadata incl. SHA-256 of the extracted text

The SHA-256 is what makes monthly runs cheap: if the hash is unchanged, the
page did not change and the analysis step can skip it entirely.

Determinism matters more than completeness here. Boilerplate that changes on
every load (cookie banners, rotating testimonials, injected timestamps, CSRF
tokens) is stripped before hashing — otherwise every page would look "changed"
every month and the diff would be worthless.

Fetch path: Firecrawl is primary (set FIRECRAWL_API_KEY in .env), with local
Playwright as an automatic fallback whenever Firecrawl fails, its quota is
exhausted, or no key is configured. The run therefore degrades rather than
stopping. `meta.json` records which path produced each snapshot in `fetched_via`,
because the two extract slightly differently and a silent switch would otherwise
show up as spurious "changes" in the next month's diff.

Usage:
    uv run tools/fetch_page_snapshot.py --url https://acme.com/pricing \\
        --competitor acme-corp --page-type pricing
    uv run tools/fetch_page_snapshot.py --url ... --no-firecrawl   # local only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from selectolax.parser import HTMLParser

PAGE_TYPES = ["home", "pricing", "product", "features", "about", "blog", "customers", "other"]

# Elements that never carry competitive signal, or that change per-load.
# `header`/`footer` are handled separately: they are only stripped at page level.
# A <header> nested inside a pricing card usually holds the plan name, so
# blanket-stripping them loses exactly the data this tool exists to capture.
STRIP_TAGS = [
    "script", "style", "noscript", "svg", "iframe", "canvas", "template", "nav",
]
PAGE_LEVEL_CHROME = ["header", "footer"]

# Class/id fragments for chrome that varies per load and would poison diffs.
STRIP_PATTERNS = re.compile(
    r"cookie|consent|gdpr|banner|popup|modal|newsletter|chat-widget|intercom|"
    r"drift|hubspot-messages|announce|carousel|marquee|ticker|social-proof",
    re.I,
)

# Volatile substrings normalised out of the extracted text before hashing.
VOLATILE_PATTERNS = [
    (re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", re.I), "<TIME>"),
    (re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"), "<DATE>"),
    (re.compile(r"\b\d+\s+(second|minute|hour|day|week|month)s?\s+ago\b", re.I), "<RELTIME>"),
    (re.compile(r"\bcsrf[-_]?token[\"'=:\s]+[A-Za-z0-9+/=_-]{8,}", re.I), "<CSRF>"),
]

HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}

# Tags whose text is never content on its own.
SKIP_LEAF_TAGS = {"option", "script", "style", "title", "head", "html", "body"}

# Currency and billing-cadence signals. Used to confirm a pricing page actually
# yielded pricing, and surfaced in metadata so a silent extraction failure on
# the most important page type is caught at fetch time rather than in analysis.
PRICE_RE = re.compile(
    r"(?:[$€£¥]\s?\d[\d,]*(?:\.\d{2})?)"
    r"|(?:\b\d[\d,]*(?:\.\d{2})?\s?(?:USD|EUR|GBP|AUD|CAD)\b)"
    r"|(?:\bper\s+(?:user|seat|month|year|mo|yr)\b)"
    r"|(?:/\s?(?:mo|month|yr|year|seat|user)\b)"
    r"|(?:\bfree\b|\bcustom pricing\b|\bcontact sales\b)",
    re.I,
)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def strip_noise(tree: HTMLParser) -> None:
    for tag in STRIP_TAGS:
        for node in tree.css(tag):
            node.decompose()

    # Page-level header/footer only — nested ones may hold plan names.
    for tag in PAGE_LEVEL_CHROME:
        for node in tree.css(tag):
            parent = node.parent
            if parent is not None and parent.tag in ("body", "html"):
                node.decompose()

    for node in tree.css("[class], [id]"):
        ident = f"{node.attributes.get('class', '')} {node.attributes.get('id', '')}"
        if STRIP_PATTERNS.search(ident):
            node.decompose()
    for node in tree.css('[aria-hidden="true"], [hidden]'):
        node.decompose()


def _is_text_leaf(node) -> bool:
    """True when no descendant element carries text of its own.

    Emitting only leaves is what prevents a container's text being repeated
    once for every ancestor, while still reaching text in <div>/<span> — which
    is where most modern pricing tables put plan names and prices.
    """
    for child in node.iter(include_text=False):
        if child.text(deep=True, separator=" ").strip():
            return False
    return True


def extract_markdown(html: str) -> str:
    """Extract main content as lightly-structured markdown.

    Headings keep their level because a competitor changing an H1 is exactly
    the positioning shift worth flagging. Everything else is emitted as a flat
    block in document order, so cosmetic markup changes do not register as
    content changes.
    """
    tree = HTMLParser(html)
    strip_noise(tree)

    root = None
    for selector in ("main", "article", '[role="main"]', "#main", "#content", ".content"):
        found = tree.css_first(selector)
        if found is not None:
            root = found
            break
    if root is None:
        root = tree.body or tree.root
    if root is None:
        return ""

    lines: list[str] = []
    seen: set[str] = set()

    for node in root.traverse(include_text=False):
        tag = node.tag
        if tag in SKIP_LEAF_TAGS:
            continue

        if tag in HEADING_TAGS:
            text = " ".join(node.text(deep=True, separator=" ").split())
            if text and text not in seen:
                lines.append(f"{HEADING_TAGS[tag]} {text}")
                seen.add(text)
            continue

        if not _is_text_leaf(node):
            continue

        text = " ".join(node.text(deep=True, separator=" ").split())
        if len(text) < 2 or text in seen:
            continue
        lines.append(f"- {text}" if tag == "li" else text)
        seen.add(text)

    return "\n\n".join(lines).strip()


def find_price_signals(text: str, limit: int = 40) -> list[str]:
    """Distinct price-shaped strings found in the extracted text."""
    found: list[str] = []
    for match in PRICE_RE.finditer(text):
        value = match.group(0).strip()
        if value.lower() not in {f.lower() for f in found}:
            found.append(value)
        if len(found) >= limit:
            break
    return found


def clean_markdown(text: str) -> str:
    """Strip markdown noise that carries no competitive signal.

    Firecrawl returns real markdown, so image embeds and link targets come
    through verbatim. Asset URLs on most marketing sites carry cache-busting
    hashes that change between fetches, which would mark every page as changed
    every month. Link *text* is kept — the words in a CTA matter; the href does
    not.
    """
    # ![alt](url) -> drop entirely
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    # [text](url) -> text
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # escaped line continuations Firecrawl emits inside link blocks
    text = text.replace("\\\n", "\n")
    # stray reference-style leftovers and empty brackets
    text = re.sub(r"\[\s*\]", "", text)
    # collapse the whitespace the removals leave behind
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def normalise_for_hash(text: str) -> str:
    """Remove per-load volatility so the hash reflects real content change."""
    normalised = text
    for pattern, replacement in VOLATILE_PATTERNS:
        normalised = pattern.sub(replacement, normalised)
    normalised = re.sub(r"[ \t]+", " ", normalised)
    normalised = re.sub(r"\n{3,}", "\n\n", normalised)
    return normalised.strip()


def capture(url: str, out_dir: Path, page_type: str, timeout_ms: int, full_page: bool) -> dict:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            # A stable, honest UA. Not spoofing a specific person's browser —
            # just identifying consistently so results are reproducible.
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 "
                "DOZ.AI-competitor-research"
            ),
            locale="en-US",
            timezone_id="UTC",
            # Freeze animations so screenshots are comparable between runs.
            reduced_motion="reduce",
        )
        page = context.new_page()

        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeout:
            browser.close()
            raise RuntimeError(f"timed out after {timeout_ms}ms loading {url}") from None
        except PlaywrightError as exc:
            browser.close()
            raise RuntimeError(f"navigation failed for {url}: {exc}") from None

        status = response.status if response else None
        final_url = page.url

        # Best-effort settle; many marketing sites keep long-poll connections
        # open and never reach networkidle, so a failure here is not fatal.
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout:
            log("  note: networkidle not reached within 8s — continuing")

        title = page.title()
        html = page.content()

        screenshot_path = out_dir / f"{page_type}.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=full_page)
        except PlaywrightError as exc:
            log(f"  warning: screenshot failed ({exc}); continuing without it")
            screenshot_path = None

        browser.close()

    return {
        "status": status,
        "final_url": final_url,
        "title": title,
        "html": html,
        "screenshot": screenshot_path,
    }


MIN_USEFUL_CHARS = 400  # below this, treat an extraction as having failed

# Firecrawl API version — pinned deliberately, not inherited.
#
# v2 exists and is live; both were probed on 2026-08-14 and return the same
# top-level shape (success/data/markdown/metadata). We stay on v1 because the
# entire 2026-08 baseline was captured with it, and the cardinal rule of this
# pipeline is that a baseline is never fetched two different ways — a version
# bump that extracts even slightly differently would surface as competitor
# "changes" that never happened.
#
# To migrate: bump this, re-capture the WHOLE set in one go, and note the switch
# in the changelog. The one known request-shape change is the screenshot format —
# v1 takes the string "screenshot@fullPage" inside `formats`, v2 takes an object
# {"type": "screenshot", "fullPage": true}. Verify against one page before
# committing the run.
FIRECRAWL_API_VERSION = "v1"


def firecrawl_key() -> str | None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    import os
    return os.getenv("FIRECRAWL_API_KEY")


def capture_firecrawl(url: str, out_dir: Path, page_type: str) -> dict | None:
    """PRIMARY fetch path. Returns html/markdown/screenshot, or None on failure.

    Firecrawl handles its own rendering, proxying and anti-bot evasion, which is
    why it is the primary: it survives sites that block a plain headless browser.
    Playwright remains installed and is used automatically whenever this path
    fails, so a quota exhaustion or outage degrades the run rather than stopping it.

    Quota note: at ~14 pages per monthly run, a 500-credit free tier is roughly
    35 runs' worth — comfortable. It only becomes a constraint if the competitor
    set or page count grows substantially.
    """
    key = firecrawl_key()
    if not key:
        return None

    log("  fetching via Firecrawl…")
    try:
        import httpx

        response = httpx.post(
            f"https://api.firecrawl.dev/{FIRECRAWL_API_VERSION}/scrape",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "url": url,
                "formats": ["markdown", "html", "screenshot@fullPage"],
                # Main content only. With this off, Smartlead's pricing page
                # returned 52k chars against 6k of actual content — the rest was
                # mega-menu markup and CDN image URLs. Those URLs carry cache-
                # busting hashes that change between fetches, so including them
                # would flag every page as "changed" every month.
                "onlyMainContent": True,
                "excludeTags": ["nav", "footer", "script", "style", "noscript", "iframe"],
                "waitFor": 2500,
                "timeout": 60000,
            },
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001 — surface the real transport error
        log(f"  Firecrawl request failed: {exc}")
        return None

    if response.status_code == 402:
        log("  Firecrawl quota exhausted (HTTP 402) — falling back to Playwright")
        return None
    if response.status_code == 401:
        log("  Firecrawl rejected the API key (HTTP 401) — check FIRECRAWL_API_KEY in .env")
        return None
    if response.status_code != 200:
        log(f"  Firecrawl returned HTTP {response.status_code}: {response.text[:180]}")
        return None

    payload = response.json().get("data", {})
    markdown = clean_markdown(payload.get("markdown") or "")
    if len(markdown.strip()) < MIN_USEFUL_CHARS:
        log(f"  Firecrawl returned only {len(markdown.strip())} chars — falling back")
        return None

    meta = payload.get("metadata", {}) or {}
    screenshot_path = None
    shot_url = payload.get("screenshot")
    if shot_url:
        try:
            import httpx
            img = httpx.get(shot_url, timeout=60)
            if img.status_code == 200:
                screenshot_path = out_dir / f"{page_type}.png"
                screenshot_path.write_bytes(img.content)
        except Exception as exc:  # noqa: BLE001
            log(f"  screenshot download failed: {exc}")

    log(f"  Firecrawl OK ({len(markdown)} chars)")
    return {
        "status": meta.get("statusCode", 200),
        "final_url": meta.get("sourceURL") or meta.get("url") or url,
        "title": meta.get("title"),
        "html": payload.get("html") or "",
        "markdown": markdown,
        "screenshot": screenshot_path,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="page URL to snapshot")
    ap.add_argument("--competitor", required=True, help="competitor slug, e.g. acme-corp")
    ap.add_argument("--page-type", required=True, choices=PAGE_TYPES, help="what kind of page this is")
    ap.add_argument("--date", default=None, help="snapshot date YYYY-MM-DD (default: today)")
    ap.add_argument("--out-root", default="research/snapshots", help="snapshot root directory")
    ap.add_argument("--timeout", type=int, default=45000, help="Playwright navigation timeout in ms")
    ap.add_argument("--no-full-page", action="store_true", help="capture viewport only, not full page")
    ap.add_argument("--no-firecrawl", action="store_true",
                    help="skip Firecrawl and use local Playwright only (no API credits consumed)")
    args = ap.parse_args()

    snapshot_date = args.date or date.today().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", snapshot_date):
        log(f"ERROR: --date must be YYYY-MM-DD, got {snapshot_date!r}")
        return 1

    competitor = slugify(args.competitor)
    out_dir = Path(args.out_root) / snapshot_date / competitor
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"fetching {args.url}")
    log(f"  -> {out_dir} [{args.page_type}]")

    # Firecrawl is the primary path; Playwright is the automatic fallback, so an
    # expired key, exhausted quota or outage degrades the run instead of stopping it.
    result = None
    fetched_via = None
    markdown = ""
    html = ""
    status = None

    if not args.no_firecrawl:
        fc = capture_firecrawl(args.url, out_dir, args.page_type)
        if fc:
            result = fc
            fetched_via = "firecrawl"
            html = fc["html"]
            markdown = fc["markdown"]
            status = fc["status"]
    elif firecrawl_key():
        log("  --no-firecrawl given; using Playwright")

    if result is None:
        if not args.no_firecrawl and firecrawl_key():
            log("  falling back to Playwright")
        elif not firecrawl_key() and not args.no_firecrawl:
            log("  no FIRECRAWL_API_KEY set — using Playwright")
        try:
            result = capture(args.url, out_dir, args.page_type, args.timeout, not args.no_full_page)
            fetched_via = "playwright"
            html = result["html"]
            status = result["status"]
            markdown = extract_markdown(html)
        except RuntimeError as exc:
            log(f"ERROR: {exc}")
            return 1

    if status is None or status >= 400:
        log(f"ERROR: HTTP {status} for {args.url} — refusing to save a failed fetch as a snapshot")
        return 1

    if len(markdown.strip()) < MIN_USEFUL_CHARS:
        log(f"ERROR: only {len(markdown.strip())} chars extracted from {args.url}")
        log("       Page is likely JS-gated behind interaction. Inspect the saved .png.")
        return 1

    (out_dir / f"{args.page_type}.html").write_text(html, encoding="utf-8")

    if not markdown.strip():
        log("ERROR: extracted no readable content — the page is likely JS-gated or blocked")
        return 1
    (out_dir / f"{args.page_type}.md").write_text(markdown, encoding="utf-8")

    normalised = normalise_for_hash(markdown)
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    price_signals = find_price_signals(markdown)
    if args.page_type == "pricing" and not price_signals:
        # Extraction "succeeded" but produced no prices — almost always means the
        # pricing table is behind JS or an interaction. Better to fail here than
        # let the analysis silently report that a competitor has no pricing.
        log("ERROR: pricing page yielded no price signals — extraction likely incomplete.")
        log("       Inspect the saved .html/.png, then adjust the extractor or mark")
        log("       this competitor's pricing as gated in competitors.json.")
        return 2

    meta = {
        "competitor": competitor,
        "page_type": args.page_type,
        "requested_url": args.url,
        "final_url": result["final_url"] if result else args.url,
        "redirected": bool(result) and result["final_url"].rstrip("/") != args.url.rstrip("/"),
        "http_status": status,
        "title": result["title"] if result else None,
        "fetched_via": fetched_via,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_date": snapshot_date,
        "content_sha256": digest,
        "extracted_chars": len(markdown),
        "price_signals": price_signals,
        "screenshot": result["screenshot"].name if result["screenshot"] else None,
        "extractor_version": 2,
    }
    (out_dir / f"{args.page_type}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    log(f"  status {status} | {len(markdown)} chars | sha256 {digest[:12]}…")
    if price_signals:
        log(f"  price signals ({len(price_signals)}): {', '.join(price_signals[:8])}")
    if meta["redirected"]:
        log(f"  note: redirected to {result['final_url']}")

    print(out_dir / f"{args.page_type}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
