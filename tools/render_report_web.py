"""Render analysis.json into a self-contained web page for publishing as an Artifact.

The PDF is the deliverable of record; this is the shareable read-online version. It
is a separate renderer rather than the print HTML reused, for three reasons:

1. The print layout is paginated A4. On a screen that reads as a stack of
   awkwardly-broken pages rather than a document.
2. The matplotlib charts bake their colours into the SVG. Black bars disappear on
   a dark ground, so screen charts are rebuilt in CSS driven by theme tokens.
3. Artifacts render in the viewer's theme. The print stylesheet only has one.

Brand rules are unchanged: every colour still resolves from brand.json, and the
strict-monochrome rule holds in both themes. The dark palette is not an invention —
brand.json already documents #000000 as `surface_inverse` with #FFFFFF as
`text_on_inverse`, and the guide supplies reversed logo variants for dark grounds.

Output has no <!doctype>/<html>/<head>/<body> — the Artifact host supplies those.

Usage:
    uv run tools/render_report_web.py --data research/analysis/2026-08/analysis.json \\
        --out .tmp/report_web.html
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import sys
from datetime import datetime
from pathlib import Path

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def e(value) -> str:
    """Escape for HTML. All competitor text is scraped, so nothing is trusted."""
    return html.escape(str(value if value is not None else ""))


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def month_label(value: str) -> str:
    try:
        year, month = value.split("-")
        return f"{MONTHS[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return value


# --------------------------------------------------------------------------- #
# fragments
# --------------------------------------------------------------------------- #

def group_sources(sources):
    """One row per company rather than one per page.

    analysis.json keeps every page for traceability, but a reader scanning the
    appendix wants to know which companies were captured and when — repeating
    the company name once per page makes a 7-company table 13 rows long and
    harder to scan, not more rigorous.
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
        # Link the pricing page when there is one — it carries the claims that
        # most need checking. Otherwise fall back to whatever was captured.
        primary = entry["urls"].get("pricing") or next(iter(entry["urls"].values()), "")
        entry["primary_url"] = primary
        out.append(entry)
    return out


def sources_html(items) -> str:
    if not items:
        return ""
    parts = [
        f'<a class="src" href="{e(s.get("url"))}" target="_blank" rel="noopener">'
        f'{e(s.get("url"))}<span class="src-date">{e(s.get("snapshot_date"))}</span></a>'
        for s in items
    ]
    return f'<div class="sources">{"".join(parts)}</div>'


def findings_html(items, key="finding", sub="so_what", start=1) -> str:
    rows = []
    for i, item in enumerate(items, start):
        badge = '<span class="inferred">inferred</span>' if item.get("inferred") else ""
        extra = ""
        if item.get("evidence"):
            extra = f'<p class="evidence">{e(item["evidence"])}</p>'
        rows.append(
            f'<li class="finding">'
            f'<span class="fnum">{i:02d}</span>'
            f'<div class="fbody">'
            f'<p class="fclaim">{e(item.get(key))}{badge}</p>'
            f'<p class="fso"><span class="so-label">So what</span>{e(item.get(sub))}</p>'
            f'{extra}{sources_html(item.get("sources"))}'
            f'</div></li>'
        )
    return f'<ol class="findings">{"".join(rows)}</ol>'


def price_bars_html(rows) -> str:
    """CSS bar chart. Rebuilt from the print SVG so it themes correctly."""
    points = [r for r in rows if isinstance(r.get("entry_value"), (int, float))]
    if len(points) < 2:
        return ""
    points.sort(key=lambda r: r["entry_value"])
    top = max(r["entry_value"] for r in points)

    bars = []
    for r in points:
        pct = (r["entry_value"] / top) * 100
        us = " is-us" if r.get("is_us") else ""
        bars.append(
            f'<div class="bar-row{us}">'
            f'<div class="bar-label">{e(r["name"])}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>'
            f'<div class="bar-value">{e(r.get("entry"))}</div>'
            f"</div>"
        )
    return f'<div class="bars">{"".join(bars)}</div>'


CURRENCY = ("$", "€", "£", "¥")


def has_price(row) -> bool:
    """True when a row actually carries a captured price.

    Rows for competitors whose pricing could not be captured are dropped from
    the table rather than shown as a line of 'not captured'. The fact is not
    lost — omitted companies are named beneath the table — but the comparison
    stays readable, which is what the table is for.
    """
    if isinstance(row.get("entry_value"), (int, float)):
        return True
    entry = str(row.get("entry") or "").strip()
    return entry.startswith(CURRENCY)


def omitted_note(rows) -> str:
    missing = [r.get("name") for r in rows if not has_price(r)]
    if not missing:
        return ""
    return (
        f'<p class="caption mono">Omitted from this table: {e(", ".join(missing))} '
        f"— no published or capturable pricing. See limitations.</p>"
    )


