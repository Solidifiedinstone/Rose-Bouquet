#!/usr/bin/env python3
"""Generate the Compose palettes from the desktop app's theme data.

A theme is plain data on both sides — a name and a dozen hex strings — so the
only honest way to keep the phone and the desktop agreeing about what "Gruvbox"
looks like is to have one of them own the numbers and the other derive them.
The desktop owns them. This reads `rose_bouquet/ui/theme.py` from a checkout of
the desktop app and writes `Palettes.kt`.

    tools/port-themes.py ~/rose-bouquet

Run it after pulling desktop changes that touch themes. The output is committed,
so building this app never needs Python or a copy of the desktop repository.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / (
    "app/src/main/java/dev/rose/bouquet/ui/theme/Palettes.kt")

#: The colour fields, in the order they are declared in Kotlin.
FIELDS = ["background", "surface", "panel", "elevated", "accent", "accent_muted",
          "text", "text_dim", "border", "success", "warning", "error", "placeholder"]


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.title() for word in rest)


def luminance(colour: str) -> float:
    """WCAG relative luminance, used only to tell light themes from dark ones.

    Derived rather than declared: a hand-maintained `is_light` flag is one more
    thing that can disagree with the colours it describes, and a theme whose
    flag is wrong renders unreadable text rather than merely looking odd.
    """
    value = colour.lstrip("#")
    channels = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def extract(desktop: Path) -> tuple[dict, dict]:
    """Read the themes out of a desktop checkout, in its own interpreter.

    Importing the desktop package here would drag in PySide6 and a GUI
    toolchain to read what amounts to a table of hex strings, so the values
    come back over a subprocess as JSON instead.
    """
    script = (
        "import dataclasses, json;"
        "from rose_bouquet.ui.theme import THEMES, STYLES;"
        "print(json.dumps({"
        "'themes': {k: dataclasses.asdict(v) for k, v in THEMES.items()},"
        "'styles': {k: dataclasses.asdict(v) for k, v in STYLES.items()}}))"
    )
    python = desktop / ".venv" / "bin" / "python"
    result = subprocess.run(
        [str(python if python.exists() else sys.executable), "-c", script],
        cwd=desktop, capture_output=True, text=True, check=True,
    )
    import json
    payload = json.loads(result.stdout)
    return payload["themes"], payload["styles"]


def render(themes: dict, styles: dict) -> str:
    lines = ['''package dev.rose.bouquet.ui.theme

import androidx.compose.ui.graphics.Color

// GENERATED from the desktop app's rose_bouquet/ui/theme.py by tools/port-themes.py.
// Edit the palettes there and regenerate; editing this file by hand makes the
// phone and the desktop disagree about what "Gruvbox" means, which is exactly
// what keeping them as plain data on both sides is meant to prevent.

private fun c(value: Long) = Color(value or 0xFF000000L)

val ROSE_THEMES: List<RoseTheme> = listOf(''']

    for key, theme in themes.items():
        light = luminance(theme["background"]) > luminance(theme["text"])
        colours = ",\n        ".join(
            f'{camel(field)} = c(0x{theme[field].lstrip("#")})' for field in FIELDS)
        lines.append(f'''    RoseTheme(
        key = "{key}",
        label = "{theme["name"]}",
        {colours},
        isLight = {"true" if light else "false"},
    ),''')

    lines.append(")\n\nval ROSE_STYLES: List<RoseStyle> = listOf(")
    for key, style in styles.items():
        lines.append(f'''    RoseStyle(
        key = "{key}",
        label = "{style["name"]}",
        radius = {style["radius"]}, radiusLarge = {style["radius_large"]},
        radiusSmall = {style["radius_small"]}, padding = {style["padding"]},
        fontSize = {style["font_size"]}, headingSize = {style["heading_size"]},
        borderWidth = {style["border_width"]}, lineHeight = {style["line_height"]},
        elevatedPanels = {"true" if style["elevated_panels"] else "false"},
    ),''')
    lines.append(")")
    return "\n".join(lines) + "\n"


def main() -> int:
    desktop = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "rose-bouquet")
    if not (desktop / "rose_bouquet" / "ui" / "theme.py").exists():
        print(f"No desktop checkout at {desktop}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 1

    themes, styles = extract(desktop)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(themes, styles), encoding="utf-8")
    print(f"{len(themes)} themes and {len(styles)} styles -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
