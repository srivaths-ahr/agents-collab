#!/usr/bin/env bash
#
# install.sh — drop agentic-loop into a target repository.
#
# Usage:
#   ./install.sh ../path/to/target-repo
#   TARGET=../path/to/target-repo ./install.sh
#   make install TARGET=../path/to/target-repo
#
# Copies the tool files (driver.py, executors.py, prompts/) into the target,
# seeds AGENTS.md and the *.example references only if absent (never clobbers
# your standing rules or an in-progress task), and prints next steps.
# Idempotent: safe to re-run to upgrade an existing install.

set -euo pipefail

# Where this script — and therefore the tool's source files — live.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET="${1:-${TARGET:-}}"
if [ -z "$TARGET" ]; then
  echo "error: no target repo given." >&2
  echo "usage: ./install.sh <target-repo>   (or: TARGET=<dir> ./install.sh)" >&2
  exit 1
fi
if [ ! -d "$TARGET" ]; then
  echo "error: target '$TARGET' is not a directory." >&2
  exit 1
fi
TARGET="$(cd "$TARGET" && pwd)"

if [ "$SRC" = "$TARGET" ]; then
  echo "error: target is the agentic-loop repo itself; pick the repo you want to work ON." >&2
  exit 1
fi
if [ ! -d "$TARGET/.git" ]; then
  echo "warning: '$TARGET' is not a git repository — the loop needs git to diff/verify." >&2
fi

echo "Installing agentic-loop -> $TARGET"

# Tool files: overwrite on install/upgrade (these ARE the tool).
cp "$SRC/driver.py" "$SRC/executors.py" "$TARGET/"
mkdir -p "$TARGET/prompts"
cp "$SRC/prompts/"*.md "$TARGET/prompts/"
echo "  ✓ driver.py, executors.py, prompts/"

# User-owned files: seed only if absent (never clobber).
seed_if_absent() {
  local name="$1"
  if [ -e "$TARGET/$name" ]; then
    echo "  · $name already exists — left as-is"
  else
    cp "$SRC/$name" "$TARGET/$name"
    echo "  ✓ $name (seeded)"
  fi
}
seed_if_absent "AGENTS.md"
seed_if_absent "task.md.example"
seed_if_absent "context.md.example"

cat <<EOF

Done. Next:
  cd "$TARGET"
  cp task.md.example task.md        # then fill in goal + acceptance criteria
  cp context.md.example context.md  # then describe your codebase
  python driver.py --help           # see flags, pick models/executor

Run on a dedicated branch or git worktree — edits are auto-applied. See SECURITY.md.
EOF