def cost_curve_svg(model) -> str:
    """Cost to run N client accounts — the chart the agency argument needs.

    An entry-price bar chart compares $39 / $47 / $49 / $69, four near-identical
    bars that say nothing the numbers do not. What decides an agency purchase is
    how cost behaves as clients are added, and that is a slope, not a value —
    so this is a line chart with the crossover point marked.

    Drawn as SVG using currentColor and CSS variables so it themes correctly;
    a rasterised or colour-baked chart would break on a dark ground.
    """
    if not model or not model.get("series"):
        return ""

    clients = model.get("clients") or [1, 5, 10, 15, 20]
    series = model["series"]
    max_clients = max(clients)
    raw_max = max(s["base"] + s["per_client"] * max_clients for s in series) * 1.1

    # Round the axis up to a readable step. An axis reading $755 / $566 / $377
    # makes the reader decode the gridlines instead of the data.
    def nice_ceiling(value: float, steps: int = 4) -> float:
        import math
        rough = value / steps
        magnitude = 10 ** math.floor(math.log10(rough)) if rough > 0 else 1
        for mult in (1, 2, 2.5, 5, 10):
            if magnitude * mult >= rough:
                return magnitude * mult * steps
        return magnitude * 10 * steps

    max_cost = nice_ceiling(raw_max)

    W, H = 720, 340
    ML, MR, MT, MB = 62, 132, 18, 46
    pw, ph = W - ML - MR, H - MT - MB

    def x(n): return ML + (n / max_clients) * pw
    def y(c): return MT + ph - (c / max_cost) * ph

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{e(model.get("title"))}">']

    # horizontal gridlines with cost labels
    steps = 4
    for i in range(steps + 1):
        cost = max_cost * i / steps
        gy = y(cost)
        parts.append(
            f'<line x1="{ML}" y1="{gy:.1f}" x2="{ML + pw}" y2="{gy:.1f}" '
            f'class="cg-grid"/>'
            f'<text x="{ML - 10}" y="{gy + 4:.1f}" class="cg-tick" text-anchor="end">'
            f"${cost:,.0f}</text>"
        )

    # x axis ticks
    for n in clients:
        parts.append(
            f'<text x="{x(n):.1f}" y="{MT + ph + 22}" class="cg-tick" text-anchor="middle">{n}</text>'
        )
    parts.append(
        f'<text x="{ML + pw / 2:.1f}" y="{H - 6}" class="cg-axis" text-anchor="middle">'
        f'{e(model.get("x_label", "Clients"))}</text>'
    )
    # Y-axis title, rotated. Without it the reader has to infer that the $ ticks
    # are monthly rather than annual or per-client.
    parts.append(
        f'<text transform="rotate(-90 14 {MT + ph / 2:.1f})" x="14" y="{MT + ph / 2:.1f}" '
        f'class="cg-axis" text-anchor="middle">{e(model.get("y_label", "Monthly cost, USD"))}</text>'
    )

    # One path per vendor, direct-labelled at its endpoint — no legend needed.
    # Every competitor line uses the same solid style and the same grey; colour
    # alone (the accent) marks which one is DOZ.AI. Varying dash pattern per
    # competitor on top of that was one distinction too many.
    for s in series:
        pts = [(x(n), y(s["base"] + s["per_client"] * n)) for n in range(0, max_clients + 1)]
        d = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in pts)
        cls = "cg-line cg-us" if s.get("is_us") else "cg-line"
        parts.append(f'<path d="{d}" class="{cls}"/>')

        end_cost = s["base"] + s["per_client"] * max_clients
        ey = y(end_cost)
        label_cls = "cg-label cg-us-label" if s.get("is_us") else "cg-label"
        parts.append(
            f'<circle cx="{x(max_clients):.1f}" cy="{ey:.1f}" r="3.5" class="{cls} cg-dot"/>'
            f'<text x="{x(max_clients) + 10:.1f}" y="{ey - 2:.1f}" class="{label_cls}">'
            f'{e(s["name"])}</text>'
            f'<text x="{x(max_clients) + 10:.1f}" y="{ey + 12:.1f}" class="cg-endcost">'
            f"${end_cost:,.0f}/mo</text>"
        )

    # Mark where a rising competitor crosses our flat line. The crossover IS the
    # finding — annotating it on the chart means a reader who only looks at the
    # picture still leaves with the conclusion, rather than having to compute it.
    ours = next((s for s in series if s.get("is_us")), None)
    if ours and ours["per_client"] == 0:
        for s in series:
            if s.get("is_us") or s["per_client"] <= 0:
                continue
            n_cross = (ours["base"] - s["base"]) / s["per_client"]
            if not (0 < n_cross <= max_clients):
                continue
            cx, cy = x(n_cross), y(ours["base"])
            # Crosshair: full-height vertical guide + full-width horizontal
            # guide, marking exactly where Smartlead's line crosses our own
            # flat price line. No line connects to the text label — the
            # crosshair itself already ties the annotation to the point.
            parts.append(
                f'<line x1="{cx:.1f}" y1="{MT}" x2="{cx:.1f}" y2="{MT + ph}" class="cg-cross"/>'
                f'<line x1="{ML}" y1="{cy:.1f}" x2="{ML + pw}" y2="{cy:.1f}" class="cg-cross"/>'
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" class="cg-crossdot"/>'
                # Clear of the line itself — at cy-1 the sub-label sat on top of it.
                f'<text x="{cx + 9:.1f}" y="{cy - 26:.1f}" class="cg-crosslabel">'
                f"{s['name']} passes us</text>"
                f'<text x="{cx + 9:.1f}" y="{cy - 13:.1f}" class="cg-crosssub">'
                f"at ~{n_cross:.0f} clients</text>"
            )
            break

    parts.append("</svg>")
    return f'<div class="chart cost-curve">{"".join(parts)}</div>'


def chart_head(block, number_hint: str = "") -> str:
    """Standard chart header: title, the question it answers, and the as-at date.

    Every chart carries all three. A chart without a date is unusable in a
    monthly series — the reader cannot tell which month's prices they are
    looking at — and a chart without a stated question invites the reader to
    guess what they are meant to take from it.
    """
    title = block.get("title") or number_hint
    question = block.get("question")
    as_at = block.get("as_at")
    parts = [f'<h3 class="chart-title">{e(title)}</h3>']
    if question:
        parts.append(f'<p class="chart-question">{e(question)}</p>')
    if as_at:
        parts.append(f'<p class="chart-date mono">Prices as at {e(as_at)}</p>')
    return "".join(parts)


def price_positioning_html(block) -> str:
    """Grouped horizontal bar chart: monthly price, banded Entry / Mid / Premium.

    A plain bar chart rather than the range-overlap plot it replaces. Ranges
    were accurate but needed explaining before they could be read; a bar of a
    single labelled number does not. Banding gives the mid tier its own visual
    block so it is compared against like, which a single continuous axis buried.
    """
    if not block or not block.get("entries"):
        return ""
    entries = block["entries"]
    top = max(en["value"] for en in entries) * 1.02
    bands = block.get("bands", [])

    out = []
    for band in bands:
        rows = [en for en in entries if en.get("band") == band["name"]]
        if not rows:
            continue
        is_our_band = any(r.get("is_us") for r in rows)
        out.append(
            f'<div class="band{" our-band" if is_our_band else ""}">'
            f'<div class="band-head">'
            f'<span class="band-name">{e(band["name"])}</span>'
            f'<span class="band-range mono">{e(band["range"])}</span>'
            f'{"<span class=\'band-tag mono\'>our band</span>" if is_our_band else ""}'
            f"</div>"
            f'<p class="band-note">{e(band.get("note"))}</p>'
        )
        for r in sorted(rows, key=lambda k: k["value"]):
            width = (r["value"] / top) * 100
            us = " is-us" if r.get("is_us") else ""
            out.append(
                f'<div class="pbar-row{us}">'
                f'<div class="pbar-name">{e(r["name"])}<span class="pbar-detail">{e(r.get("detail"))}</span></div>'
                f'<div class="pbar-track"><div class="pbar-fill" style="width:{width:.1f}%"></div></div>'
                f'<div class="pbar-val mono">{e(r.get("label"))}</div>'
                f"</div>"
            )
        out.append("</div>")

    axis = block.get("x_axis_label", "Monthly price, USD")
    return (
        f'<div class="price-bands">{"".join(out)}</div>'
        f'<p class="axis-label mono">&#8594; {e(axis)}</p>'
    )


