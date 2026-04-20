#!/usr/bin/env bash
# Hoops Highlight Exporter - macOS/Linux launcher
# Make executable once: chmod +x "hoops-exporter.command"
# Then double-click in Finder (Mac) or your file manager (Linux).

cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    python3 gui.py
elif command -v python >/dev/null 2>&1; then
    python gui.py
else
    osascript -e 'display alert "Python not found" message "Install Python 3 from python.org and try again."' 2>/dev/null \
      || echo "Python not found. Install Python 3 and try again."
    exit 1
fi
