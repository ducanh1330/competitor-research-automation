"""Render analysis.json into the branded DOZ.AI PDF report.

Pipeline: analysis.json + brand.json -> Jinja2 -> HTML -> Chromium -> PDF.

Design rule enforced throughout: this script never invents a brand value.
Colours, sizes, families and logo paths all come from brand.json and are
injected as CSS custom properties. If brand.json is missing something the
template needs, that is a bug in brand.json and this tool fails rather than
substituting a default.

Fonts and images are embedded as data URIs rather than referenced by path.
That guarantees Chromium can load them regardless of where the repo lives —
this project's path contains spaces and non-ASCII characters, which breaks
file:// references — and guarantees the faces are embedded in the PDF.

Usage:
    uv run tools/render_report.py --data research/analysis/2026-08/analysis.json \\
        --out research/reports/2026-08/report.pdf
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
import tempfile
from datetime import datetime
from pathlib import Path

MAX_DIFF_HIGHLIGHTS = 6  # diff lines shown per page in the changes section


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------- #
# brand plumbing
# --------------------------------------------------------------------------- #

def load_brand(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"ERROR: brand file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_colors(brand: dict) -> dict[str, str]:
    """Map semantic token names to hex, via color.tokens -> color.palette."""
    palette = brand["color"]["palette"]
    by_name: dict[str, str] = {}
    for name, entry in palette.items():
        by_name[name] = entry["hex"]
        for alias in entry.get("aliases", []):
            by_name[alias] = entry["hex"]

    resolved: dict[str, str] = {}
    for token, target in brand["color"]["tokens"].items():
        if token.startswith("_"):
            continue
        if target not in by_name:
            raise SystemExit(
                f"ERROR: brand.json token {token!r} references unknown palette entry "
                f"{target!r}. Fix brand.json — this tool will not guess a colour."
            )
        resolved[token] = by_name[target]
    return resolved


def data_uri(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"ERROR: asset referenced by brand.json is missing: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def font_face_css(brand: dict, root: Path) -> str:
    """@font-face blocks with the TTFs inlined as data URIs."""
    blocks = []
    for family in brand["typography"]["families"].values():
        path = root / family["file"]
        if not path.exists():
            raise SystemExit(f"ERROR: font missing: {path}")
        uri = data_uri(path)
        axis = family.get("variable_axis")
        weight = f"{axis['min']} {axis['max']}" if axis else "400"
        blocks.append(
            f"@font-face{{font-family:'{family['name']}';"
            f"src:url({uri}) format('truetype-variations');"
            f"font-weight:{weight};font-style:normal;font-display:block;}}"
        )
    return "".join(blocks)


def css_variables(brand: dict, colors: dict[str, str]) -> str:
    scale = brand["typography"]["scale"]
    families = brand["typography"]["families"]

    lines = [f"--dozai-{k.replace('_', '-')}:{v};" for k, v in colors.items()]
    lines.append(f"--dozai-font-sans:{families['sans']['css_stack']};")
    lines.append(f"--dozai-font-mono:{families['mono']['css_stack']};")

    for key, entry in scale.items():
        prefix = f"--dozai-{key}"
        lines.append(f"{prefix}-size:{entry['size_px']}px;")
        lines.append(f"{prefix}-weight:{entry['weight']};")
        lines.append(f"{prefix}-line-height:{entry['line_height']};")
        if entry.get("letter_spacing"):
            lines.append(f"{prefix}-tracking:{entry['letter_spacing']};")

    return ":root{" + "".join(lines) + "}"


# --------------------------------------------------------------------------- #
# charts
# --------------------------------------------------------------------------- #

def setup_matplotlib(brand: dict, root: Path):
    """Register the brand faces with matplotlib.

    Static instances are used rather than the variable fonts: matplotlib's font
    manager only sees a variable font's default instance, so a request for bold
    silently renders regular. In a monochrome report weight is a real signal —
    it is how DOZ.AI's own series is set apart — so that fallback would be a
    silent design failure. Run tools/build_font_instances.py to create them.
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager

    static_dir = root / "brand/fonts/static"
    registered = 0
    if static_dir.is_dir():
        for ttf in sorted(static_dir.glob("*.ttf")):
            font_manager.fontManager.addfont(str(ttf))
            registered += 1

    if registered == 0:
        # Fall back to the variable files so rendering still works, but say so —
        # bold weights will be wrong and that must not pass unnoticed.
        log("WARNING: no static font instances found in brand/fonts/static.")
        log("         Chart bold weights will render as regular.")
        log("         Fix with: uv run tools/build_font_instances.py")
        for role in ("sans", "mono"):
            path = root / brand["typography"]["families"][role]["file"]
            if path.exists():
                font_manager.fontManager.addfont(str(path))

    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": brand["typography"]["families"]["sans"]["name"],
        "svg.fonttype": "path",  # outline text so the SVG needs no font at render time
        "figure.dpi": 100,
        "axes.grid": False,
    })
    return plt


