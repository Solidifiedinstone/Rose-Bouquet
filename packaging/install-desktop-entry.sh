#!/usr/bin/env bash
#
# Install Rose Bouquet's desktop entry and icons for the current user.
#
# Everything goes under ~/.local, so no root is needed and nothing outside the
# user's own home is touched. Run install-desktop-entry.sh --uninstall to
# remove it again.
#
# The launcher command is resolved at install time and written into the desktop
# entry as an absolute path, because desktop entries do not inherit the shell's
# PATH — a bare "rose-bouquet" fails to launch from a dock even when it works
# perfectly in a terminal.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
SIZES=(16 24 32 48 64 128 256)

uninstall() {
    rm -f "$APPS/rose-bouquet.desktop"
    rm -f "$ICONS/scalable/apps/rose-bouquet.svg"
    for size in "${SIZES[@]}"; do
        rm -f "$ICONS/${size}x${size}/apps/rose-bouquet.png"
    done
    update_caches
    echo "Removed Rose Bouquet desktop entry and icons."
}

update_caches() {
    command -v update-desktop-database >/dev/null 2>&1 &&
        update-desktop-database -q "$APPS" || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 &&
        gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
    exit 0
fi

# ── Locate the executable ─────────────────────────────────────────
LAUNCHER="$(command -v rose-bouquet || true)"
if [[ -z "$LAUNCHER" ]]; then
    # Fall back to a virtualenv sitting beside the repo, which is how the
    # README tells people to install it.
    for candidate in "$HERE/../.venv/bin/rose-bouquet" "$HOME/rose-bouquet/.venv/bin/rose-bouquet"; do
        if [[ -x "$candidate" ]]; then
            LAUNCHER="$(cd "$(dirname "$candidate")" && pwd)/$(basename "$candidate")"
            break
        fi
    done
fi

if [[ -z "$LAUNCHER" ]]; then
    echo "Could not find the rose-bouquet command." >&2
    echo "Install it first:  pip install -e ." >&2
    exit 1
fi

echo "Using launcher: $LAUNCHER"

# ── Icons ─────────────────────────────────────────────────────────
install -Dm644 "$HERE/rose-bouquet.svg" "$ICONS/scalable/apps/rose-bouquet.svg"

if command -v rsvg-convert >/dev/null 2>&1; then
    RENDER="rsvg-convert"
elif command -v magick >/dev/null 2>&1; then
    RENDER="magick"
else
    RENDER=""
    echo "No SVG rasteriser found; installing the scalable icon only." >&2
    echo "Docks that cannot read SVG will show a generic icon." >&2
fi

for size in "${SIZES[@]}"; do
    target="$ICONS/${size}x${size}/apps/rose-bouquet.png"
    mkdir -p "$(dirname "$target")"
    case "$RENDER" in
        rsvg-convert)
            rsvg-convert -w "$size" -h "$size" "$HERE/rose-bouquet.svg" -o "$target"
            ;;
        magick)
            magick -background none -density 384 "$HERE/rose-bouquet.svg" \
                -resize "${size}x${size}" "$target"
            ;;
    esac
done

# ── Desktop entry ─────────────────────────────────────────────────
mkdir -p "$APPS"
sed "s|^Exec=rose-bouquet$|Exec=$LAUNCHER|; s|^Exec=rose-bouquet |Exec=$LAUNCHER |" \
    "$HERE/rose-bouquet.desktop" > "$APPS/rose-bouquet.desktop"
chmod 644 "$APPS/rose-bouquet.desktop"

update_caches

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$APPS/rose-bouquet.desktop" &&
        echo "Desktop entry validates cleanly."
fi

echo
echo "Installed. Rose Bouquet should now appear in your application menu."
echo "  entry: $APPS/rose-bouquet.desktop"
echo "  icons: $ICONS/{scalable,48x48,256x256}/apps/rose-bouquet.*"