def capability_table_html(features) -> str:
    """Plain side-by-side table with explicit Yes / Partial / blank.

    Replaces filled, half and open circles. Those needed a legend to decode and
    the half-circle was routinely misread; the words do not.
    """
    cols = features.get("columns") or []
    head = "".join(
        f'<th class="ctr{" col-us" if i == 0 else ""}">{e(c)}</th>' for i, c in enumerate(cols)
    )
    rows = []
    for row in features.get("matrix") or []:
        cells = []
        for i, c in enumerate(row.get("cells", [])):
            label = {"yes": "Yes", "partial": "Partial", "no": ""}.get(c, "")
            cls = f"cap-{c}" + (" col-us" if i == 0 else "")
            cells.append(f'<td class="ctr {cls}">{label}</td>')
        rows.append(
            f'<tr><td class="strong">{e(row.get("capability"))}</td>{"".join(cells)}</tr>'
        )

    header = ""
    if features.get("table_title"):
        header += f'<h3 class="chart-title">{e(features["table_title"])}</h3>'
    if features.get("as_at"):
        header += f'<p class="chart-date mono">Assessed {e(features["as_at"])}</p>'

    legend = ""
    if features.get("legend"):
        legend = f'<p class="caption mono">{e(features["legend"])}</p>'

    return (
        header
        + '<div class="scroller"><table class="cap-table">'
        f"<thead><tr><th>Capability</th>{head}</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table></div>' + legend
    )


def insight_html(text) -> str:
    if not text:
        return ""
    return f'<p class="insight">{e(text)}</p>'


