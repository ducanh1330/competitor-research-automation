"""Derive logo variants from the supplied DOZ.AI lockup PNG.

The brand guide defines a primary lockup (symbol above wordmark) and a secondary
symbol-only mark, but only the lockup was supplied as a file. This splits the
lockup at the whitespace band between symbol and wordmark, trims each to its ink
bounds, and emits transparent + reversed (white) variants for use on dark panels.

It also measures the height of the dot in the `.AI` wordmark, which is the unit
the guide uses to define minimum clear space. That ratio is written to
brand_metrics.json so brand.json / the CSS can derive padding from the logo's
own geometry rather than a hardcoded guess.

Re-run this if brand/logo/dozai-lockup.png is ever replaced.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

INK_THRESHOLD = 128  # grayscale value below which a pixel counts as ink
MIN_GAP = 10  # px; ignore empty bands thinner than this when splitting


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def ink_mask(gray: np.ndarray) -> np.ndarray:
    return gray < INK_THRESHOLD


def bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) inclusive bounds of True pixels."""
    ys, xs = np.where(mask)
    if len(ys) == 0:
        raise ValueError("image contains no ink")
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def find_split(mask: np.ndarray, top: int, bottom: int) -> int | None:
    """Find the largest empty horizontal band between symbol and wordmark."""
    rows = mask.any(axis=1)
    bands: list[tuple[int, int]] = []
    start = None
    for y in range(top, bottom + 1):
        if not rows[y]:
            if start is None:
                start = y
        elif start is not None:
            if y - start >= MIN_GAP:
                bands.append((start, y - 1))
            start = None
    if not bands:
        return None
    widest = max(bands, key=lambda b: b[1] - b[0])
    return (widest[0] + widest[1]) // 2


def measure_dot(mask: np.ndarray, wm_top: int, wm_bottom: int) -> int | None:
    """Height of the period in `.AI` — the guide's clear-space unit.

    Glyphs are separated by splitting the wordmark band at empty columns, then
    the period is identified as the one that is short (well under cap height)
    and roughly square. Measuring per-column instead of per-glyph picks up
    anti-aliasing artifacts at letter junctions, which reads several glyphs as
    one and overstates the dot — hence the grouping.
    """
    cap_height = wm_bottom - wm_top + 1
    band = mask[wm_top : wm_bottom + 1, :]
    cols = band.any(axis=0)

    groups: list[tuple[int, int]] = []
    start = None
    for x in range(len(cols)):
        if cols[x] and start is None:
            start = x
        elif not cols[x] and start is not None:
            groups.append((start, x - 1))
            start = None
    if start is not None:
        groups.append((start, len(cols) - 1))

    for x0, x1 in groups:
        sub = band[:, x0 : x1 + 1]
        ys = np.where(sub.any(axis=1))[0]
        h = int(ys.max() - ys.min() + 1)
        w = x1 - x0 + 1
        is_short = h < cap_height * 0.5
        is_square = 0.7 <= w / h <= 1.4
        sits_low = ys.min() > cap_height * 0.5
        if is_short and is_square and sits_low:
            return h
    return None


def to_rgba(gray: np.ndarray, box: tuple[int, int, int, int], white: bool) -> Image.Image:
    """Crop to box and build an RGBA image with ink coverage as alpha.

    Alpha comes from ink coverage rather than a hard cutout, so anti-aliased
    edges survive and the mark stays crisp when scaled down in the PDF.
    """
    left, top, right, bottom = box
    crop = gray[top : bottom + 1, left : right + 1]
    alpha = (255 - crop).astype(np.uint8)
    fill = 255 if white else 0
    rgb = np.full((*crop.shape, 3), fill, dtype=np.uint8)
    return Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="brand/logo/dozai-lockup.png", help="supplied lockup PNG")
    ap.add_argument("--out-dir", default="brand/logo", help="where variants are written")
    ap.add_argument("--metrics", default="brand/brand_metrics.json", help="measured geometry output")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        log(f"ERROR: source logo not found: {src}")
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    gray = np.array(Image.open(src).convert("L"))
    mask = ink_mask(gray)
    left, top, right, bottom = bbox(mask)
    log(f"source {src.name}: {gray.shape[1]}x{gray.shape[0]}, ink bbox=({left},{top})-({right},{bottom})")

    split = find_split(mask, top, bottom)
    if split is None:
        log("ERROR: no whitespace band found between symbol and wordmark — cannot split lockup")
        return 1
    log(f"split at y={split}")

    # symbol occupies everything above the split; wordmark everything below
    sym_mask = mask.copy()
    sym_mask[split:, :] = False
    wm_mask = mask.copy()
    wm_mask[:split, :] = False

    sym_box = bbox(sym_mask)
    wm_box = bbox(wm_mask)
    log(f"symbol bbox={sym_box}  wordmark bbox={wm_box}")

    dot = measure_dot(mask, wm_box[1], wm_box[3])
    if dot is None:
        log("WARNING: could not measure the .AI dot — clear space falls back to 12% of mark height")
    else:
        log(f"measured .AI dot height = {dot}px")

    lockup_box = (left, top, right, bottom)
    variants = {
        "dozai-lockup-trimmed.png": (lockup_box, False),
        "dozai-lockup-reversed.png": (lockup_box, True),
        "dozai-symbol.png": (sym_box, False),
        "dozai-symbol-reversed.png": (sym_box, True),
    }
    for name, (box, white) in variants.items():
        img = to_rgba(gray, box, white)
        path = out_dir / name
        img.save(path)
        log(f"wrote {path}  ({img.width}x{img.height})")

    lockup_h = bottom - top + 1
    metrics = {
        "source": str(src).replace("\\", "/"),
        "source_size": [int(gray.shape[1]), int(gray.shape[0])],
        "lockup_bbox": list(lockup_box),
        "symbol_bbox": list(sym_box),
        "wordmark_bbox": list(wm_box),
        "dot_height_px": dot,
        "lockup_height_px": lockup_h,
        # Clear space = one dot height, expressed as a fraction of the mark's own
        # height so it scales with whatever size the logo is placed at.
        "clear_space_ratio": round(dot / lockup_h, 4) if dot else 0.12,
        "clear_space_basis": "height of the dot in the .AI wordmark (brand guide, Clear Space Rules)",
    }
    metrics_path = Path(args.metrics)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    log(f"clear space ratio = {metrics['clear_space_ratio']} of lockup height")

    print(metrics_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