def chart_to_svg(fig) -> str:
    """Serialise a figure to an inline SVG string."""
    import io
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", transparent=True)
    import matplotlib.pyplot as plt
    plt.close(fig)
    svg = buf.getvalue()
    # Drop the XML prolog and DOCTYPE so it can be inlined in HTML.
    start = svg.find("<svg")
    return svg[start:] if start != -1 else svg


def mpl_text(value: str) -> str:
    """Escape text for matplotlib.

    matplotlib treats a paired `$...$` as LaTeX math mode, so a label like
    "$167 - $499" silently loses both dollar signs and renders the middle as
    maths. Escaping every `$` is the fix; single-dollar labels are unaffected
    either way.
    """
    return str(value).replace("$", r"\$")


def chart_accent(brand: dict, fallback: str) -> str:
    """The authorised chart accent, or ink if none is approved.

    Falling back to ink rather than picking a colour keeps the output on-brand
    when brand.json has no signed-off exception — the accent is a permission,
    not a default. Returns the light-tuned value: the PDF renders on white
    paper, where the deeper #E8590C holds its contrast and the lighter
    #FF8A3D (tuned for a black ground) would wash out.

    Only Chart 2 uses this. Chart 1 is deliberately monochrome — see
    price_positioning_chart.
    """
    accent = (brand["color"].get("chart") or {}).get("accent") or {}
    if accent.get("approved_by_user") and accent.get("light"):
        return accent["light"]
    return fallback