def pricing_table_html(rows) -> str:
    body = []
    for r in [row for row in rows if has_price(row)]:
        us = ' class="is-us"' if r.get("is_us") else ""
        badge = '<span class="inferred">inferred</span>' if r.get("inferred") else ""
        # Placeholder gets its own, louder treatment. "Inferred" means reasoned
        # from evidence; these numbers are invented for layout testing, and in a
        # report whose whole premise is sourced figures they must be impossible
        # to mistake for real ones.
        if r.get("placeholder"):
            badge += '<span class="placeholder">test data</span>'
        body.append(
            f"<tr{us}>"
            f'<td class="strong">{e(r.get("name"))}</td>'
            f"<td>{e(r.get('model'))}</td>"
            f'<td class="num">{e(r.get("entry"))}</td>'
            f'<td class="num">{e(r.get("mid"))}</td>'
            f"<td>{e(r.get('notes'))}{badge}</td>"
            f"</tr>"
        )
    return (
        '<div class="scroller"><table class="pricing-table">'
        "<thead><tr>"
        '<th class="c-company">Company</th>'
        '<th class="c-model">Model</th>'
        '<th class="c-num">Entry</th>'
        '<th class="c-num">Mid tier</th>'
        "<th>Notes</th>"
        "</tr></thead>"
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def matrix_html(features) -> str:
    cols = features.get("columns") or []
    head = "".join(f'<th class="ctr">{e(c)}</th>' for c in cols)
    rows = []
    for row in features.get("matrix") or []:
        cells = "".join(
            f'<td class="ctr"><span class="mark mark-{e(c)}" title="{e(c)}"></span></td>'
            for c in row.get("cells", [])
        )
        rows.append(f'<tr><td class="strong">{e(row.get("capability"))}</td>{cells}</tr>')
    return (
        '<div class="scroller"><table class="matrix">'
        f"<thead><tr><th>Capability</th>{head}</tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<p class="legend"><span class="mark mark-yes"></span> has it'
        '<span class="mark mark-partial"></span> partial'
        '<span class="mark mark-no"></span> absent</p>'
    )


def recs_html(recs) -> str:
    order = {"high": 3, "medium": 2, "low": 1}
    out = []
    for i, r in enumerate(recs, 1):
        impact = str(r.get("impact", "")).lower()
        effort = str(r.get("effort", "")).lower()
        # Leverage = impact relative to effort. Shown as a chip so the ranking
        # is visible at a glance rather than requiring the reader to compare.
        lev = order.get(impact, 0) - order.get(effort, 0)
        tag = "quick win" if lev >= 1 else ("heavy" if lev <= -1 else "balanced")
        traces = (
            f'<span class="chip mono">from {e(r["traces_to"])}</span>' if r.get("traces_to") else ""
        )
        out.append(
            f'<li class="rec">'
            f'<span class="rnum">{i}</span>'
            f'<div class="rbody">'
            f'<p class="rtitle">{e(r.get("title"))}</p>'
            f'<p>{e(r.get("rationale"))}</p>'
            f'<div class="chips">'
            f'<span class="chip mono">impact {e(impact)}</span>'
            f'<span class="chip mono">effort {e(effort)}</span>'
            f'<span class="chip chip-lev mono">{tag}</span>{traces}'
            f"</div></div></li>"
        )
    return f'<ol class="recs">{"".join(out)}</ol>'


def competitors_html(items) -> str:
    out = []
    for c in items:
        they = "".join(f"<li>{e(x)}</li>" for x in c.get("they_win", []))
        we = "".join(f"<li>{e(x)}</li>" for x in c.get("we_win", []))
        out.append(
            f'<article class="comp">'
            f'<header class="comp-head">'
            f'<h3>{e(c.get("name"))}</h3><span class="chip mono">{e(c.get("tier"))}</span>'
            f"</header>"
            f'<p class="comp-line">{e(c.get("one_liner"))}</p>'
            f'<div class="cols">'
            f'<div><h4>Where they beat us</h4><ul>{they}</ul></div>'
            f'<div><h4>Where we beat them</h4><ul>{we}</ul></div>'
            f"</div>"
            f'{sources_html(c.get("sources"))}'
            f"</article>"
        )
    return "".join(out)


def list_panel(title, items, dark=False) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{e(x)}</li>" for x in items)
    cls = "panel panel-dark" if dark else "panel"
    return f'<div class="{cls}"><h4 class="eyebrow">{e(title)}</h4><ul>{lis}</ul></div>'


# --------------------------------------------------------------------------- #

def build(data: dict, brand: dict, root: Path) -> str:
    palette = {k: v["hex"] for k, v in brand["color"]["palette"].items()}
    ramp = brand["color"]["chart"]["series_provenance"]
    fam = brand["typography"]["families"]

    # Chart accent: an authorised exception to strict monochrome, charts only.
    # Falls back to ink if brand.json has no approved accent, so the page stays
    # on-brand rather than borrowing a colour that was never signed off.
    accent_cfg = brand["color"]["chart"].get("accent") or {}
    accent_ok = accent_cfg.get("approved_by_user")
    accent_light = accent_cfg.get("light") if accent_ok else palette["primary_solid"]
    accent_dark = accent_cfg.get("dark") if accent_ok else palette["absolute_light"]

    sans_uri = data_uri(root / fam["sans"]["file"])
    mono_uri = data_uri(root / fam["mono"]["file"])
    logo_light = data_uri(root / brand["logo"]["variants"]["lockup"]["file"])
    logo_dark = data_uri(root / brand["logo"]["variants"]["lockup_reversed"]["file"])

    meta = data.get("meta", {})
    month = month_label(meta.get("report_month", ""))

    # Dark-theme muted must lift off #5B5B5B: that grey is only 3.1:1 on black,
    # which fails AA for body text. #8A8A8A is from the approved chart ramp and
    # clears 6.1:1, so the dark theme stays inside the brand system.
    muted_dark = "#8A8A8A" if "#8A8A8A" in ramp else palette["light_gray"]

    sections = []

    def add(num, title, inner, anchor):
        sections.append(
            f'<section id="{anchor}"><div class="shead">'
            f'<span class="snum mono">{num}</span><h2>{e(title)}</h2></div>{inner}</section>'
        )

    nav = [("summary", "Summary"), ("market", "Market map"), ("pricing", "Pricing"),
           ("messaging", "Messaging"), ("features", "Features"),
           ("rivals", "Head to head"), ("working", "What works"),
           ("actions", "Actions"), ("method", "Method")]

    # ---- lead finding: the single most decision-relevant claim -------------
    # Promoted out of the summary rather than duplicated into a hero. The list
    # below therefore starts at 02 — repeating finding 01 verbatim would make a
    # reader who scrolled think they had lost their place.
    summary_items = data.get("executive_summary") or []
    lead = summary_items[0] if summary_items else {}
    rest = summary_items[1:]

    lead_html = (
        f'<div class="lead">'
        f'<p class="eyebrow">Most urgent finding</p>'
        f'<p class="lead-claim">{e(lead.get("finding"))}</p>'
        f'<p class="lead-so">{e(lead.get("so_what"))}</p>'
        f'{sources_html(lead.get("sources"))}</div>'
    ) if lead else ""

    if rest:
        add("01", "Executive summary",
            '<p class="intro">The most urgent finding is called out above. '
            f'{len(rest)} further findings follow.</p>' + findings_html(rest, start=2),
            "summary")

    if data.get("market_map"):
        mm = data["market_map"]
        tiers = "".join(
            f'<div class="tier"><span class="eyebrow">{e(t)} &middot; {len(v)}</span>'
            f'<p>{e(" · ".join(v))}</p></div>'
            for t, v in mm.get("tiers", {}).items() if v
        )
        pos = (
            f'<div class="panel panel-dark"><h4 class="eyebrow">Where {e(brand["brand"]["name"])} sits</h4>'
            f'<p>{e(mm.get("our_position"))}</p></div>'
        ) if mm.get("our_position") else ""
        add("02", "Market map", f'<div class="tiers">{tiers}</div>{pos}', "market")

    if data.get("pricing"):
        p = data["pricing"]
        # A standing warning wherever invented figures are present. Silence here
        # would let test numbers travel into a real decision.
        has_placeholder = any(r.get("placeholder") for r in (p.get("rows") or []))
        inner = ""
        if has_placeholder:
            inner += (
                '<div class="warnbar"><span class="tagline">Test data present</span>'
                "<p>DOZ.AI's own figures are invented placeholders for layout testing, marked "
                "<span class=\"placeholder\">test data</span> in the table. Every competitor "
                "figure is real and sourced. Replace ours before showing this to anyone.</p></div>"
            )
        inner += f'<p class="intro">{e(p.get("intro"))}</p>'

        # Chart 1 — where we sit. Chart 2 — when we become cheaper. Two charts,
        # two questions, in that order because the second answers the first's
        # obvious objection ("you are the most expensive in your band").
        pos = p.get("price_positioning")
        if pos:
            inner += (
                chart_head(pos)
                + price_positioning_html(pos)
                + f'<p class="caption mono">{e(pos.get("caption"))}</p>'
                + insight_html(pos.get("insight"))
            )

        cm = p.get("cost_model")
        if cm:
            inner += (
                chart_head(cm)
                + cost_curve_svg(cm)
                + f'<p class="caption mono">{e(cm.get("caption"))}</p>'
                + insight_html(cm.get("insight"))
            )

        inner += pricing_table_html(p.get("rows") or [])
        inner += omitted_note(p.get("rows") or [])

        # The entry-price bar chart is deliberately gone. Four bars at $39/$47/
        # $49/$69 are near-identical lengths, so it added nothing the table did
        # not already say — and it charted the very number this section argues
        # is the wrong comparison. The cost curve replaces it.
        if p.get("observations"):
            inner += findings_html(p["observations"])
        add("03", "Pricing & business model", inner, "pricing")

    if data.get("messaging"):
        m = data["messaging"]
        rows = "".join(
            f'<tr{" class=\'is-us\'" if r.get("is_us") else ""}>'
            f'<td class="strong">{e(r.get("name"))}</td><td>{e(r.get("headline"))}</td>'
            f'<td>{e(r.get("audience"))}</td></tr>'
            for r in m.get("rows", [])
        )
        inner = (
            f'<p class="intro">{e(m.get("intro"))}</p>'
            f'<div class="scroller"><table><thead><tr><th>Company</th>'
            f"<th>Headline claim</th><th>Who they say it's for</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )
        # The claim-contest chart was removed here. Counting how many rivals use
        # a phrase is a one-number fact per row; the two lists below say it
        # directly and in fewer pixels. A chart that needs a paragraph to explain
        # what its bars mean is not earning its place.
        inner += (
            list_panel("Crowded language — claimed by more than one player", m.get("crowded"))
            + list_panel("Unclaimed ground", m.get("whitespace"), dark=True)
        )
        add("04", "Marketing & messaging", inner, "messaging")

    if data.get("features"):
        f = data["features"]
        # One table, not a chart plus a matrix. The segmented readiness bars said
        # the same thing as the table below them, less precisely.
        inner = f'<p class="intro">{e(f.get("intro"))}</p>'
        inner += (
            capability_table_html(f)
            + '<div class="cols cols-gap">'
            + list_panel("They have, we don't", f.get("gaps_ours"))
            + list_panel("We have, they don't", f.get("gaps_theirs"))
            + "</div>"
        )
        add("05", "Products, services & features", inner, "features")

    if data.get("competitors"):
        add("06", f'Strengths & weaknesses vs {brand["brand"]["name"]}',
            competitors_html(data["competitors"]), "rivals")

    if data.get("whats_working"):
        add("07", "What's working for them",
            findings_html(data["whats_working"], key="tactic", sub="why_it_works"), "working")

    if data.get("recommendations"):
        add("08", f'Where {brand["brand"]["name"]} can improve',
            recs_html(data["recommendations"]), "actions")

    # ---- method / appendix -------------------------------------------------
    ap = data.get("appendix", {})
    grouped = group_sources(ap.get("sources"))
    src_rows = "".join(
        f'<tr><td class="strong">{e(g["competitor"])}</td>'
        f'<td class="mono">{e(", ".join(g["pages"]))}</td>'
        f'<td class="mono">{e(g["snapshot_date"])}</td>'
        f'<td class="mono break"><a href="{e(g["primary_url"])}" target="_blank" rel="noopener">'
        f'{e(g["primary_url"])}</a></td></tr>'
        for g in grouped
    )
    total_pages = sum(len(g["pages"]) for g in grouped)
    method = (
        '<p class="intro">Every factual claim traces to a page captured on the date shown. '
        'Claims that could not be sourced are marked <span class="inferred">inferred</span> '
        "where they appear.</p>"
        f'<div class="scroller"><table><thead><tr><th>Company</th><th>Pages captured</th>'
        f"<th>Captured</th><th>Primary source</th></tr></thead><tbody>{src_rows}</tbody></table></div>"
        f'<p class="caption mono">{len(grouped)} companies, {total_pages} pages. '
        f"Link goes to the pricing page where one was captured, since that carries the claims "
        f"most worth checking.</p>"
        + list_panel("Limitations", ap.get("limitations"))
    )
    add("09", "Sources & method", method, "method")

    navlinks = "".join(f'<a href="#{a}">{e(t)}</a>' for a, t in nav)

    css = f"""
:root {{
  --ink:{palette['primary_solid']};
  --body:{palette['charcoal']};
  --muted:{palette['slate_gray']};
  --ground:{palette['absolute_light']};
  --surface:{palette['light_gray']};
  --line:{palette['light_gray']};
  --line-strong:{palette['primary_solid']};
  --inverse-bg:{palette['primary_solid']};
  --inverse-fg:{palette['absolute_light']};
  --bar:{palette['slate_gray']};
  --bar-us:{palette['primary_solid']};
  /* Numbered badges get their own tokens rather than reusing the inverse pair.
     On a dark ground a full inverse fill reads as a stark white box rather than
     emphasis, and clashes with the hairline language used everywhere else. */
  --badge-bg:{palette['primary_solid']};
  --badge-fg:{palette['absolute_light']};
  --badge-line:{palette['primary_solid']};
  /* charts only — see color.chart.accent in brand.json */
  --accent:{accent_light};
  --logo-light:1; --logo-dark:0;
  --sans:'Geist',ui-sans-serif,system-ui,sans-serif;
  --mono:'Geist Mono',ui-monospace,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ink:{palette['absolute_light']};
    --body:{palette['light_gray']};
    --muted:{muted_dark};
    --ground:{palette['primary_solid']};
    --surface:{palette['charcoal']};
    --line:#2E2E2E;
    --line-strong:{palette['absolute_light']};
    --inverse-bg:{palette['light_gray']};
    --inverse-fg:{palette['primary_solid']};
    --bar:{muted_dark};
    --bar-us:{palette['absolute_light']};
    --badge-bg:{palette['charcoal']};
    --badge-fg:{palette['absolute_light']};
    --badge-line:#3A3A3A;
    --accent:{accent_dark};
    --logo-light:0; --logo-dark:1;
  }}
}}
:root[data-theme="dark"] {{
  --ink:{palette['absolute_light']};
  --body:{palette['light_gray']};
  --muted:{muted_dark};
  --ground:{palette['primary_solid']};
  --surface:{palette['charcoal']};
  --line:#2E2E2E;
  --line-strong:{palette['absolute_light']};
  --inverse-bg:{palette['light_gray']};
  --inverse-fg:{palette['primary_solid']};
  --bar:{muted_dark};
  --bar-us:{palette['absolute_light']};
  --badge-bg:{palette['charcoal']};
  --badge-fg:{palette['absolute_light']};
  --badge-line:#3A3A3A;
  --accent:{accent_dark};
  --logo-light:0; --logo-dark:1;
}}

@font-face {{ font-family:'Geist'; src:url({sans_uri}) format('truetype-variations');
  font-weight:100 900; font-style:normal; font-display:block; }}
@font-face {{ font-family:'Geist Mono'; src:url({mono_uri}) format('truetype-variations');
  font-weight:100 900; font-style:normal; font-display:block; }}

*,*::before,*::after {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--ground); color:var(--body);
  font-family:var(--sans); font-size:16px; line-height:1.65;
  font-variant-numeric:tabular-nums;
  -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1080px; margin:0 auto; padding:0 24px 96px; }}

h1,h2,h3,h4 {{ color:var(--ink); margin:0; text-wrap:balance; }}
h1 {{ font-size:clamp(34px,6vw,58px); font-weight:700; line-height:1.05; letter-spacing:-.03em; }}
h2 {{ font-size:clamp(21px,3vw,27px); font-weight:600; letter-spacing:-.015em; line-height:1.2; }}
h3 {{ font-size:18px; font-weight:600; }}
h4 {{ font-size:14px; font-weight:600; }}
p {{ margin:0 0 .8em; }}
a {{ color:inherit; }}
:focus-visible {{ outline:2px solid var(--ink); outline-offset:3px; }}

.mono {{ font-family:var(--mono); font-variant-ligatures:none; }}
.eyebrow {{
  font-family:var(--mono); font-size:11px; font-weight:500;
  letter-spacing:.14em; text-transform:uppercase; color:var(--muted);
  margin:0 0 8px;
}}
.intro {{ max-width:68ch; color:var(--body); }}
.caption {{ font-size:12px; color:var(--muted); margin:10px 0 26px; max-width:68ch; }}

/* masthead */
.top {{
  display:flex; align-items:center; justify-content:space-between; gap:20px;
  padding:26px 0 20px; flex-wrap:wrap;
}}
.logo {{ height:42px; width:auto; display:block; }}
.logo-l {{ opacity:var(--logo-light); }}
.logo-d {{ opacity:var(--logo-dark); margin-top:-42px; }}
.hero {{ border-top:2px solid var(--line-strong); padding:34px 0 30px; }}
.hero h1 {{ max-width:16ch; margin-bottom:18px; }}
.hero .sub {{ font-size:clamp(16px,2vw,19px); color:var(--muted); max-width:60ch; }}
.facts {{
  display:flex; flex-wrap:wrap; gap:34px; border-top:1px solid var(--line);
  padding-top:16px; margin-top:26px;
}}
.fact .v {{ font-size:22px; font-weight:600; color:var(--ink); display:block; }}

/* nav */
nav.rail {{
  position:sticky; top:0; z-index:5; background:var(--ground);
  border-bottom:1px solid var(--line); margin-bottom:34px;
  display:flex; gap:20px; overflow-x:auto; padding:12px 0;
  font-family:var(--mono); font-size:11.5px; letter-spacing:.08em; text-transform:uppercase;
}}
nav.rail a {{ color:var(--muted); text-decoration:none; white-space:nowrap; }}
nav.rail a:hover {{ color:var(--ink); }}

/* lead */
.lead {{
  background:var(--inverse-bg); color:var(--inverse-fg);
  padding:30px 32px; margin-bottom:44px;
}}
.lead .eyebrow {{ color:inherit; opacity:.65; }}
.lead-claim {{ font-size:clamp(19px,2.6vw,25px); font-weight:600; line-height:1.3; max-width:34ch; }}
.lead-so {{ opacity:.82; max-width:62ch; margin-bottom:0; }}
.lead .sources a {{ color:inherit; opacity:.6; }}

section {{ margin-bottom:60px; scroll-margin-top:64px; }}
.shead {{
  display:flex; align-items:baseline; gap:14px;
  border-top:2px solid var(--line-strong); padding-top:10px; margin-bottom:20px;
}}
/* Bold + full-strength ink (not the muted eyebrow grey) so each section's
   leading number reads as a distinct marker, not a caption. text-transform
   is a no-op on digits but is included so the rule matches should this ever
   carry a lettered prefix. */
.snum {{
  font-size:15px; font-weight:700; color:var(--ink);
  letter-spacing:.1em; text-transform:uppercase;
}}

/* findings */
.findings, .recs {{ list-style:none; margin:0; padding:0; }}
.finding {{ display:flex; gap:16px; padding:16px 0; border-bottom:1px solid var(--line); }}
.finding:last-child {{ border-bottom:none; }}
.fnum {{ font-family:var(--mono); font-size:12px; color:var(--muted); padding-top:3px; }}
.fbody {{ flex:1; min-width:0; }}
.fclaim {{ font-weight:600; color:var(--ink); margin-bottom:5px; max-width:70ch; }}
.fso {{ max-width:70ch; margin-bottom:0; }}
.so-label {{ font-weight:600; color:var(--ink); }}
.so-label::after {{ content:" — "; }}
.evidence {{ font-size:14px; color:var(--muted); margin:6px 0 0; max-width:70ch; }}

.sources {{ display:flex; flex-direction:column; gap:2px; margin-top:8px; }}
.src {{
  font-family:var(--mono); font-size:11.5px; color:var(--muted);
  text-decoration:none; word-break:break-all;
}}
.src:hover {{ color:var(--ink); text-decoration:underline; }}
.src-date::before {{ content:" ("; }}
.src-date::after {{ content:")"; }}

.inferred {{
  font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--muted); border:1px dashed var(--muted); padding:1px 6px;
  margin-left:8px; white-space:nowrap;
}}
.placeholder {{
  font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--badge-fg); background:var(--badge-bg); border:1px solid var(--ink);
  padding:1px 6px; margin-left:8px; white-space:nowrap; font-weight:600;
}}
.warnbar {{
  border:1.5px solid var(--ink); padding:12px 16px; margin:0 0 20px;
  display:flex; gap:12px; align-items:baseline; flex-wrap:wrap;
}}
.warnbar .tagline {{
  font-family:var(--mono); font-size:10.5px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--ink); font-weight:600; white-space:nowrap;
}}
.warnbar p {{ margin:0; font-size:14px; }}

/* panels */
.panel {{ background:var(--surface); padding:18px 20px; margin:16px 0; }}
.panel ul {{ margin:0; padding-left:18px; }}
.panel li {{ margin-bottom:6px; }}
.panel-dark {{ background:var(--inverse-bg); color:var(--inverse-fg); }}
.panel-dark .eyebrow {{ color:inherit; opacity:.65; }}
.panel-dark h4 {{ color:inherit; }}

.tiers {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; }}
.tier {{ background:var(--surface); padding:16px 18px; }}
.tier p {{ margin:0; color:var(--ink); font-weight:500; }}

/* bars */
.bars {{ display:flex; flex-direction:column; gap:11px; margin:8px 0 0; }}
.bar-row {{ display:grid; grid-template-columns:132px 1fr 78px; align-items:center; gap:14px; }}
.bar-label {{ font-size:14px; color:var(--body); text-align:right; }}
.bar-track {{ height:26px; background:transparent; }}
.bar-fill {{ height:100%; background:var(--bar); }}
.bar-value {{ font-family:var(--mono); font-size:13px; color:var(--muted); }}
.bar-row.is-us .bar-fill {{ background:var(--bar-us); }}
.bar-row.is-us .bar-label,
.bar-row.is-us .bar-value {{ color:var(--ink); font-weight:600; }}

/* chart shell */
.chart-title {{
  font-size:15px; font-weight:600; margin:30px 0 4px; color:var(--ink);
}}
.chart {{ margin:12px 0 6px; }}
.chart svg {{ width:100%; height:auto; display:block; overflow:visible; }}
.insight {{
  border-left:2px solid var(--ink); padding-left:14px; margin:4px 0 26px;
  max-width:70ch; color:var(--ink); font-weight:500;
}}

/* cost curve */
.cg-grid {{ stroke:var(--line); stroke-width:1; }}
.cg-tick {{ fill:var(--muted); font-family:var(--mono); font-size:10.5px; }}
.cg-axis {{ fill:var(--muted); font-family:var(--mono); font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; }}
.cg-line {{ fill:none; stroke:var(--muted); stroke-width:2; stroke-linejoin:round; }}
.cg-line.cg-us {{ stroke:var(--accent); stroke-width:3.2; }}
.cg-dot {{ fill:var(--muted); stroke:none; }}
.cg-line.cg-us.cg-dot {{ fill:var(--accent); }}
.cg-label {{ fill:var(--body); font-family:var(--sans); font-size:12px; font-weight:600; }}
/* Label stays on ink, not the accent: these are ~12px and the accent is tuned
   for graphical objects (3:1), not text (4.5:1). The line carries the colour. */
.cg-us-label {{ fill:var(--ink); font-weight:700; }}
.cg-endcost {{ fill:var(--muted); font-family:var(--mono); font-size:10.5px; }}
.cg-cross {{ stroke:var(--accent); stroke-width:1.2; stroke-dasharray:3 3; opacity:.7; }}
.cg-crossdot {{ fill:var(--ground); stroke:var(--accent); stroke-width:2.8; }}
.cg-crosslabel {{ fill:var(--ink); font-family:var(--sans); font-size:11.5px; font-weight:700; }}
.cg-crosssub {{ fill:var(--muted); font-family:var(--mono); font-size:10px; }}

/* price positioning — grouped bar chart, banded so the mid tier reads as its own block */
.price-bands {{ display:flex; flex-direction:column; gap:18px; margin:12px 0 0; }}
.band {{ padding:14px 16px; border:1px solid var(--line); }}
.band.our-band {{ border-color:var(--ink); border-width:1.5px; background:var(--surface); }}
.band-head {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; margin-bottom:2px; }}
.band-name {{ font-size:15px; font-weight:700; color:var(--ink); }}
.band-range {{ font-size:12px; color:var(--muted); }}
.band-tag {{
  font-size:10px; letter-spacing:.1em; text-transform:uppercase; font-weight:700;
  color:var(--ground); background:var(--accent); padding:2px 7px;
}}
.band-note {{ font-size:12.5px; color:var(--muted); margin:0 0 12px; }}
.pbar-row {{ display:grid; grid-template-columns:145px 1fr 62px; gap:14px; align-items:center; margin-bottom:8px; }}
.pbar-row:last-child {{ margin-bottom:0; }}
.pbar-name {{ font-size:13.5px; color:var(--body); text-align:right; line-height:1.25; }}
.pbar-detail {{ display:block; font-size:10.5px; color:var(--muted); font-family:var(--mono); }}
.pbar-track {{ height:20px; }}
.pbar-fill {{ height:100%; background:var(--muted); min-width:2px; }}
.pbar-val {{ font-size:12.5px; color:var(--muted); text-align:right; }}
.pbar-row.is-us .pbar-fill {{ background:var(--accent); }}
.pbar-row.is-us .pbar-name, .pbar-row.is-us .pbar-val {{ color:var(--ink); font-weight:700; }}
.axis-label {{
  font-size:10.5px; color:var(--muted); margin:12px 0 0 159px;
  letter-spacing:.08em; text-transform:uppercase;
}}

/* capability table — words, not icons */
.cap-table td.ctr {{ font-size:13px; }}
.cap-table .cap-yes {{ color:var(--ink); font-weight:700; }}
.cap-table .cap-partial, .cap-table .cap-no {{ color:var(--muted); }}
.cap-table th.col-us, .cap-table td.col-us {{ background:var(--surface); }}
.cap-table th.col-us {{ color:var(--ink); }}

/* chart header block */
.chart-question {{
  font-size:14px; color:var(--body); margin:0 0 2px; font-weight:500;
}}
.chart-date {{ font-size:11px; color:var(--muted); margin:0 0 4px; }}

/* tables */
.scroller {{ overflow-x:auto; margin:18px 0; }}
table {{ width:100%; border-collapse:collapse; font-size:14.5px; min-width:560px; }}
thead th {{
  text-align:left; font-family:var(--mono); font-size:10.5px; font-weight:500;
  letter-spacing:.12em; text-transform:uppercase; color:var(--muted);
  border-bottom:2px solid var(--line-strong); padding:0 14px 8px 0; vertical-align:bottom;
  /* uppercase + .12em tracking makes headers wide; without nowrap "MID TIER"
     wraps to two lines and squeezes the column below it. The table already
     scrolls horizontally, so nowrap cannot break the page. */
  white-space:nowrap;
}}
.pricing-table {{ min-width:720px; }}
.pricing-table .c-company {{ width:14%; }}
.pricing-table .c-model {{ width:13%; }}
.pricing-table .c-num {{ width:10%; text-align:right; }}
.cap-table {{ min-width:640px; }}
tbody td {{ padding:11px 14px 11px 0; border-bottom:1px solid var(--line); vertical-align:top; }}
tbody tr:last-child td {{ border-bottom:none; }}
td.strong {{ color:var(--ink); font-weight:600; }}
td.num {{ font-family:var(--mono); text-align:right; white-space:nowrap; }}
th:last-child, td:last-child {{ padding-right:0; }}
tr.is-us td {{ background:var(--surface); color:var(--ink); font-weight:600; }}
td.break a {{ word-break:break-all; color:var(--muted); text-decoration:none; }}
td.break a:hover {{ color:var(--ink); text-decoration:underline; }}
.ctr {{ text-align:center; }}

/* matrix marks — monochrome, shape carries meaning, never colour */
.mark {{ display:inline-block; width:13px; height:13px; border-radius:50%; vertical-align:-2px; }}
.mark-yes {{ background:var(--ink); }}
.mark-partial {{ background:linear-gradient(90deg,var(--ink) 50%,transparent 50%); border:1.5px solid var(--ink); }}
.mark-no {{ border:1.5px solid var(--muted); }}
.legend {{ font-family:var(--mono); font-size:11.5px; color:var(--muted); display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
.legend .mark {{ margin-left:14px; }}
.legend .mark:first-child {{ margin-left:0; }}

/* competitors */
.comp {{ padding:22px 0; border-bottom:1px solid var(--line); }}
.comp:last-child {{ border-bottom:none; }}
.comp-head {{ display:flex; align-items:baseline; gap:12px; justify-content:space-between; }}
.comp-line {{ margin:8px 0 4px; max-width:72ch; }}
.cols {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(255px,1fr)); gap:22px; margin-top:12px; }}
.cols-gap {{ gap:16px; }}
.cols h4 {{
  font-family:var(--mono); font-size:10.5px; font-weight:500; letter-spacing:.12em;
  text-transform:uppercase; color:var(--muted); margin-bottom:7px;
}}
.cols ul {{ margin:0; padding-left:18px; }}
.cols li {{ margin-bottom:5px; }}

.chip {{
  font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
  border:1px solid var(--line); padding:3px 8px; white-space:nowrap;
}}
/* The leverage chip is the one that should catch the eye. It earns that with a
   solid border and full-strength ink, not a fill — a filled chip at this size
   reads as a button, and in dark mode as another white box. */
.chip-lev {{ border-color:var(--ink); color:var(--ink); font-weight:600; }}
.chips {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }}

/* recommendations */
.rec {{ display:flex; gap:16px; padding:18px 0; border-bottom:1px solid var(--line); }}
.rec:last-child {{ border-bottom:none; }}
.rnum {{
  flex:0 0 auto; width:30px; height:30px;
  background:var(--badge-bg); color:var(--badge-fg); border:1px solid var(--badge-line);
  font-family:var(--mono); font-size:13px; font-weight:600;
  display:flex; align-items:center; justify-content:center;
}}
.rbody {{ flex:1; min-width:0; }}
.rtitle {{ font-weight:600; color:var(--ink); margin-bottom:4px; }}
.rbody p {{ max-width:70ch; }}

footer {{ border-top:1px solid var(--line); padding-top:20px; margin-top:20px; }}
footer .mono {{ font-size:11.5px; color:var(--muted); }}

@media (max-width:640px) {{
  .bar-row {{ grid-template-columns:104px 1fr 66px; gap:10px; }}
  .bar-label {{ font-size:13px; }}
  .lead {{ padding:24px 20px; }}
}}
@media (prefers-reduced-motion:reduce) {{
  * {{ animation:none !important; transition:none !important; scroll-behavior:auto !important; }}
}}
"""

    return f"""<title>{e(brand['brand']['name'])} — Competitor Analysis, {e(month)}</title>
<style>{css}</style>
<div class="wrap">
  <header class="top">
    <div>
      <img class="logo logo-l" src="{logo_light}" alt="{e(brand['brand']['name'])}">
      <img class="logo logo-d" src="{logo_dark}" alt="" aria-hidden="true">
    </div>
    <span class="eyebrow" style="margin:0">Competitor analysis &middot; {e(month)}</span>
  </header>

  <div class="hero">
    <h1>{e(meta.get('title'))}</h1>
    <p class="sub">{e(meta.get('subtitle'))}</p>
    <div class="facts">
      <div class="fact"><span class="eyebrow">Competitors</span><span class="v">{e(meta.get('competitor_count'))}</span></div>
      <div class="fact"><span class="eyebrow">Pages captured</span><span class="v">{e(meta.get('pages_tracked'))}</span></div>
      <div class="fact"><span class="eyebrow">Run type</span><span class="v">{'Baseline' if meta.get('run_type') == 'baseline' else 'Monthly'}</span></div>
      <div class="fact"><span class="eyebrow">Generated</span><span class="v">{datetime.now().strftime('%d %b %Y')}</span></div>
    </div>
  </div>

  <nav class="rail" aria-label="Sections">{navlinks}</nav>

  {lead_html}
  {''.join(sections)}

  <footer>
    <p class="mono">{e(brand['brand']['name'])} internal competitive intelligence &middot;
    generated {datetime.now().strftime('%d %b %Y')} &middot; every claim sourced above</p>
  </footer>
</div>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--brand-json", default="brand/brand.json")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        log(f"ERROR: {data_path} not found")
        return 1

    data = json.loads(data_path.read_text(encoding="utf-8"))
    brand = json.loads(Path(args.brand_json).read_text(encoding="utf-8"))

    html_out = build(data, brand, Path(args.root))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_out, encoding="utf-8")
    log(f"wrote {out} ({len(html_out) / 1024:,.0f} KB)")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
