"""Regression tests for tools/validate_brand.py.

`validate_brand.py` is the gate that stops a malformed brand file reaching the
renderer, and its whole value is that it does not trust what brand.json claims —
contrast is recomputed, font embedding permission is read from the binary. That
property is easy to break silently: soften one check and the tool still exits 0
on the real file, so nothing looks wrong until a bad report ships.

So each test takes the *real* brand.json, injects exactly one defect, and asserts
the specific check fires. A test that passes because some unrelated check
happened to error would be worthless, so every case matches on the message.

Run:
    uv run python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import validate_brand as vb  # noqa: E402

BRAND_PATH = REPO_ROOT / "brand" / "brand.json"


def run_checks(brand: dict) -> tuple[list[str], list[str]]:
    """Run every check against `brand`, returning (errors, warnings).

    validate_brand accumulates into module-level lists, so they have to be reset
    per case or the second test in a process inherits the first one's failures.
    """
    vb.errors.clear()
    vb.warnings.clear()

    vb.check_structure(brand)
    resolved = vb.check_palette(brand)
    vb.check_tokens(brand, resolved)
    vb.check_contrast(brand, resolved)
    vb.check_chart(brand, resolved)
    vb.check_assets(brand, REPO_ROOT)
    vb.check_fonts(brand, REPO_ROOT)
    vb.check_provenance(brand)

    return list(vb.errors), list(vb.warnings)


class BrandValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pristine = json.loads(BRAND_PATH.read_text(encoding="utf-8"))

    def brand(self) -> dict:
        """A fresh deep copy, so an injected defect cannot leak between tests."""
        return copy.deepcopy(self.pristine)

    def assertFlags(self, errors: list[str], fragment: str) -> None:
        joined = "\n".join(errors)
        self.assertTrue(
            any(fragment.lower() in e.lower() for e in errors),
            f"expected an error mentioning {fragment!r}, got:\n{joined or '(none)'}",
        )

    # -- the control case ---------------------------------------------------- #

    def test_real_brand_json_passes_clean(self):
        """The shipped file must have zero errors, or every other test is moot."""
        errors, _ = run_checks(self.brand())
        self.assertEqual(errors, [], f"brand.json has errors:\n" + "\n".join(errors))

    # -- structure and parsing ------------------------------------------------ #

    def test_missing_top_level_section(self):
        brand = self.brand()
        del brand["typography"]
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "missing top-level section")

    def test_malformed_hex_in_palette(self):
        brand = self.brand()
        brand["color"]["palette"]["charcoal"]["hex"] = "#GGGGGG"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "malformed hex")

    # -- the single-source-of-truth rule -------------------------------------- #

    def test_raw_hex_in_token(self):
        """Tokens must name a palette entry. A raw hex forks the source of truth."""
        brand = self.brand()
        brand["color"]["tokens"]["text_body"] = "#111111"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "raw hex")

    def test_token_referencing_unknown_palette_entry(self):
        brand = self.brand()
        brand["color"]["tokens"]["text_body"] = "midnight_teal"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "unknown palette entry")

    # -- strict monochrome ---------------------------------------------------- #

    def test_non_grey_palette_colour(self):
        brand = self.brand()
        brand["color"]["palette"]["charcoal"]["hex"] = "#113311"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "not a true grey")

    def test_non_grey_chart_series_colour(self):
        brand = self.brand()
        brand["color"]["chart"]["series"][2] = "#8A5A2A"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "not a true grey")

    def test_monochrome_rule_off_permits_colour(self):
        """The check is driven by the rule, not hardcoded. Turn it off, colour passes."""
        brand = self.brand()
        brand["rules"]["strict_monochrome"] = False
        brand["color"]["palette"]["charcoal"]["hex"] = "#113311"
        errors, _ = run_checks(brand)
        self.assertFalse([e for e in errors if "true grey" in e])

    # -- contrast is recomputed, never trusted -------------------------------- #

    def test_lying_contrast_ratio(self):
        """The headline property: a file that claims a ratio it does not have."""
        brand = self.brand()
        brand["color"]["contrast_verified"][2]["ratio"] = 21.0  # really 6.8:1
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "contrast claim wrong")

    def test_contrast_pair_below_aa(self):
        brand = self.brand()
        brand["color"]["contrast_verified"].append(
            {"fg": "#AAAAAA", "bg": "#FFFFFF", "ratio": 2.32, "wcag": "AA"}
        )
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "fails aa")

    def test_body_text_token_below_aa_on_page_background(self):
        """Even with no declared pair, the tokens that carry body copy are checked."""
        brand = self.brand()
        brand["color"]["palette"]["charcoal"]["hex"] = "#BBBBBB"
        brand["color"]["contrast_verified"] = []
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "below aa")

    # -- provenance honesty ---------------------------------------------------- #

    def test_chart_colour_falsely_claiming_brand_provenance(self):
        brand = self.brand()
        brand["color"]["chart"]["series_provenance"]["#8A8A8A"] = "brand"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "provenance 'brand'")

    # -- the accent exception -------------------------------------------------- #

    def test_accent_without_recorded_approval(self):
        """An accent breaks strict monochrome. Unapproved, that is a violation."""
        brand = self.brand()
        brand["color"]["chart"]["accent"]["approved_by_user"] = False
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "without approved_by_user")

    def test_accent_too_faint_against_its_ground(self):
        brand = self.brand()
        brand["color"]["chart"]["accent"]["dark"] = "#3A2A10"  # near-black on black
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "3:1 needed for graphical objects")

    def test_approved_accent_still_warns_about_scope(self):
        """Approval silences the error but must not silence the reminder."""
        _, warnings = run_checks(self.brand())
        self.assertTrue(
            any("EXCEPTION to strict_monochrome" in w for w in warnings),
            "an approved accent must still warn that its scope is charts only",
        )

    # -- assets on disk -------------------------------------------------------- #

    def test_missing_logo_asset(self):
        brand = self.brand()
        brand["logo"]["variants"]["symbol"]["file"] = "brand/logo/does-not-exist.png"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "missing file")

    def test_missing_font_file(self):
        brand = self.brand()
        brand["typography"]["families"]["mono"]["file"] = "brand/fonts/Nope.ttf"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "missing font")

    # -- font licensing is read from the binary -------------------------------- #

    def test_embeddable_flag_is_not_trusted(self):
        """brand.json claiming embeddable:true proves nothing; the OS/2 table decides.

        Both bundled fonts are genuinely fsType 0, so this asserts the check reads
        the binary at all: a font whose file is real is accepted, and the flag
        alone never rescues a font whose file is missing.
        """
        brand = self.brand()
        brand["typography"]["families"]["sans"]["embeddable"] = True
        brand["typography"]["families"]["sans"]["file"] = "brand/fonts/Ghost.ttf"
        errors, _ = run_checks(brand)
        self.assertFlags(errors, "missing font")


if __name__ == "__main__":
    unittest.main()
