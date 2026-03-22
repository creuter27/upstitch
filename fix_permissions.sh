#!/usr/bin/env bash
# fix_permissions.sh — restore execute bits and npm .bin symlinks after Tresorit sync.
# Tresorit strips execute bits and does not sync symlinks.
# Run this once on macOS/Linux after files arrive via Tresorit.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== fix_permissions.sh ==="
echo "Root: $SCRIPT_DIR"
echo

# 1. Shell scripts
echo "[1/2] Restoring execute bits on .sh files..."
find "$SCRIPT_DIR" -name "*.sh" \
    -not -path "*/node_modules/*" \
    -print0 | xargs -0 chmod +x
echo "      Done."

# 2. node_modules: fix binary execute bits, then re-run npm install to restore .bin symlinks
# (Python venvs live outside Tresorit at ~/.local/share/upstitch-venvs/ and need no fixing)
echo "[2/2] Restoring node_modules .bin symlinks..."
find "$SCRIPT_DIR" -path "*/node_modules/*/bin/*" -type f -print0 \
    | xargs -0 chmod +x 2>/dev/null || true

while IFS= read -r -d '' pkg; do
    dir="$(dirname "$pkg")"
    echo "      npm install: $dir"
    (cd "$dir" && npm install --silent 2>/dev/null) \
        || echo "      WARN: npm install failed in $dir"
done < <(find "$SCRIPT_DIR" -name "package.json" \
    -not -path "*/node_modules/*" \
    -print0)

echo "      Done."
echo
echo "=== All permissions restored! ==="
