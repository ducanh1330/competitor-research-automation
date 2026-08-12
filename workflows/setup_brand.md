# Set Up Brand System

## Objective
Turn dropped-in brand assets into `brand/brand.json` — the single source of truth every
renderer reads — plus the derived logo variants and font instances the PDF needs.
One-time; re-run only when brand assets change.

**Status: complete for DOZ.AI as of 2026-08-12.** This document exists so it can be
repeated correctly, and so the reasoning behind the extracted values is recoverable.

## Inputs
| Input | Required | Notes |
|---|---|---|
| Logo file(s) in `brand/logo/` | yes | SVG preferred; PNG at 2x+ acceptable |
| Brand guidelines in `brand/guidelines/` | yes | PDF, image, Word, or Figma export |
| Brand font files in `brand/fonts/` | no | If absent, fetch the named faces if they are freely licensed |

## Steps

1. **Read the guidelines end to end before extracting anything.**
   Read them as an image/document, not by skimming for hex codes. Section headers,
   prohibitions and usage notes carry rules that are not visible in a colour swatch.

2. **Extract the palette exactly as printed.** Record for each colour: hex, the
   guide's own name, and its stated role.
   - Watch for duplicate hexes under different names. The DOZ.AI guide prints 8
     swatches but only 5 unique values — the second row repeats three of the
     first. Store canonical colours once and record the other names as `aliases`,
     so either vocabulary resolves.

3. **Verify contrast before trusting any pairing.** Compute WCAG ratios rather than
   assuming; a "muted" grey may be fine for annotations and unusable for body copy.
   Record the results in `color.contrast_verified` — `validate_brand.py` recomputes
   them and fails if a stored number is wrong.

4. **Extract typography from the specimens, not the recommendations.**
   Guides frequently disagree with themselves here. DOZ.AI's "Type Recommendations"
   column suggests *Inter, Satoshi, or similar*, while every specimen is labelled
   **Geist**. The specimens win; the suggestion is recorded as `fallback_note`.

5. **Check font embedding permission before promising real brand type.**
   ```
   uv run tools/validate_brand.py
   ```
   reads `fsType` out of the OS/2 table. `fsType 2` means embedding is forbidden and
   the PDF would silently substitute a fallback face — flag it and choose a licensed
   substitute rather than shipping a report that renders wrong.
   Geist and Geist Mono are SIL OFL with `fsType 0`: embedding is permitted.

6. **Build static font instances.**
   ```
   uv run tools/build_font_instances.py
   ```
   Writes: `brand/fonts/static/*.ttf` + `instances.json`

7. **Derive the logo variants.**
   ```
   uv run tools/derive_logo_variants.py
   ```
   Writes: trimmed lockup, symbol-only, and reversed versions of both, plus
   `brand/brand_metrics.json` with the measured clear-space ratio.

8. **Write `brand/brand.json`**, then validate:
   ```
   uv run tools/validate_brand.py
   ```

9. **Flag every inferred value.** Anything the guide does not specify but the report
   needs (h3 size, caption size, page margins, minimum logo sizes) gets a
   `provenance` or `status: proposed` field. `validate_brand.py` lists them as
   warnings so they stay visible instead of hardening into fake brand rules.

## Output
`brand/brand.json` validating clean, with only expected warnings. Downstream, no
other file contains a hex code, font name, or logo path.

## Edge cases & failure handling
- **Font is web-only licensed** → do not embed. Flag it and pick a licensed substitute.
- **Guideline is silent on something needed** → propose a value, mark it `proposed`,
  and raise it with the user. Never silently invent a brand rule.
- **Guide contradicts itself** → prefer the concrete specimen over the prose
  recommendation, and record the contradiction in the relevant `*_note` field.
- **Only a raster logo supplied** → derive variants from it, and note that print
  above roughly `pixel_width / 300` inches will soften.
- **`validate_brand.py` fails** → fix `brand.json`. Never edit the validator to pass.

## Learned constraints

- **2026-08-12 — Guide swatch counts mislead.** 8 swatches, 5 unique hexes. Always
  deduplicate by value before assuming palette breadth.
- **2026-08-12 — Strict monochrome conflicts with multi-series charts.** Four values
  usable on white cannot separate 5–7 competitors. Resolved with a derived grey
  ramp recorded as an explicit, user-approvable extension (`color.chart.status =
  proposed-extension`), never as an accent hue. `render_report.py` refuses to draw
  charts until `approved_by_user` is true unless `--allow-unapproved-chart-colors`
  is passed.
- **2026-08-12 — Variable fonts break matplotlib emphasis silently.** matplotlib's
  font manager only sees a variable font's default instance, so `fontweight="bold"`
  renders at 400 with only a `findfont` warning. Chromium handles variable fonts
  fine, so the HTML looked correct while charts lost their emphasis. Static
  instances are now generated for 400/600/700; `render_report.py` warns loudly if
  they are missing.
- **2026-08-12 — The `.AI` dot must be measured per-glyph, not per-column.** A
  column-scanning heuristic merged the dot with anti-aliasing at neighbouring
  letter junctions and reported 71px against a true 48px — a 48% overstatement of
  every clear-space margin. Group the wordmark into glyphs by empty columns, then
  select the short, roughly square, low-sitting one.
- **2026-08-12 — PowerShell treats `[` and `]` in paths as wildcards.** Downloading
  `Geist[wght].ttf` via `Invoke-WebRequest -OutFile` fails with a misleading
  "Unable to find the specified file". Save to a plain filename instead.