def cost_curve_chart(plt, brand: dict, colors: dict[str, str], model: dict) -> str | None:
    """Monthly cost as client count grows — the comparison an agency actually makes.

    Replaces an entry-price bar chart. Four bars at $39/$47/$49/$69 are
    near-identical lengths and say nothing the table does not, and they chart
    the very number this section argues is the wrong comparison. What decides
    an agency purchase is how cost behaves as clients are added: a slope, not
    a value.

    Every competitor line uses the same solid style and the same grey — the
    only thing that separates a series is whether it is DOZ.AI. Varying dash
    pattern per competitor on top of that was one distinction too many; colour
    (the accent, an authorised exception to strict monochrome for exactly this
    purpose) already does the job of marking "this is us" on its own.
    Every series is direct-labelled at its endpoint rather than via a legend.
    """
    series = (model or {}).get("series") or []
    if len(series) < 2:
        return None

    clients = model.get("clients") or [1, 5, 10, 15, 20]
    max_clients = max(clients)
    muted = colors["text_muted"]
    ink = colors["text_primary"]
    grid = brand["color"]["chart"]["grid"]
    accent = chart_accent(brand, ink)
    mono = brand["typography"]["families"]["mono"]["name"]

    fig, ax = plt.subplots(figsize=(7.0, 3.5))
    ax.set_axisbelow(True)
    ax.grid(True, axis="y", color=grid, linewidth=0.8)

    xs = list(range(0, max_clients + 1))

    for s in series:
        ys = [s["base"] + s["per_client"] * n for n in xs]
        us = s.get("is_us", False)
        ax.plot(
            xs, ys,
            color=accent if us else muted,
            linewidth=2.8 if us else 1.6,
            zorder=3,
        )
        end = ys[-1]
        ax.plot([max_clients], [end], marker="o", markersize=5 if us else 4.5,
                color=accent if us else muted, zorder=4)
        ax.annotate(
            f"{s['name']}\n${end:,.0f}/mo",
            xy=(max_clients, end), xytext=(7, 0), textcoords="offset points",
            va="center", ha="left", fontsize=8,
            color=ink if us else muted,
            fontweight="bold" if us else "normal",
            fontfamily=mono,
        )

    # Annotate the crossover: it is the finding, so a reader who only looks at
    # the picture should still leave with the conclusion.
    ours = next((s for s in series if s.get("is_us")), None)
    if ours and ours["per_client"] == 0:
        for s in series:
            if s.get("is_us") or s["per_client"] <= 0:
                continue
            n_cross = (ours["base"] - s["base"]) / s["per_client"]
            if not (0 < n_cross <= max_clients):
                continue
            # Crosshair at the intersection — a full-height vertical guide and a
            # full-width horizontal guide, matching each other's style. The
            # horizontal guide is what marks where Smartlead's line crosses our
            # own flat price line ("border").
            ax.axvline(n_cross, color=accent, linewidth=1.1, linestyle=(0, (3, 3)), alpha=0.7, zorder=2)
            ax.axhline(ours["base"], color=accent, linewidth=1.1, linestyle=(0, (3, 3)), alpha=0.7, zorder=2)
            ax.plot([n_cross], [ours["base"]], marker="o", markersize=8,
                    markerfacecolor=colors["page_background"], markeredgecolor=accent,
                    markeredgewidth=2.4, zorder=5)
            # Positioned clear of both lines, with a page-coloured backing patch
            # so it stays legible over the gridlines. No connecting line to the
            # point — the dropline crosshair already ties it to the
            # intersection, so a second line pointing at the same spot was
            # redundant clutter.
            ax.annotate(
                f"{s['name']} passes us\nat ~{n_cross:.0f} clients",
                xy=(n_cross, ours["base"]), xytext=(-18, 34), textcoords="offset points",
                ha="right", va="bottom", fontsize=8, color=ink, fontweight="bold", zorder=6,
                bbox=dict(boxstyle="round,pad=0.3", facecolor=colors["page_background"],
                          edgecolor="none", alpha=0.92),
            )
            break

    ax.set_xlabel(model.get("x_label", "Client accounts"), fontsize=9, color=muted)
    ax.set_ylabel(model.get("y_label", "Monthly cost"), fontsize=9, color=muted)
    ax.set_xlim(0, max_clients * 1.28)
    ax.set_xticks(clients)
    ax.tick_params(labelsize=8.5, length=0, colors=muted)
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(colors["rule_hairline"])
    fig.tight_layout()
    return chart_to_svg(fig)


