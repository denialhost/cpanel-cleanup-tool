#!/usr/bin/env python3
"""Build whm/app.js as a single ES module (model + activity + UI) with no imports."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "const $ = id => document.getElementById(id);"


def demote(text: str) -> str:
    text = re.sub(r"^import\s+.+?\n", "", text, flags=re.M)
    text = re.sub(r"export\s+async\s+function\s+", "async function ", text)
    text = re.sub(r"export\s+function\s+", "function ", text)
    text = re.sub(r"export\s+const\s+", "const ", text)
    text = re.sub(r"export\s+let\s+", "let ", text)
    text = re.sub(r"export\s+class\s+", "class ", text)
    text = re.sub(r"export\s*\{[^}]*\}\s*;?", "", text)
    return text


def extract_app_body(app: str) -> str:
    start = app.find(MARKER)
    if start < 0:
        raise SystemExit(f"WHM app marker not found: {MARKER!r}")
    return app[start:]


def main() -> None:
    ui_source = ROOT / "whm" / "app.ui.js"
    app_path = ROOT / "whm" / "app.js"
    # Prefer editable UI source; fall back to stripping a previous bundle.
    if ui_source.is_file():
        app_body = extract_app_body(ui_source.read_text(encoding="utf-8"))
    else:
        app_body = extract_app_body(app_path.read_text(encoding="utf-8"))

    model = demote((ROOT / "plugin" / "model.js").read_text(encoding="utf-8"))
    activity = demote((ROOT / "plugin" / "activity.js").read_text(encoding="utf-8"))
    bundle = (
        "/* CCT WHM bundle: model + activity + app. One module, no relative imports. */\n"
        + model
        + "\n"
        + activity
        + "\n"
        + app_body
    )
    if re.search(r"(?m)^(import|export)\s", bundle):
        raise SystemExit("bundle still contains import/export")
    app_path.write_text(bundle, encoding="utf-8", newline="\n")
    print(app_path, len(bundle))


if __name__ == "__main__":
    main()
