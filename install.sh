#!/usr/bin/env bash
#
# install.sh — thin shim around install.py (the real, cross-platform installer).
# Kept for muscle memory and existing docs; it just forwards every argument:
#
#   ./install.sh <target-repo>                       # install
#   ./install.sh --uninstall [--dry-run|--force] <t> # uninstall
#
# Prefer `python install.py ...` directly — it needs no bash and runs on Windows
# (cmd/PowerShell), macOS, and Linux alike. See install.py for full usage.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$DIR/install.py" "$@"
fi
exec python "$DIR/install.py" "$@"