def price_positioning_chart(plt, brand: dict, colors: dict[str, str], block: dict) -> str | None:
    """Grouped horizontal bar chart of monthly price, banded Entry / Mid / Premium.

    A plain bar chart of one labelled number per row. Banding is drawn as
    separators with a band caption, so the mid tier reads as its own group and
    is compared against like rather than lost on a single continuous axis.
    """
    entries = (block or {}).get("entries") or []
    if len(entries) < 2:
        return None

    ink = colors["text_primary"]
    muted = colors["text_muted"]
    sans = brand["typography"]["families"]["sans"]["name"]
    mono = brand["typography"]["families"]["mono"]["name"]
    page_bg = colors["page_background"]
    band_defs = {b["name"]: b for b in block.get("bands", [])}
    band_order = list(band_defs.keys())

    # This chart is deliberately monochrome — no accent colour. Emphasis comes
    # from weight and value instead: every band is boxed in black, our band
    # gets a heavier black border plus a tinted panel, and our bar is solid
    # black against mid-grey competitors. That is also what the brand guide
    # asks for ("strictly monochrome"), so the accent exception is not needed
    # here at all.

    # Row model, matching the web version's boxed-band layout: each band gets
    # a bold TITLE row (name + range + an "our band" pill), a NOTE row (the
    # one-line description underneath), its bar rows, and — except after the
    # last band — a blank GAP row so boxes read as visibly separate groups
    # rather than one touching the next.
    #
    # Built bottom-to-top (barh plots index 0 at the bottom): within a band,
    # bars go in first (largest value first, so the smallest ends up nearest
    # the title above it), then note, then title, then a gap before the next
    # band starts.
    nonempty_bands = [b for b in reversed(band_order) if any(e.get("band") == b for e in entries)]
    rows: list[dict] = []
    for i, band in enumerate(nonempty_bands):
        members = [en for en in entries if en.get("band") == band]
        for en in sorted(members, key=lambda k: -k["value"]):
            label = f"{en['name']}\n{en.get('detail', '')}" if en.get("detail") else en["name"]
            rows.append({
                "kind": "bar", "label": label, "value": en["value"],
                "band": band, "is_us": en.get("is_us", False),
            })
        rows.append({"kind": "note", "band": band})
        rows.append({"kind": "title", "band": band})
        if i < len(nonempty_bands) - 1:
            rows.append({"kind": "gap"})

    labels = [r.get("label", "") for r in rows]
    values = [r.get("value", 0) for r in rows]
    is_us = [r.get("is_us", False) for r in rows]
    max_value = max(v for v in values if v)

    fig, ax = plt.subplots(figsize=(7.0, 0.4 * len(rows) + 1.4))

    # Box every band in black; ours gets a heavier border and a tinted panel so
    # it reads as the emphasised group without needing a hue. The box covers
    # the bar area from x=0 out to just past the axis's right edge, stopping
    # short of the row-label margin — that margin is auto-sized by matplotlib
    # to fit whichever label is longest, so a box edge placed a fixed distance
    # into it is guaranteed to clip on some label length.
    from matplotlib.patches import Rectangle
    # box_left sits slightly LEFT of the bars' origin. Bars start at data x=0,
    # so a box edge also at x=0 would put the border stroke — which straddles
    # its own path — directly on top of every bar's left end. The small
    # negative inset gives the bars clear space inside the box instead. It is
    # a fraction of the axis width, so it stays a consistent visual gap
    # whatever the data range, and is far too small to reach the row labels.
    box_left, box_right = -max_value * 0.028, max_value * 1.14  # right matches ax.set_xlim
    for band in band_order:
        idxs = [i for i, r in enumerate(rows) if r.get("band") == band]
        if not idxs:
            continue
        is_ours = any(r.get("is_us") for r in rows if r.get("band") == band and r["kind"] == "bar")
        y0, y1 = min(idxs) - 0.5, max(idxs) + 0.5
        ax.add_patch(Rectangle(
            (box_left, y0), box_right - box_left, y1 - y0,
            facecolor=colors["surface_container"] if is_ours else "none",
            edgecolor=ink,
            linewidth=2.2 if is_ours else 1.0,
            zorder=0.5,
        ))

    # Solid black for us, mid-grey for competitors.
    bar_colors = [ink if u else muted for u in is_us]
    # Non-bar rows get a zero-width bar so indices line up; they draw nothing.
    ax.barh(range(len(rows)), values, color=bar_colors, height=0.55, zorder=3)

    for i, row in enumerate(rows):
        if row["kind"] != "bar":
            continue
        us = row["is_us"]
        ax.text(row["value"] + max_value * 0.012, i, f"${row['value']:,.0f}",
                va="center", ha="left", fontsize=8.5,
                color=ink if us else muted, fontweight="bold" if us else "normal",
                fontfamily=mono)

    # Band title/note text is padded from the BOX's left edge, not from the
    # bars' x=0 origin, so the text keeps an even gap inside the border now
    # that the box starts left of zero.
    inset = box_left + max_value * 0.018
    for i, row in enumerate(rows):
        if row["kind"] == "title":
            meta = band_defs[row["band"]]
            is_ours = any(r.get("is_us") for r in rows if r["kind"] == "bar" and r["band"] == row["band"])
            ax.text(inset, i, row["band"], fontsize=10.5, fontweight="bold",
                    color=ink, va="center", ha="left", fontfamily=sans)
            # Fixed offset rather than measuring the name's rendered width: band
            # names here are always short words ("Entry", "Mid", "Premium"), so
            # a generous fixed gap clears them without the fragility of reading
            # back pixel extents before the figure's final layout is settled.
            ax.text(inset + max_value * 0.17, i, mpl_text(meta.get("range", "")), fontsize=8.5,
                    color=muted, va="center", ha="left", fontfamily=mono)
            if is_ours:
                # Right after the range text (same row, not pinned to the box's
                # far edge) — matches where the web version places its pill.
                ax.text(inset + max_value * 0.36, i, "OUR BAND", fontsize=7.5,
                        fontweight="bold", color=page_bg, va="center", ha="left",
                        fontfamily=mono,
                        bbox=dict(boxstyle="round,pad=0.35", facecolor=ink, edgecolor="none"))
        elif row["kind"] == "note":
            meta = band_defs[row["band"]]
            ax.text(inset, i, meta.get("note", ""), fontsize=8,
                    color=muted, va="center", ha="left", fontfamily=sans)

    ax.set_yticks(range(len(rows)), labels)
    for i, row in enumerate(rows):
        if row["kind"] == "bar" and row["is_us"]:
            ax.get_yticklabels()[i].set_fontweight("bold")
            ax.get_yticklabels()[i].set_color(ink)
    ax.set_xlabel(block.get("x_axis_label", "Monthly price, USD"), fontsize=9, color=muted)
    # Axis starts at box_left, not 0. That puts the box's left border on the
    # plot's own boundary rather than out in the row-label margin (where it
    # would strike through "Smartlead / Base plan" and friends), while still
    # leaving clear space between that border and the bars, which begin at 0.
    ax.set_xlim(box_left, box_right)
    ax.tick_params(axis="y", length=0, labelsize=8.5, colors=colors["text_body"])
    ax.tick_params(axis="x", labelsize=8.5, length=0, colors=muted)
    ax.xaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(colors["rule_hairline"])
    fig.tight_layout()
    return chart_to_svg(fig)


