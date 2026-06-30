#!/usr/bin/env python3
"""install.py — drop agents-collab into a target repository (or remove it again).

The cross-platform installer: standard-library Python only, so it runs identically
on Windows (cmd/PowerShell), macOS, and Linux — no bash, no pip, no packaging. The
sibling `install.sh` is a thin shim that just execs this. Python is already the one
hard prerequisite (the tool *is* Python), so this needs nothing extra.

Install:
  python install.py ../path/to/target-repo
  TARGET=../path/to/target-repo python install.py
  make install TARGET=../path/to/target-repo

Uninstall (the inverse of install; shares the same file list):
  python install.py --uninstall ../path/to/target-repo
  python install.py --uninstall --dry-run ../path/to/target-repo   # show, delete nothing
  python install.py --uninstall --force   ../path/to/target-repo   # also remove modified/untracked user files

Install copies the tool files (driver.py, executors.py, prompts/) and seeds
AGENTS.md + the *.example references only if absent (never clobbers your standing
rules or an in-progress task). Idempotent: safe to re-run to upgrade.

Uninstall removes, in tiers:
  tool      — driver.py, executors.py, the shipped prompts/*.md, __pycache__
  artifacts — .loop/, plan.md, verdict.json, clarifications_needed.json
  user      — AGENTS.md, task.md, context.md, clarifications.md, *.example,
              but ONLY when recoverable (git-tracked & clean) or untouched
              (byte-identical to the seed). Modified/untracked files are KEPT
              unless --force, so unrecoverable work is never silently destroyed.
"""

import argparse
import filecmp
import os
import shutil
import subprocess
import sys

# Where this script — and therefore the tool's source files — live.
SRC = os.path.dirname(os.path.abspath(__file__))

# Tool files (overwritten on install, removed outright on uninstall). The shipped
# prompt set is derived from SRC/prompts at runtime (see shipped_prompts), not
# hardcoded, so install and uninstall share one source of truth.
TOOL_FILES = ["driver.py", "executors.py"]

# Generated runtime artifacts (gitignored; removed outright on uninstall).
ARTIFACTS = [".loop", "plan.md", "verdict.json", "clarifications_needed.json"]

# Files the installer seeds only if absent (never clobbers).
SEED_FILES = ["AGENTS.md", "task.md.example", "context.md.example"]

# Guarded user content for uninstall: (relpath, seed-or-None). A seed lets an
# untouched copy be removed by byte-compare; the rest rely on the git-clean check.
USER_FILES = [
    ("AGENTS.md", "AGENTS.md"),
    ("task.md.example", "task.md.example"),
    ("context.md.example", "context.md.example"),
    ("task.md", None),
    ("context.md", None),
    ("clarifications.md", None),
]


# ---- pure decision logic (unit-tested; no I/O) -----------------------------
def removal_reason(*, force, matches_seed, git_clean):
    """Why a guarded user file may be removed, or '' to KEEP it. Pure: the three
    inputs are booleans computed by the I/O probes below. Priority mirrors the
    installer's asymmetric, never-clobber contract — an explicit --force first,
    then an untouched seed, then a git-clean (recoverable) file."""
    if force:
        return "--force"
    if matches_seed:
        return "untouched seed"
    if git_clean:
        return "git-clean (recoverable)"
    return ""


# ---- helpers ---------------------------------------------------------------
def shipped_prompts():
    """Basenames of the prompt files this tool ships (SRC/prompts/*.md), sorted."""
    d = os.path.join(SRC, "prompts")
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".md"))


def _ospath(target, rel):
    """Join a forward-slash relpath onto target as a native OS path."""
    return os.path.join(target, *rel.split("/"))


