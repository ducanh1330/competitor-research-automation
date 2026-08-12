"""Compare two snapshot dates for a competitor and report what changed.

This is the engine behind the report's "what changed since last month" section.
It is deliberately deterministic: the AI analysis step reads this output rather
than re-reading both months of raw pages, so month-over-month claims are
grounded in an actual computed diff instead of recollection.

Pages whose content hash matches are reported as unchanged and skipped without
generating a diff — that hash gate is what keeps recurring runs cheap.

Usage:
    uv run tools/diff_snapshots.py --competitor acme-corp --latest-two
    uv run tools/diff_snapshots.py --competitor acme-corp --from 2026-07-01 --to 2026-08-01
    uv run tools/diff_snapshots.py --all --latest-two
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CONTEXT_LINES = 2
MAX_DIFF_LINES = 400  # per page, to keep the report readable

PRICE_RE = re.compile(
    r"(?:[$€£¥]\s?\d[\d,]*(?:\.\d{2})?)"
    r"|(?:\b\d[\d,]*(?:\.\d{2})?\s?(?:USD|EUR|GBP|AUD|CAD)\b)",
    re.I,
)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def available_dates(root: Path, competitor: str) -> list[str]:
    """Snapshot dates that contain this competitor, oldest first."""
    if not root.exists():
        return []
    dates = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / competitor).is_dir():
            dates.append(child.name)
    return dates


def all_competitors(root: Path) -> list[str]:
    names: set[str] = set()
    if root.exists():
        for date_dir in root.iterdir():
            if date_dir.is_dir():
                for comp in date_dir.iterdir():
                    if comp.is_dir():
                        names.add(comp.name)
    return sorted(names)


def load_pages(root: Path, snapshot_date: str, competitor: str) -> dict[str, dict]:
    """{page_type: {meta, text}} for one snapshot."""
    base = root / snapshot_date / competitor
    pages: dict[str, dict] = {}
    if not base.is_dir():
        return pages
    for meta_path in sorted(base.glob("*.json")):
        page_type = meta_path.stem
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log(f"  warning: unreadable metadata {meta_path}: {exc}")
            continue
        md_path = base / f"{page_type}.md"
        text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        pages[page_type] = {"meta": meta, "text": text}
    return pages


def diff_page(old_text: str, new_text: str) -> tuple[list[str], int, int]:
    """Unified diff plus added/removed line counts."""
    old_lines = [ln for ln in old_text.splitlines() if ln.strip()]
    new_lines = [ln for ln in new_text.splitlines() if ln.strip()]
    diff = list(
        difflib.unified_diff(
            old_lines, new_lines, lineterm="", n=CONTEXT_LINES, fromfile="before", tofile="after"
        )
    )
    added = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"... diff truncated at {MAX_DIFF_LINES} lines ..."]
    return diff, added, removed


def price_delta(old_meta: dict, new_meta: dict) -> dict | None:
    """Price signals that appeared or disappeared.

    Surfaced separately because a pricing change is the single most
    decision-relevant thing a competitor can do, and it is easy to lose inside
    a long text diff.
    """
    old = {s for s in old_meta.get("price_signals", []) if PRICE_RE.search(s)}
    new = {s for s in new_meta.get("price_signals", []) if PRICE_RE.search(s)}
    if old == new:
        return None
    return {
        "added": sorted(new - old),
        "removed": sorted(old - new),
    }


def compare(root: Path, competitor: str, date_from: str, date_to: str) -> dict:
    old_pages = load_pages(root, date_from, competitor)
    new_pages = load_pages(root, date_to, competitor)

    page_types = sorted(set(old_pages) | set(new_pages))
    results = []
    changed = unchanged = added_pages = removed_pages = 0

    for page_type in page_types:
        old = old_pages.get(page_type)
        new = new_pages.get(page_type)

        if old is None:
            added_pages += 1
            results.append({
                "page_type": page_type,
                "status": "added",
                "note": f"not captured on {date_from}; first seen {date_to}",
                "url": new["meta"].get("final_url"),
            })
            continue

        if new is None:
            removed_pages += 1
            results.append({
                "page_type": page_type,
                "status": "removed",
                "note": f"captured on {date_from} but missing on {date_to}",
                "url": old["meta"].get("final_url"),
            })
            continue

        # A snapshot taken via Firecrawl and one taken via Playwright extract
        # differently, so comparing across them shows wholesale "changes" that
        # are really just a tooling switch. Flag it rather than let the analysis
        # report a competitor rewriting a page they never touched.
        old_via = old["meta"].get("fetched_via")
        new_via = new["meta"].get("fetched_via")
        via_mismatch = bool(old_via and new_via and old_via != new_via)

        old_hash = old["meta"].get("content_sha256")
        new_hash = new["meta"].get("content_sha256")

        if old_hash and new_hash and old_hash == new_hash:
            unchanged += 1
            results.append({
                "page_type": page_type,
                "status": "unchanged",
                "content_sha256": new_hash,
                "url": new["meta"].get("final_url"),
            })
            continue

        diff, n_added, n_removed = diff_page(old["text"], new["text"])
        if not diff:
            # Hashes differ but normalised text does not — volatile content only.
            unchanged += 1
            results.append({
                "page_type": page_type,
                "status": "unchanged",
                "note": "hash differs but no textual change (volatile markup only)",
                "url": new["meta"].get("final_url"),
            })
            continue

        changed += 1
        entry = {
            "page_type": page_type,
            "status": "changed",
            "url": new["meta"].get("final_url"),
            "lines_added": n_added,
            "lines_removed": n_removed,
            "old_sha256": old_hash,
            "new_sha256": new_hash,
            "diff": diff,
        }
        prices = price_delta(old["meta"], new["meta"])
        if prices:
            entry["price_change"] = prices
        if via_mismatch:
            entry["fetch_method_changed"] = {"from": old_via, "to": new_via}
            entry["warning"] = (
                f"Snapshots were captured by different tools ({old_via} then {new_via}). "
                f"Much of this diff is likely an extraction difference, not a real change. "
                f"Re-capture the earlier date with the same tool before trusting it."
            )
        results.append(entry)

    return {
        "competitor": competitor,
        "from_date": date_from,
        "to_date": date_to,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "pages_compared": len(page_types),
            "changed": changed,
            "unchanged": unchanged,
            "added": added_pages,
            "removed": removed_pages,
            "has_price_change": any("price_change" in r for r in results),
        },
        "pages": results,
    }


def render_summary(report: dict) -> str:
    lines = [
        f"{report['competitor']}: {report['from_date']} -> {report['to_date']}",
    ]
    s = report["summary"]
    lines.append(
        f"  {s['changed']} changed, {s['unchanged']} unchanged, "
        f"{s['added']} added, {s['removed']} removed"
    )
    for page in report["pages"]:
        if page["status"] == "changed":
            bits = f"+{page['lines_added']}/-{page['lines_removed']}"
            lines.append(f"  CHANGED   {page['page_type']:10s} {bits}")
            if page.get("fetch_method_changed"):
                fm = page["fetch_method_changed"]
                lines.append(
                    f"            !! fetch method changed ({fm['from']} -> {fm['to']}) "
                    f"— diff is unreliable"
                )
            if "price_change" in page:
                pc = page["price_change"]
                if pc["added"]:
                    lines.append(f"            PRICE + {', '.join(pc['added'][:8])}")
                if pc["removed"]:
                    lines.append(f"            PRICE - {', '.join(pc['removed'][:8])}")
        elif page["status"] in ("added", "removed"):
            lines.append(f"  {page['status'].upper():9s} {page['page_type']}")
    if s["changed"] == 0 and s["added"] == 0 and s["removed"] == 0:
        lines.append("  no changes detected")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--competitor", help="competitor slug; omit with --all")
    ap.add_argument("--all", action="store_true", help="compare every competitor found")
    ap.add_argument("--from", dest="date_from", help="earlier snapshot date YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="later snapshot date YYYY-MM-DD")
    ap.add_argument("--latest-two", action="store_true", help="use the two most recent snapshots")
    ap.add_argument("--snapshots", default="research/snapshots", help="snapshot root")
    ap.add_argument("--out", default=".tmp/diff", help="output directory for diff JSON")
    args = ap.parse_args()

    root = Path(args.snapshots)

    if not args.competitor and not args.all:
        log("ERROR: give --competitor <slug> or --all")
        return 1
    if not args.latest_two and not (args.date_from and args.date_to):
        log("ERROR: give --latest-two, or both --from and --to")
        return 1

    targets = all_competitors(root) if args.all else [args.competitor]
    if not targets:
        log(f"ERROR: no competitors found under {root}")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    any_change = False

    for competitor in targets:
        dates = available_dates(root, competitor)
        if args.latest_two:
            if len(dates) < 2:
                log(f"{competitor}: only {len(dates)} snapshot(s) — nothing to diff (baseline run)")
                continue
            date_from, date_to = dates[-2], dates[-1]
        else:
            date_from, date_to = args.date_from, args.date_to
            for d in (date_from, date_to):
                if d not in dates:
                    log(f"ERROR: {competitor} has no snapshot for {d}. Available: {', '.join(dates) or 'none'}")
                    return 1

        report = compare(root, competitor, date_from, date_to)
        log(render_summary(report))

        if report["summary"]["changed"] or report["summary"]["added"] or report["summary"]["removed"]:
            any_change = True

        path = out_dir / f"{competitor}_{date_from}_to_{date_to}.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        written.append(path)

    if not written:
        log("no comparisons produced")
        return 0

    log(f"\n{len(written)} diff file(s) written; changes detected: {any_change}")
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