# --------------------------------------------------------------------------- #
# data shaping
# --------------------------------------------------------------------------- #

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def month_label(value: str) -> str:
    try:
        year, month = value.split("-")
        return f"{MONTHS[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return value


def shape_changes(changes: dict | None) -> dict | None:
    """Trim raw diff hunks down to the few lines worth printing."""
    if not changes or not changes.get("competitors"):
        return None
    for comp in changes["competitors"]:
        for page in comp.get("pages", []):
            if page.get("highlights"):
                page["highlights"] = page["highlights"][:MAX_DIFF_HIGHLIGHTS]
    return changes


REQUIRED_FIELDS = {
    "executive_summary": ["finding", "so_what"],
    "competitors": ["name", "url", "tier"],
    "recommendations": ["title", "rationale", "impact", "effort"],
    "whats_working": ["tactic", "why_it_works"],
}


def validate_analysis(data: dict) -> list[str]:
    """Check required fields before rendering.

    Optional fields are genuinely optional (the template tolerates them being
    absent), so this checks only what a section cannot render without. Failing
    here with a precise path beats a Jinja traceback that names a line number
    in a template the reader did not write.
    """
    problems: list[str] = []

    meta = data.get("meta")
    if not meta:
        problems.append("meta: missing entirely")
    else:
        for key in ("report_month", "competitor_count"):
            if key not in meta:
                problems.append(f"meta.{key}: required")

    for section, fields in REQUIRED_FIELDS.items():
        items = data.get(section)
        if not items:
            continue
        if not isinstance(items, list):
            problems.append(f"{section}: expected a list, got {type(items).__name__}")
            continue
        for i, item in enumerate(items):
            for field in fields:
                if not item.get(field):
                    problems.append(f"{section}[{i}].{field}: required")

    # The evidence rule: a claim is either sourced or explicitly marked inferred.
    for section in ("executive_summary", "whats_working"):
        for i, item in enumerate(data.get(section) or []):
            if not item.get("sources") and not item.get("inferred"):
                problems.append(
                    f"{section}[{i}]: has neither 'sources' nor 'inferred: true'. "
                    f"Every claim must be traceable or explicitly flagged."
                )

    pricing_rows = data.get("pricing", {}).get("rows") or []
    for i, row in enumerate(pricing_rows):
        if not row.get("name"):
            problems.append(f"pricing.rows[{i}].name: required")

    features = data.get("features") or {}
    if features.get("matrix"):
        ncols = len(features.get("columns") or [])
        for i, row in enumerate(features["matrix"]):
            cells = row.get("cells") or []
            if len(cells) != ncols:
                problems.append(
                    f"features.matrix[{i}]: {len(cells)} cells but {ncols} columns declared"
                )
            for cell in cells:
                if cell not in ("yes", "partial", "no"):
                    problems.append(
                        f"features.matrix[{i}]: cell value {cell!r} must be yes|partial|no"
                    )

    return problems


