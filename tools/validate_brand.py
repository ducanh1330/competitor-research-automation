"""Validate brand/brand.json against the DOZ.AI brand system.

This is the gate that stops a malformed brand file reaching the renderer. It
does not trust the values recorded in brand.json — contrast ratios are
recomputed from the hex codes, and font embedding permission is read out of the
font binary rather than from the `embeddable` flag. A file that merely *claims*
to be correct fails here.

Checks:
  1. Required structure is present.
  2. Every hex code parses.
  3. color.tokens reference real palette entries (no raw hex, no dangling names).
  4. Contrast ratios recomputed via WCAG 2.x relative luminance; AA (4.5:1) enforced.
  5. Every referenced logo and font file exists on disk.
  6. Font fsType read from the OS/2 table — restricted fonts cannot be embedded.
  7. Chart series marked "brand" provenance actually appear in the palette.
  8. Strict-monochrome rule: every colour is a true grey (R == G == B).

Exit codes: 0 clean (warnings allowed), 1 one or more errors.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
AA_NORMAL = 4.5
AA_LARGE = 3.0

FSTYPE_MEANING = {
    0: "Installable Embedding — permitted",
    2: "Restricted License Embedding — FORBIDDEN",
    4: "Preview & Print Embedding",
    8: "Editable Embedding",
}

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


# --------------------------------------------------------------------------- #
# colour maths
# --------------------------------------------------------------------------- #

def parse_hex(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def relative_luminance(hex_value: str) -> float:
    """WCAG 2.x relative luminance."""
    out = []
    for channel in parse_hex(hex_value):
        c = channel / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = out
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    lo, hi = sorted((l1, l2))
    return (hi + 0.05) / (lo + 0.05)


def is_grey(hex_value: str) -> bool:
    r, g, b = parse_hex(hex_value)
    return r == g == b


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #

def check_structure(brand: dict) -> None:
    for key in ("brand", "rules", "color", "typography", "logo"):
        if key not in brand:
            err(f"missing top-level section: {key!r}")
    color = brand.get("color", {})
    for key in ("palette", "tokens"):
        if key not in color:
            err(f"missing color.{key}")
    if "families" not in brand.get("typography", {}):
        err("missing typography.families")
    if "scale" not in brand.get("typography", {}):
        err("missing typography.scale")
    if "variants" not in brand.get("logo", {}):
        err("missing logo.variants")


def check_palette(brand: dict) -> dict[str, str]:
    """Validate hex codes, return {token_name: hex} including aliases."""
    palette = brand.get("color", {}).get("palette", {})
    if not palette:
        err("color.palette is empty")
        return {}

    resolved: dict[str, str] = {}
    strict = brand.get("rules", {}).get("strict_monochrome", False)

    for name, entry in palette.items():
        hex_value = entry.get("hex") if isinstance(entry, dict) else None
        if not hex_value:
            err(f"palette entry {name!r} has no 'hex'")
            continue
        if not HEX_RE.match(hex_value):
            err(f"palette entry {name!r} has malformed hex: {hex_value!r}")
            continue
        if strict and not is_grey(hex_value):
            err(
                f"palette entry {name!r} = {hex_value} is not a true grey (R!=G!=B), "
                f"violating rules.strict_monochrome"
            )
        resolved[name] = hex_value.upper()
        for alias in entry.get("aliases", []):
            if alias in resolved and resolved[alias] != hex_value.upper():
                err(f"alias {alias!r} maps to two different colours")
            resolved[alias] = hex_value.upper()

    return resolved


def check_tokens(brand: dict, resolved: dict[str, str]) -> None:
    tokens = brand.get("color", {}).get("tokens", {})
    for token, target in tokens.items():
        if token.startswith("_"):
            continue
        if not isinstance(target, str):
            err(f"token {token!r} is not a string reference")
            continue
        if HEX_RE.match(target):
            err(
                f"token {token!r} contains a raw hex ({target}). Tokens must reference "
                f"a palette entry by name so the palette stays the single source of truth."
            )
        elif target not in resolved:
            err(f"token {token!r} references unknown palette entry {target!r}")


def check_contrast(brand: dict, resolved: dict[str, str]) -> None:
    """Recompute every documented pair; never trust the stored ratio."""
    declared = brand.get("color", {}).get("contrast_verified", [])
    if not declared:
        warn("no color.contrast_verified pairs declared — contrast is unchecked")

    for pair in declared:
        fg, bg = pair.get("fg"), pair.get("bg")
        if not (fg and bg and HEX_RE.match(fg) and HEX_RE.match(bg)):
            err(f"malformed contrast pair: {pair}")
            continue
        actual = contrast_ratio(fg, bg)
        claimed = pair.get("ratio")
        if claimed is not None and abs(actual - float(claimed)) > 0.15:
            err(
                f"contrast claim wrong for {fg} on {bg}: file says {claimed}:1, "
                f"actual is {actual:.2f}:1"
            )
        if actual < AA_NORMAL:
            level = "FAILS AA"
            if actual >= AA_LARGE:
                level = "AA large-text only"
                warn(f"{fg} on {bg} = {actual:.2f}:1 — {level}; not safe for body copy")
            else:
                err(f"{fg} on {bg} = {actual:.2f}:1 — {level} (needs {AA_NORMAL}:1)")

    # the token pairs that actually matter for the report body
    tokens = brand.get("color", {}).get("tokens", {})
    bg_name = tokens.get("page_background")
    for fg_token in ("text_primary", "text_body", "text_muted"):
        fg_name = tokens.get(fg_token)
        if fg_name in resolved and bg_name in resolved:
            ratio = contrast_ratio(resolved[fg_name], resolved[bg_name])
            if ratio < AA_NORMAL:
                err(
                    f"token {fg_token} ({resolved[fg_name]}) on page_background "
                    f"({resolved[bg_name]}) = {ratio:.2f}:1, below AA {AA_NORMAL}:1"
                )


def check_chart(brand: dict, resolved: dict[str, str]) -> None:
    chart = brand.get("color", {}).get("chart")
    if not chart:
        return

    palette_hexes = set(resolved.values())
    strict = brand.get("rules", {}).get("strict_monochrome", False)

    for hex_value in chart.get("series", []):
        if not HEX_RE.match(hex_value):
            err(f"chart series contains malformed hex: {hex_value!r}")
            continue
        if strict and not is_grey(hex_value):
            err(f"chart series colour {hex_value} is not a true grey, violating strict_monochrome")

    # provenance honesty: anything claimed as "brand" must really be in the palette
    for hex_value, origin in chart.get("series_provenance", {}).items():
        if origin == "brand" and hex_value.upper() not in palette_hexes:
            err(
                f"chart colour {hex_value} claims provenance 'brand' but is not in color.palette"
            )
        if origin == "derived" and hex_value.upper() in palette_hexes:
            warn(f"chart colour {hex_value} is marked 'derived' but exists in the palette")

    if chart.get("status") == "proposed-extension" and not chart.get("approved_by_user"):
        warn(
            "color.chart is a proposed extension to the brand guide and is not yet "
            "user-approved. Charts must not ship until approved_by_user is true."
        )

    # A chart accent is the one permitted exception to strict monochrome, and only
    # with recorded user authorisation. An accent that appears without approval is
    # a violation, not a preference — that is exactly what this check exists for.
    accent = chart.get("accent")
    if accent:
        if not accent.get("approved_by_user"):
            err(
                "color.chart.accent breaks rules.strict_monochrome without "
                "approved_by_user: true. Either remove it or record the authorisation."
            )
        else:
            for key in ("light", "dark"):
                value = accent.get(key)
                if not value:
                    err(f"color.chart.accent.{key} is missing — both themes need a value")
                    continue
                if not HEX_RE.match(value):
                    err(f"color.chart.accent.{key} is malformed: {value!r}")
                    continue
                # Graphical objects need 3:1 against their ground (WCAG 1.4.11).
                ground = "#FFFFFF" if key == "light" else "#000000"
                ratio = contrast_ratio(value, ground)
                if ratio < 3.0:
                    err(
                        f"chart accent {value} on {ground} is {ratio:.2f}:1 — below the "
                        f"3:1 needed for graphical objects. It will disappear in {key} mode."
                    )
            if not accent.get("approved_on"):
                warn("color.chart.accent is approved but has no approved_on date")
            warn(
                f"color.chart.accent is an authorised EXCEPTION to strict_monochrome "
                f"(scope: {accent.get('scope', 'unspecified')}). Verify it appears in "
                f"charts only — never in body copy, rules, panels or logos."
            )


def check_assets(brand: dict, root: Path) -> None:
    for name, variant in brand.get("logo", {}).get("variants", {}).items():
        rel = variant.get("file")
        if not rel:
            err(f"logo variant {name!r} has no 'file'")
            continue
        if not (root / rel).exists():
            err(f"logo variant {name!r} points at a missing file: {rel}")

    source = brand.get("logo", {}).get("source_supplied")
    if source and not (root / source).exists():
        warn(f"original supplied logo missing: {source}")


def check_fonts(brand: dict, root: Path) -> None:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        warn("fontTools unavailable — font embedding permission not verified")
        return

    for role, family in brand.get("typography", {}).get("families", {}).items():
        rel = family.get("file")
        if not rel:
            err(f"typography family {role!r} has no 'file'")
            continue
        path = root / rel
        if not path.exists():
            err(f"typography family {role!r} points at a missing font: {rel}")
            continue

        try:
            font = TTFont(str(path))
        except Exception as exc:  # noqa: BLE001 - surface the real parse error
            err(f"could not parse font {rel}: {exc}")
            continue

        fs_type = font["OS/2"].fsType & 0x000F
        meaning = FSTYPE_MEANING.get(fs_type, f"unknown fsType {fs_type}")
        if fs_type == 2:
            err(
                f"font {rel} has fsType 2 ({meaning}). It cannot legally be embedded "
                f"in the PDF — pick a licensed substitute rather than shipping a report "
                f"that silently renders in a fallback face."
            )
        elif fs_type not in (0, 4, 8):
            warn(f"font {rel} has unexpected fsType {fs_type}")

        if family.get("embeddable") is True and fs_type == 2:
            err(f"font {rel} is marked embeddable:true in brand.json but the binary says otherwise")

        declared = family.get("name")
        actual = {r.nameID: str(r) for r in font["name"].names if r.platformID == 3}.get(1)
        if declared and actual and declared.lower() != actual.lower():
            warn(f"font {rel}: brand.json calls it {declared!r}, the file reports {actual!r}")


def check_provenance(brand: dict) -> None:
    """Surface every value that was inferred rather than extracted."""
    proposed: list[str] = []
    for key, entry in brand.get("typography", {}).get("scale", {}).items():
        if isinstance(entry, dict) and entry.get("provenance"):
            proposed.append(f"typography.scale.{key}")
    for section in ("minimum_size",):
        if brand.get("logo", {}).get(section, {}).get("status") == "proposed":
            proposed.append(f"logo.{section}")
    if brand.get("page", {}).get("status") == "proposed":
        proposed.append("page")
    if proposed:
        warn(
            "values not present in the brand guide (inferred, flagged for review): "
            + ", ".join(proposed)
        )


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand-json", default="brand/brand.json", help="path to brand.json")
    ap.add_argument("--root", default=".", help="repo root that asset paths are relative to")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args()

    path = Path(args.brand_json)
    root = Path(args.root)

    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1

    try:
        brand = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: {path} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    check_structure(brand)
    resolved = check_palette(brand)
    check_tokens(brand, resolved)
    check_contrast(brand, resolved)
    check_chart(brand, resolved)
    check_assets(brand, root)
    check_fonts(brand, root)
    check_provenance(brand)

    for w in warnings:
        print(f"WARN  {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)

    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s) — brand.json is NOT usable", file=sys.stderr)
        return 1

    if warnings and args.strict:
        print(f"\n{len(warnings)} warning(s) and --strict given — failing", file=sys.stderr)
        return 1

    print(
        f"\nbrand.json OK — {len(resolved)} colour names resolved, "
        f"{len(warnings)} warning(s)",
        file=sys.stderr,
    )
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
