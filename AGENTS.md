# AGENTS.md — standing rules for the coding agent

These apply to every change in this repo. The per-task spec lives in plan.md;
these are the constants.

## Scope discipline

- Change only what the current plan requires. No drive-by refactors.
- Prefer the smallest diff that satisfies the plan.
- Never touch files outside those named in the plan unless compilation forces it.

## Code conventions

- Match the surrounding file's existing style, naming, and structure. Do not
  reformat code you are not changing.
- Reuse existing helpers/types before introducing new ones.
- No new third-party dependencies unless the plan explicitly calls for one.

## Safety

- Do not edit test files unless the plan says to.
- Do not change CI, build config, or version/lockfiles unless the plan says to.
- Do not delete code that is unrelated to the task.

## When stuck

- If the plan is wrong or impossible, implement what you can and mark the gap
  with a `// LOOP-NOTE:` comment explaining what and why. Do not guess around it.