CURRENCY = ("$", "€", "£", "¥")


def has_price(row) -> bool:
    """True when a row carries a captured price.

    Competitors whose pricing could not be captured are dropped from the table
    rather than shown as a row of 'not captured'. They are still named beneath
    it, so nothing is hidden — the table just stays readable.
    """
    if isinstance(row.get("entry_value"), (int, float)):
        return True
    entry = str(row.get("entry") or "").strip()
    return entry.startswith(CURRENCY)


def with_leverage(recommendations: list[dict] | None) -> list[dict] | None:
    """Attach a computed 'quick win / balanced / heavy' tag to each recommendation.

    Derived rather than authored so the tag cannot contradict the impact and
    effort values printed beside it — analysis.json states those two, and this
    is the only place the third is computed from them.
    """
    if not recommendations:
        return recommendations
    order = {"low": 1, "medium": 2, "high": 3}
    out = []
    for rec in recommendations:
        impact = str(rec.get("impact", "")).lower()
        effort = str(rec.get("effort", "")).lower()
        lev = order.get(impact, 0) - order.get(effort, 0)
        tag = "quick win" if lev >= 1 else ("heavy" if lev <= -1 else "balanced")
        out.append({**rec, "leverage": tag})
    return out


def filter_pricing(pricing: dict | None) -> dict | None:
    if not pricing or not pricing.get("rows"):
        return pricing
    rows = pricing["rows"]
    kept = [r for r in rows if has_price(r)]
    omitted = [r.get("name") for r in rows if not has_price(r)]
    out = dict(pricing)
    out["rows"] = kept
    out["omitted"] = omitted
    return out