def _delete(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _matches_seed(abs_path, seed):
    """True if abs_path is byte-identical to the installer's seed (so it's an
    untouched copy and safe to remove). Reads files; no subprocess."""
    if not seed:
        return False
    seed_path = os.path.join(SRC, seed)
    if not (os.path.isfile(seed_path) and os.path.isfile(abs_path)):
        return False
    return filecmp.cmp(abs_path, seed_path, shallow=False)


def _git_tracked_clean(target, rel):
    """True if rel is git-tracked in target with no staged/unstaged changes, i.e. a
    deletion is recoverable via `git restore`. git uses forward-slash pathspecs on
    every platform, so rel passes through directly."""
    if not os.path.isdir(os.path.join(target, ".git")):
        return False

    def ok(*args):
        return (
            subprocess.run(
                ["git", "-C", target, *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )

    return (
        ok("ls-files", "--error-unmatch", "--", rel)
        and ok("diff", "--quiet", "--", rel)
        and ok("diff", "--cached", "--quiet", "--", rel)
    )


# ---- install ---------------------------------------------------------------
def install(target):
    print(f"Installing agents-collab -> {target}")

    # Tool files: overwrite on install/upgrade (these ARE the tool).
    for name in TOOL_FILES:
        shutil.copy2(os.path.join(SRC, name), os.path.join(target, name))
    os.makedirs(os.path.join(target, "prompts"), exist_ok=True)
    for p in shipped_prompts():
        shutil.copy2(
            os.path.join(SRC, "prompts", p), os.path.join(target, "prompts", p)
        )
    print("  ✓ driver.py, executors.py, prompts/")

    # User-owned files: seed only if absent (never clobber).
    for name in SEED_FILES:
        dst = os.path.join(target, name)
        if os.path.exists(dst):
            print(f"  · {name} already exists — left as-is")
        else:
            shutil.copy2(os.path.join(SRC, name), dst)
            print(f"  ✓ {name} (seeded)")

    print(
        f'\nDone. Next:\n'
        f'  cd "{target}"\n'
        f"  cp task.md.example task.md        # then fill in goal + acceptance criteria\n"
        f"  cp context.md.example context.md  # then describe your codebase\n"
        f"  python driver.py --help           # see flags, pick models/executor\n\n"
        f"Run on a dedicated branch or git worktree — edits are auto-applied. "
        f"See SECURITY.md."
    )


# ---- uninstall -------------------------------------------------------------
def _remove_path(target, rel, dry):
    """Remove a tool/artifact path unconditionally (respecting --dry-run)."""
    abs_path = _ospath(target, rel)
    if not os.path.lexists(abs_path):
        return
    if dry:
        print(f"  would remove  {rel}")
    else:
        _delete(abs_path)
        print(f"  ✓ removed     {rel}")


def _rmdir_if_empty(target, rel, dry):
    """Drop prompts/ only if nothing the user added is left in it."""
    abs_path = _ospath(target, rel)
    if not os.path.isdir(abs_path):
        return
    if dry:
        print(f"  would remove  {rel}/ (if empty)")
        return
    try:
        os.rmdir(abs_path)
        print(f"  ✓ removed     {rel}/ (was empty)")
    except OSError:
        print(f"  · kept        {rel}/ (has files you added)")


def _remove_user(target, rel, seed, dry, force):
    """Remove a guarded user file ONLY if safe (untouched seed, git-clean, or
    --force); otherwise KEEP it — never destroy unrecoverable work silently."""
    abs_path = _ospath(target, rel)
    if not os.path.lexists(abs_path):
        return
    why = removal_reason(
        force=force,
        matches_seed=_matches_seed(abs_path, seed),
        git_clean=_git_tracked_clean(target, rel),
    )
    if why:
        if dry:
            print(f"  would remove  {rel}   [{why}]")
        else:
            _delete(abs_path)
            print(f"  ✓ removed     {rel}   [{why}]")
    else:
        print(
            f"  · KEPT        {rel}   "
            "(modified/untracked — not recoverable; use --force)"
        )


def uninstall(target, dry=False, force=False):
    print(f"Uninstalling agents-collab from {target}")
    if dry:
        print("  (dry run — nothing will be deleted)")

    print("tool files:")
    for name in TOOL_FILES:
        _remove_path(target, name, dry)
    for p in shipped_prompts():
        _remove_path(target, f"prompts/{p}", dry)
    _rmdir_if_empty(target, "prompts", dry)
    _remove_path(target, "__pycache__", dry)

    print("generated artifacts:")
    for name in ARTIFACTS:
        _remove_path(target, name, dry)

    print("user content (guarded):")
    for rel, seed in USER_FILES:
        _remove_user(target, rel, seed, dry, force)

    print()
    if dry:
        print("Dry run complete — re-run without --dry-run to apply.")
    else:
        print(
            "Done. Any files marked KEPT were modified or untracked; "
            "remove with --force if intended."
        )


# ---- entry point -----------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(
        prog="install.py",
        description="Install or uninstall agents-collab in a target repository.",
    )
    p.add_argument("--uninstall", action="store_true", help="remove the tool instead of installing")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="(uninstall) show what would be removed, delete nothing")
    p.add_argument("--force", action="store_true", help="(uninstall) also remove modified/untracked user files")
    p.add_argument("target", nargs="?", default=None, help="target repo (or set TARGET=<dir>)")
    args = p.parse_args(argv)

    target = args.target or os.environ.get("TARGET")
    if not target:
        print("error: no target repo given.", file=sys.stderr)
        p.print_usage(sys.stderr)
        return 1
    if not os.path.isdir(target):
        print(f"error: target '{target}' is not a directory.", file=sys.stderr)
        return 1
    target = os.path.realpath(target)

    if os.path.realpath(SRC) == target:
        print(
            "error: target is the agents-collab repo itself; pick the repo you want "
            "to work ON.",
            file=sys.stderr,
        )
        return 1
    if not os.path.isdir(os.path.join(target, ".git")):
        print(
            f"warning: '{target}' is not a git repository — the loop needs git to "
            "diff/verify.",
            file=sys.stderr,
        )

    if args.uninstall:
        if not args.dry_run and sys.stdin.isatty():
            ans = input(f"Remove agents-collab from {target}? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("aborted.")
                return 0
        uninstall(target, dry=args.dry_run, force=args.force)
    else:
        install(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
