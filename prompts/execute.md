Implement the plan in plan.md exactly.

- plan.md is your specification. Do every step in it; do nothing it does not ask
  for. Do not add features, refactors, or "improvements" beyond the plan.
- Follow the conventions in AGENTS.md (it is loaded automatically).
- Make the smallest change set that satisfies the plan. Touch only the files the
  plan names, plus whatever is strictly required to make them compile/run.
- Do NOT edit tests unless the plan explicitly says to.
- Do NOT change unrelated formatting, dependencies, or config.
- If a step in the plan is impossible or contradicts the existing code, implement
  the rest and leave a clear `// LOOP-NOTE:` comment at the relevant spot saying
  what you could not do and why. Do not invent a workaround.

When done, stop. A separate verifier will check your work; you do not need to
summarize or test it yourself.
