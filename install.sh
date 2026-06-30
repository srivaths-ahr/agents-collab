#!/usr/bin/env bash
#
# install.sh — drop agentic-loop into a target repository (or remove it again).
#
# Install:
#   ./install.sh ../path/to/target-repo
#   TARGET=../path/to/target-repo ./install.sh
#   make install TARGET=../path/to/target-repo
#
# Uninstall (the inverse of install; shares the same file list):
#   ./install.sh --uninstall ../path/to/target-repo
#   ./install.sh --uninstall --dry-run ../path/to/target-repo   # show, delete nothing
#   ./install.sh --uninstall --force   ../path/to/target-repo   # also remove modified/untracked user files
#
# Install copies the tool files (driver.py, executors.py, prompts/) and seeds
# AGENTS.md + the *.example references only if absent (never clobbers your standing
# rules or an in-progress task). Idempotent: safe to re-run to upgrade.
#
# Uninstall removes, in tiers:
#   tool      — driver.py, executors.py, the shipped prompts/*.md, __pycache__
#   artifacts — .loop/, plan.md, verdict.json, clarifications_needed.json
#   user      — AGENTS.md, task.md, context.md, clarifications.md, *.example,
#               but ONLY when recoverable (git-tracked & clean) or untouched
#               (byte-identical to the seed). Modified/untracked files are KEPT
#               unless --force, so unrecoverable work is never silently destroyed.

set -euo pipefail

# Where this script — and therefore the tool's source files — live.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE=install
DRY=0
FORCE=0
TARGET="${TARGET:-}"

usage() {
  cat >&2 <<'USAGE'
usage:
  install:    ./install.sh <target-repo>
  uninstall:  ./install.sh --uninstall [--dry-run] [--force] <target-repo>
  (target may also be given via TARGET=<dir>; --dry-run/--force apply to uninstall)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) MODE=uninstall ;;
    --dry-run)   DRY=1 ;;
    --force)     FORCE=1 ;;
    -h|--help)   usage; exit 0 ;;
    --*)         echo "error: unknown flag '$1'" >&2; usage; exit 1 ;;
    *)           TARGET="$1" ;;
  esac
  shift
done

if [ -z "$TARGET" ]; then
  echo "error: no target repo given." >&2
  usage
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

# ---------------------------------------------------------------------------
install() {
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
}

# ---------------------------------------------------------------------------
# Remove a tool/artifact path unconditionally (respecting --dry-run).
remove_path() {
  local rel="$1" abs="$TARGET/$1"
  [ -e "$abs" ] || return 0
  if [ "$DRY" = 1 ]; then
    echo "  would remove  $rel"
  else
    rm -rf "$abs"
    echo "  ✓ removed     $rel"
  fi
}

# Drop prompts/ only if nothing the user added is left in it.
rmdir_if_empty() {
  local rel="$1" abs="$TARGET/$1"
  [ -d "$abs" ] || return 0
  if [ "$DRY" = 1 ]; then
    echo "  would remove  $rel/ (if empty)"
    return 0
  fi
  if rmdir "$abs" 2>/dev/null; then
    echo "  ✓ removed     $rel/ (was empty)"
  else
    echo "  · kept        $rel/ (has files you added)"
  fi
}

# True if a path is git-tracked with no staged/unstaged changes, i.e. a deletion
# is recoverable via `git restore`.
git_tracked_clean() {
  local rel="$1"
  [ -d "$TARGET/.git" ] || return 1
  git -C "$TARGET" ls-files --error-unmatch -- "$rel" >/dev/null 2>&1 || return 1
  git -C "$TARGET" diff         --quiet -- "$rel" 2>/dev/null || return 1
  git -C "$TARGET" diff --cached --quiet -- "$rel" 2>/dev/null || return 1
  return 0
}

# Remove a user file ONLY if safe: --force, untouched seed, or git-clean
# (recoverable). Otherwise KEEP it — never destroy unrecoverable work silently.
remove_user() {
  local rel="$1" seed="${2:-}" abs="$TARGET/$1" why=""
  [ -e "$abs" ] || return 0
  if   [ "$FORCE" = 1 ]; then
    why="--force"
  elif [ -n "$seed" ] && [ -f "$SRC/$seed" ] && cmp -s "$abs" "$SRC/$seed"; then
    why="untouched seed"
  elif git_tracked_clean "$rel"; then
    why="git-clean (recoverable)"
  fi
  if [ -n "$why" ]; then
    if [ "$DRY" = 1 ]; then
      echo "  would remove  $rel   [$why]"
    else
      rm -rf "$abs"
      echo "  ✓ removed     $rel   [$why]"
    fi
  else
    echo "  · KEPT        $rel   (modified/untracked — not recoverable; use --force)"
  fi
}

uninstall() {
  echo "Uninstalling agentic-loop from $TARGET"
  if [ "$DRY" = 1 ]; then
    echo "  (dry run — nothing will be deleted)"
  fi

  echo "tool files:"
  remove_path "driver.py"
  remove_path "executors.py"
  local p
  for p in "$SRC/prompts/"*.md; do
    remove_path "prompts/$(basename "$p")"
  done
  rmdir_if_empty "prompts"
  remove_path "__pycache__"

  echo "generated artifacts:"
  remove_path ".loop"
  remove_path "plan.md"
  remove_path "verdict.json"
  remove_path "clarifications_needed.json"

  echo "user content (guarded):"
  remove_user "AGENTS.md"          "AGENTS.md"
  remove_user "task.md.example"    "task.md.example"
  remove_user "context.md.example" "context.md.example"
  remove_user "task.md"
  remove_user "context.md"
  remove_user "clarifications.md"

  echo
  if [ "$DRY" = 1 ]; then
    echo "Dry run complete — re-run without --dry-run to apply."
  else
    echo "Done. Any files marked KEPT were modified or untracked; remove with --force if intended."
  fi
}

if [ "$MODE" = uninstall ]; then
  if [ "$DRY" = 0 ] && [ -t 0 ]; then
    printf "Remove agentic-loop from %s? [y/N] " "$TARGET"
    read -r ans || ans=""
    case "$ans" in
      y|Y|yes|YES) ;;
      *) echo "aborted."; exit 0 ;;
    esac
  fi
  uninstall
else
  install
fi
