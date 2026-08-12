"""Generate static weight instances from the Geist variable fonts.

Chromium handles variable fonts natively, so the HTML side needs nothing. But
matplotlib's font manager only ever sees a variable font's default instance —
asking it for bold silently yields regular, which means chart emphasis is lost
without any error. Emphasis in a monochrome report carries real meaning (it is
how DOZ.AI's own series is distinguished), so a silent fallback is not
acceptable.

This pins the weights the brand actually uses into standalone TTFs.

Re-run only if the variable fonts are replaced.

Usage:
    uv run tools/build_font_instances.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Weight, suffix. Matches the weights used by typography.scale in brand.json.
INSTANCES = [
    (400, "Regular"),
    (600, "SemiBold"),
    (700, "Bold"),
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def build(source: Path, out_dir: Path, base_name: str) -> list[dict]:
    from fontTools import ttLib
    from fontTools.varLib import instancer

    made = []
    for weight, suffix in INSTANCES:
        font = ttLib.TTFont(str(source))
        instancer.instantiateVariableFont(font, {"wght": weight}, inplace=True, updateFontNames=True)

        out_path = out_dir / f"{base_name}-{suffix}.ttf"
        font.save(str(out_path))

        # Confirm the instance really reports the weight we asked for; a wrong
        # OS/2 usWeightClass is exactly what makes matplotlib fall back again.
        check = ttLib.TTFont(str(out_path))
        actual = check["OS/2"].usWeightClass
        status = "ok" if actual == weight else f"MISMATCH (reports {actual})"
        log(f"  {out_path.name}: wght={weight} -> usWeightClass={actual} [{status}]")
        if actual != weight:
            raise SystemExit(f"ERROR: {out_path.name} reports weight {actual}, expected {weight}")

        made.append({
            "weight": weight,
            "style": suffix,
            "file": str(out_path).replace("\\", "/"),
        })
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand-json", default="brand/brand.json")
    ap.add_argument("--out-dir", default="brand/fonts/static")
    args = ap.parse_args()

    brand_path = Path(args.brand_json)
    brand = json.loads(brand_path.read_text(encoding="utf-8"))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {}
    for role, family in brand["typography"]["families"].items():
        source = Path(family["file"])
        if not source.exists():
            log(f"ERROR: variable font missing: {source}")
            return 1
        base = source.stem.replace("-Variable", "")
        log(f"{family['name']} ({role}) from {source.name}")
        manifest[role] = build(source, out_dir, base)

    manifest_path = out_dir / "instances.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log(f"\nwrote {manifest_path}")

    print(manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