def group_sources(sources):
    """One appendix row per company rather than one per page.

    analysis.json keeps every page for traceability; repeating the company name
    once per page just makes the table longer to scan, not more rigorous.
    """
    grouped: dict[str, dict] = {}
    for s in sources or []:
        name = s.get("competitor", "")
        entry = grouped.setdefault(
            name, {"competitor": name, "pages": [], "urls": {}, "snapshot_date": s.get("snapshot_date")}
        )
        page = s.get("page_type", "")
        if page and page not in entry["pages"]:
            entry["pages"].append(page)
        entry["urls"][page] = s.get("url")

    out = []
    for entry in grouped.values():
        entry["page_list"] = ", ".join(entry["pages"])
        # Prefer the pricing page: it carries the claims most worth checking.
        entry["url"] = entry["urls"].get("pricing") or next(iter(entry["urls"].values()), "")
        out.append(entry)
    return out


def count_sections(data: dict) -> int:
    keys = ["executive_summary", "pricing", "messaging", "features",
            "competitors", "whats_working", "recommendations"]
    n = sum(1 for k in keys if data.get(k))
    if data.get("market_map"):
        n += 1
    if data.get("changes", {}).get("competitors"):
        n += 1
    return n + 1  # appendix


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def build_html(data: dict, brand: dict, root: Path, template_path: Path,
               css_path: Path, allow_unapproved: bool) -> str:
    from jinja2 import Environment, FileSystemLoader

    colors = resolve_colors(brand)

    charts: dict[str, str] = {}
    chart_cfg = brand["color"].get("chart", {})
    needs_charts = bool(data.get("pricing", {}).get("rows"))
    unapproved = chart_cfg.get("status") == "proposed-extension" and not chart_cfg.get("approved_by_user")

    if needs_charts and unapproved and not allow_unapproved:
        raise SystemExit(
            "ERROR: brand.json color.chart is a proposed extension to the brand guide and\n"
            "       approved_by_user is false. Charts use greys that are NOT in the guide.\n"
            "       Get sign-off and set approved_by_user: true, or re-run with\n"
            "       --allow-unapproved-chart-colors to preview."
        )
    if needs_charts and unapproved:
        log("WARNING: rendering charts with UNAPPROVED derived greys (preview only)")

    if needs_charts:
        plt = setup_matplotlib(brand, root)
        pricing = data.get("pricing", {})
        if pricing.get("price_positioning"):
            svg = price_positioning_chart(plt, brand, colors, pricing["price_positioning"])
            if svg:
                charts["price_positioning"] = svg
        if pricing.get("cost_model"):
            svg = cost_curve_chart(plt, brand, colors, pricing["cost_model"])
            if svg:
                charts["cost_curve"] = svg

    logo_variants = brand["logo"]["variants"]
    logo = {
        "lockup": data_uri(root / logo_variants["lockup"]["file"]),
        "symbol": data_uri(root / logo_variants["symbol"]["file"]),
    }

    meta = dict(data.get("meta", {}))
    meta.setdefault("title", "Competitive landscape")
    meta.setdefault("subtitle", "")
    meta["report_month_label"] = month_label(meta.get("report_month", ""))
    meta["run_type_label"] = "Baseline" if meta.get("run_type") == "baseline" else "Monthly"
    meta["generated_date"] = datetime.now().strftime("%d %b %Y")
    meta.setdefault(
        "footer_note",
        f"{brand['brand']['name']} internal competitive intelligence · "
        f"generated {meta['generated_date']} · sources listed above",
    )

    # Default Undefined (not Strict): optional fields are genuinely optional and
    # must render as absent rather than raise. Required fields are checked up
    # front by validate_analysis(), which gives a far better error message.
    env = Environment(
        loader=FileSystemLoader(template_path.parent),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_path.name)

    # Autoescape stays on so scraped competitor text can never inject markup.
    # The chart SVGs are generated by matplotlib here, not scraped, so they are
    # the one thing marked safe.
    from markupsafe import Markup

    body = template.render(
        brand=brand["brand"],
        meta=meta,
        logo=logo,
        charts={k: Markup(v) for k, v in charts.items()},
        executive_summary=data.get("executive_summary"),
        market_map=data.get("market_map"),
        pricing=filter_pricing(data.get("pricing")),
        messaging=data.get("messaging"),
        features=data.get("features"),
        competitors=data.get("competitors"),
        whats_working=data.get("whats_working"),
        recommendations=with_leverage(data.get("recommendations")),
        changes=shape_changes(data.get("changes")),
        appendix={**data.get("appendix", {}),
                  "sources": group_sources(data.get("appendix", {}).get("sources"))},
        section_count=count_sections(data),
    )

    style = font_face_css(brand, root) + css_variables(brand, colors)
    css = css_path.read_text(encoding="utf-8")

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{brand['brand']['name']} competitor report</title>"
        f"<style>{style}</style><style>{css}</style>"
        f"</head><body>{body}</body></html>"
    )


