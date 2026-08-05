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

Install copies the tool files (driver.py, executors.py, prompts/, skills/) and seeds
AGENTS.md + the *.example references only if absent (never clobbers your standing
rules or an in-progress task). Idempotent: safe to re-run to upgrade.

Agent skills land as skills/<name>/, next to driver.py — we do not write into your
.claude/ or .cursor/. Which host you use and where it reads skills from is your call;
each skill's INSTALL.md maps the options. They upgrade per file on re-install, but
only where our last copy is untouched — a file you edited is kept and reported.

Uninstall removes, in tiers:
  tool      — driver.py, executors.py, the shipped prompts/*.md, __pycache__
  skills    — skills/<name>/, guarded per file like user content below (edited
              files are KEPT); empty dirs are then dropped
  artifacts — .loop/, plan.md, verdict.json, clarifications_needed.json
  user      — AGENTS.md, task.md, context.md, clarifications.md, *.example, but
              ONLY when byte-identical to the seed (an untouched tool copy) or
              --force. A merely git-tracked-and-clean file is KEPT: it may be the
              user's own (a pre-existing AGENTS.md, say — install seeds only if
              absent, so it may never have been ours), and deleting that, even
              recoverably, is a nasty surprise. Files with no seed (task.md,
              context.md, clarifications.md) are therefore removed only with --force.

Git hygiene: on install we add a managed block to the target's .git/info/exclude
(not the tracked .gitignore, so there's no visible change to commit) listing the
tool's own files + per-run artifacts. That keeps the loop's `git add -A` from
staging driver.py/prompts/artifacts into the user's index — which otherwise
polluted the verified diff and, once uninstall deleted them, left "deleted but
staged" ghosts in `git status`. Uninstall removes that block and unstages exactly
the files it deletes, so the repo ends clean.
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

# Agent skills (SRC/skills/<name>/SKILL.md + reference/). Copied into the target as
# skills/<name>/, alongside driver.py — NOT into .claude/ or .cursor/. Where a user
# keeps their skills (per-repo, global, or uploaded to a desktop app) is their call,
# and their config dirs are not ours to write into; each skill's INSTALL.md tells
# them how to place it. Like prompts, the set is derived at runtime, so install and
# uninstall share one source of truth.
SKILLS_DIRNAME = "skills"

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

# Paths the driver removes unconditionally (tool + artifacts) — the set that must
# stay out of the target's git, so the loop's `git add -A` never stages the tool's
# own files (which would pollute the verified diff and leave staged ghosts after
# uninstall). Kept out via .git/info/exclude, not the user's tracked .gitignore, so
# there's no visible change to commit. NOT the guarded user files — those the user
# may legitimately version.
EXCLUDE_BEGIN = "# >>> agents-collab (managed by install.py) >>>"
EXCLUDE_END = "# <<< agents-collab <<<"


# ---- pure decision logic (unit-tested; no I/O) -----------------------------
def removal_reason(*, force, matches_seed):
    """Why a guarded user file may be removed, or '' to KEEP it. Pure. Only two
    signals reliably mean "the tool owns this file, safe to delete": an explicit
    --force, or a byte-match against the seed (an untouched tool copy). We do NOT
    remove a merely git-tracked-and-clean file: being committed-and-unmodified can't
    distinguish the tool's file from a user's OWN pre-existing one (e.g. an AGENTS.md
    they had before installing — install seeds it only if absent, so it may never
    have been ours), and deleting that, even recoverably, is a nasty surprise."""
    if force:
        return "--force"
    if matches_seed:
        return "untouched seed"
    return ""


def render_git_exclude(existing_text, patterns, *, add):
    """Return .git/info/exclude content with the agents-collab managed block added
    (add=True) or removed (add=False). Idempotent: any prior managed block is
    stripped first, so re-running install never duplicates it and uninstall removes
    it cleanly. User lines outside the markers are preserved. Pure — file I/O is
    separate."""
    kept = []
    skipping = False
    for line in existing_text.splitlines():
        if line.strip() == EXCLUDE_BEGIN:
            skipping = True
            continue
        if line.strip() == EXCLUDE_END:
            skipping = False
            continue
        if not skipping:
            kept.append(line)
    while kept and kept[-1].strip() == "":  # trim trailing blanks we may have left
        kept.pop()
    if add:
        block = [
            EXCLUDE_BEGIN,
            "# Tool files + per-run artifacts, kept out of the loop's `git add -A` so",
            "# the verified diff stays clean and uninstall leaves no staged ghosts.",
            *patterns,
            EXCLUDE_END,
        ]
        if kept:
            kept.append("")
        kept.extend(block)
    text = "\n".join(kept)
    return text + "\n" if text else ""


# ---- helpers ---------------------------------------------------------------
def shipped_prompts():
    """Basenames of the prompt files this tool ships (SRC/prompts/*.md), sorted."""
    d = os.path.join(SRC, "prompts")
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".md"))


def shipped_skills():
    """Names of the agent skills this tool ships — the SRC/skills/* directories that
    actually contain a SKILL.md, sorted. A dir without one isn't a skill."""
    d = os.path.join(SRC, SKILLS_DIRNAME)
    if not os.path.isdir(d):
        return []
    return sorted(
        n for n in os.listdir(d)
        if os.path.isfile(os.path.join(d, n, "SKILL.md"))
    )


def skill_files(name):
    """Forward-slash relpaths of every file inside skills/<name>/, sorted. The whole
    folder ships as one unit — body, reference/, INSTALL.md, and the Cursor pointer
    rule — because the user, not the installer, decides where it ends up."""
    root = os.path.join(SRC, SKILLS_DIRNAME, name)
    rels = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), root)
            rels.append(rel.replace(os.sep, "/"))
    return sorted(rels)


def skill_dirs(name):
    """Forward-slash relpaths of subdirectories inside skills/<name>/, deepest first
    — the order uninstall must remove them in for each to be empty when it's tried."""
    dirs = {os.path.dirname(r) for r in skill_files(name) if "/" in r}
    return sorted(dirs, key=lambda d: (-d.count("/"), d))


def _ospath(target, rel):
    """Join a forward-slash relpath onto target as a native OS path."""
    return os.path.join(target, *rel.split("/"))


def _delete(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _same_bytes(a, b):
    """True if both paths are existing files with identical contents. The one test
    that reliably means "the tool owns this copy" — used in both directions: install
    won't overwrite a file that fails it, uninstall won't delete one."""
    return os.path.isfile(a) and os.path.isfile(b) and filecmp.cmp(a, b, shallow=False)


def _matches_seed(abs_path, seed):
    """True if abs_path is byte-identical to the installer's seed (so it's an
    untouched copy and safe to remove). `seed` is a forward-slash relpath under SRC,
    so nested seeds (skills/<name>/reference/x.md) work on Windows too. Reads files;
    no subprocess."""
    if not seed:
        return False
    return _same_bytes(abs_path, os.path.join(SRC, *seed.split("/")))


def _exclude_patterns():
    """Root-anchored ignore patterns for the tool's own files + per-run artifacts —
    the set the driver removes unconditionally. Prompt files are listed individually
    (not the whole /prompts/ dir) so a user's own top-level prompts/ is never
    shadowed."""
    pats = ["/driver.py", "/executors.py", "/__pycache__/"]
    pats += [f"/prompts/{p}" for p in shipped_prompts()]
    # Skills likewise per-skill (not the whole /skills/ dir), so a user's own
    # top-level skills/ is never shadowed — same reasoning as prompts above.
    pats += [f"/{SKILLS_DIRNAME}/{s}/" for s in shipped_skills()]
    pats += ["/.loop/", "/plan.md", "/verdict.json", "/clarifications_needed.json"]
    return pats


def _git_exclude_file(target):
    """Path to this repo's info/exclude (via rev-parse, so it's correct for git
    worktrees where .git is a file), or None if target isn't a git repo."""
    r = subprocess.run(
        ["git", "-C", target, "rev-parse", "--git-path", "info/exclude"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if r.returncode != 0:
        return None
    p = r.stdout.strip()
    return p if os.path.isabs(p) else os.path.join(target, p)


def _update_git_exclude(target, *, add):
    """Add/remove the managed ignore block in .git/info/exclude. Returns True if it
    acted (target is a git repo), False otherwise."""
    path = _git_exclude_file(target)
    if not path:
        return False
    existing = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
    new = render_git_exclude(existing, _exclude_patterns(), add=add)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return True


def _git_unstage(target, rels):
    """Drop rels from the index without touching the working tree, so tool files a
    prior `git add -A` staged don't linger as ghosts. `git rm --cached` correctly
    stages a deletion for a committed (vendored) file and just drops the entry for a
    staged-but-uncommitted one; --ignore-unmatch makes never-staged paths a no-op."""
    subprocess.run(
        ["git", "-C", target, "rm", "-r", "--cached", "--ignore-unmatch", "--quiet",
         "--", *rels],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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

    # Agent skills: dropped next to driver.py for the user to place themselves (each
    # skill's INSTALL.md says where). Upgraded per file, but only where our last copy
    # is still byte-identical — a file the user edited is KEPT, because unlike
    # prompts/ a skill carries no contract the driver must stay in step with, and
    # teams do customise them.
    for name in shipped_skills():
        src_root = os.path.join(SRC, SKILLS_DIRNAME, name)
        written, kept = 0, []
        for rel in skill_files(name):
            src = os.path.join(src_root, *rel.split("/"))
            dst = _ospath(target, f"{SKILLS_DIRNAME}/{name}/{rel}")
            if os.path.exists(dst) and not _same_bytes(dst, src):
                kept.append(rel)
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            written += 1
        print(f"  ✓ {SKILLS_DIRNAME}/{name}/ ({written} file(s)) — see its INSTALL.md")
        for rel in kept:
            print(f"  · KEPT {SKILLS_DIRNAME}/{name}/{rel} (your edits; the new "
                  f"version is in this clone at {SKILLS_DIRNAME}/{name}/{rel})")

    # User-owned files: seed only if absent (never clobber).
    for name in SEED_FILES:
        dst = os.path.join(target, name)
        if os.path.exists(dst):
            print(f"  · {name} already exists — left as-is")
        else:
            shutil.copy2(os.path.join(SRC, name), dst)
            print(f"  ✓ {name} (seeded)")

    # Keep the tool's own files out of the target's git, so the loop's `git add -A`
    # stages only the user's edits (not driver.py/prompts/artifacts).
    if _update_git_exclude(target, add=True):
        print("  ✓ .git/info/exclude — tool files kept out of `git add -A`")

    print(
        f'\nDone. Next:\n'
        f'  cd "{target}"\n'
        f"  cp task.md.example task.md        # then fill in goal + acceptance criteria\n"
        f"  cp context.md.example context.md  # then describe your codebase\n"
        f"  python driver.py --help           # see flags, pick models/executor\n\n"
        f"Working from a Jira ticket? skills/jira-to-task/ writes task.md for you,\n"
        f"pulling the issue over your own Jira MCP. Read its INSTALL.md to add it to\n"
        f"Claude Code, Cursor, or Claude Desktop — where skills live is your call.\n\n"
        f"Run on a dedicated branch or git worktree — edits are auto-applied. "
        f"See SECURITY.md."
    )


# ---- uninstall -------------------------------------------------------------
def _remove_path(target, rel, dry):
    """Remove a tool/artifact path unconditionally (respecting --dry-run). Returns
    the rel if it actually deleted something, else None (so the caller can unstage
    exactly what was removed)."""
    abs_path = _ospath(target, rel)
    if not os.path.lexists(abs_path):
        return None
    if dry:
        print(f"  would remove  {rel}")
        return None
    _delete(abs_path)
    print(f"  ✓ removed     {rel}")
    return rel


def _rmdir_if_empty(target, rel, dry):
    """Drop a container dir (prompts/, skills/, skills/<name>/reference/ …) only if
    nothing the user added — or that we guarded and kept — is left in it."""
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
    """Remove a guarded user file ONLY if it's an untouched tool seed or --force;
    otherwise KEEP it — never delete the user's own or customized content (a merely
    committed file is NOT reason enough; see removal_reason). Returns the rel if it
    deleted the file, else None."""
    abs_path = _ospath(target, rel)
    if not os.path.lexists(abs_path):
        return None
    why = removal_reason(
        force=force,
        matches_seed=_matches_seed(abs_path, seed),
    )
    if why:
        if dry:
            print(f"  would remove  {rel}   [{why}]")
            return None
        _delete(abs_path)
        print(f"  ✓ removed     {rel}   [{why}]")
        return rel
    print(
        f"  · KEPT        {rel}   "
        "(your content — not an untouched tool copy; use --force to remove)"
    )
    return None


def uninstall(target, dry=False, force=False):
    print(f"Uninstalling agents-collab from {target}")
    if dry:
        print("  (dry run — nothing will be deleted)")

    deleted = []  # rels actually removed — unstaged together at the end

    print("tool files:")
    deleted.append(_remove_path(target, "driver.py", dry))
    deleted.append(_remove_path(target, "executors.py", dry))
    for p in shipped_prompts():
        deleted.append(_remove_path(target, f"prompts/{p}", dry))
    _rmdir_if_empty(target, "prompts", dry)
    deleted.append(_remove_path(target, "__pycache__", dry))

    # Skills are guarded like user content, not removed outright like prompts: the
    # target copy is the one a user may have edited before placing it. Byte-identical
    # files go; anything they touched is KEPT and reported. The seed relpath equals
    # the target relpath, since the target layout mirrors this clone's.
    if shipped_skills():
        print("skills (guarded — your edits are kept):")
    for s in shipped_skills():
        for rel in skill_files(s):
            p = f"{SKILLS_DIRNAME}/{s}/{rel}"
            deleted.append(_remove_user(target, p, p, dry, force))
        for d in skill_dirs(s):  # deepest first, so each is empty when tried
            _rmdir_if_empty(target, f"{SKILLS_DIRNAME}/{s}/{d}", dry)
        _rmdir_if_empty(target, f"{SKILLS_DIRNAME}/{s}", dry)
    if shipped_skills():
        _rmdir_if_empty(target, SKILLS_DIRNAME, dry)

    print("generated artifacts:")
    for name in ARTIFACTS:
        deleted.append(_remove_path(target, name, dry))

    print("user content (guarded):")
    for rel, seed in USER_FILES:
        deleted.append(_remove_user(target, rel, seed, dry, force))

    # Undo the git footprint: unstage exactly the files we deleted (so an earlier
    # `git add -A` doesn't leave them as "deleted but staged" ghosts) and drop the
    # managed exclude block. Covers tool, artifact, AND any guarded file removed.
    deleted = [r for r in deleted if r]
    if _git_exclude_file(target):
        print("git:")
        if dry:
            print("  would unstage the removed files and clean .git/info/exclude")
        else:
            if deleted:
                _git_unstage(target, deleted)
            _update_git_exclude(target, add=False)
            print("  ✓ unstaged the removed files; cleaned .git/info/exclude")

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
