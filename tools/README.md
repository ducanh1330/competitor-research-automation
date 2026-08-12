# tools/

Deterministic Python scripts. One script = one job. No reasoning here — if a step
needs judgment, it belongs in a workflow, not in a tool.

## Conventions

- **Runnable standalone:** `uv run tools/<name>.py --help` must work without any
  agent context. Use `argparse` for inputs, not hardcoded values.
- **Config from `.env`:** load with `python-dotenv`; never read secrets from argv
  (they leak into shell history and logs).
- **Output:** write results to `.tmp/` and print the path on stdout. Print
  human-readable progress to stderr so stdout stays parseable.
- **Exit codes:** `0` success, non-zero failure. Fail loudly with the actual API
  error text — a tool that swallows errors is worse than one that crashes.
- **Idempotent where possible:** re-running with the same inputs should be safe.

## Naming

`<verb>_<object>.py` — e.g. `scrape_single_site.py`, `export_to_sheet.py`.