def html_to_pdf(html: str, out_path: Path, brand: dict, colors: dict[str, str]) -> None:
    from playwright.sync_api import sync_playwright

    page_cfg = brand.get("page", {})
    margin = page_cfg.get("margin_mm", {"top": 18, "right": 16, "bottom": 18, "left": 16})
    mono = brand["typography"]["families"]["mono"]["name"]

    footer = (
        f"<div style=\"width:100%;font-size:8px;font-family:'{mono}',monospace;"
        f"color:{colors['text_muted']};padding:0 {margin['right']}mm;"
        f"display:flex;justify-content:space-between;\">"
        f"<span>{brand['brand']['name']}</span>"
        f"<span class='pageNumber'></span>"
        f"</div>"
    )
    # Chromium requires a header template when displayHeaderFooter is on; an
    # empty one keeps page 1 clean while still reserving the footer band.
    header = "<div></div>"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_html = Path(tmp) / "report.html"
        tmp_html.write_text(html, encoding="utf-8")

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(tmp_html.as_uri(), wait_until="networkidle")
            page.emulate_media(media="print")
            page.pdf(
                path=str(out_path),
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template=header,
                footer_template=footer,
                margin={
                    "top": f"{margin['top']}mm",
                    "right": f"{margin['right']}mm",
                    "bottom": f"{margin['bottom']}mm",
                    "left": f"{margin['left']}mm",
                },
            )
            browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="path to analysis.json")
    ap.add_argument("--out", required=True, help="output PDF path")
    ap.add_argument("--brand-json", default="brand/brand.json")
    ap.add_argument("--template", default="brand/report_template.html")
    ap.add_argument("--css", default="brand/report.css")
    ap.add_argument("--root", default=".", help="repo root for asset resolution")
    ap.add_argument("--keep-html", action="store_true", help="also write the intermediate HTML next to the PDF")
    ap.add_argument("--allow-unapproved-chart-colors", action="store_true",
                    help="render charts even though brand.json marks the chart ramp unapproved")
    args = ap.parse_args()

    root = Path(args.root)
    data_path = Path(args.data)
    if not data_path.exists():
        log(f"ERROR: analysis data not found: {data_path}")
        return 1

    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"ERROR: {data_path} is not valid JSON: {exc}")
        return 1

    problems = validate_analysis(data)
    if problems:
        log(f"ERROR: {data_path} failed validation ({len(problems)} problem(s)):")
        for problem in problems:
            log(f"  - {problem}")
        return 1

    brand = load_brand(Path(args.brand_json))
    colors = resolve_colors(brand)

    log(f"rendering {data_path} with {args.brand_json}")

    html = build_html(
        data, brand, root, Path(args.template), Path(args.css),
        args.allow_unapproved_chart_colors,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.keep_html:
        html_path = out_path.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")
        log(f"  wrote {html_path}")

    html_to_pdf(html, out_path, brand, colors)
    size_kb = out_path.stat().st_size / 1024
    log(f"  wrote {out_path} ({size_kb:,.0f} KB)")

    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
