# Contributing

Thanks for your interest in improving **agentic-loop**. This is a small,
single-purpose tool; the bar for changes is "does it keep the loop simple,
inspectable, and model-agnostic." Please read the design notes below before
opening a PR — most of the value here is in *not* over-building.

## Design principles (please preserve these)

- **The driver owns control flow; the prompts own behavior.** `driver.py`
  decides *when* things run and *when to stop*. `prompts/*.md` decide *how* each
  agent behaves. Keep that separation — don't bake role behavior into Python, and
  don't put loop logic into a prompt.
- **Agents are stateless one-shots; git + files are the contract.** State lives
  in the driver and on disk (`task.md`, `plan.md`, the diff, `verdict.json`), not
  in any agent's memory. New features should keep that property.
- **`verify.md` and the driver's parser move together.** The verifier's output
  schema is parsed by `driver.py`. If you change the `verdict.json` shape, update
  both `prompts/verify.md` and the parsing/branching in the driver in the same PR,
  and update `verdict.sample.json`.
- **Standard library only.** The driver intentionally has zero pip dependencies.
  Please don't add any; if a feature seems to need one, open an issue first.
- **Non-destructive by default.** The driver stages to diff but never commits,
  resets, or deletes. Don't add steps that mutate history without an explicit,
  off-by-default flag.

## Good first contributions

### Add a new executor backend

This is the most useful and self-contained change. To add a coding CLI:

1. In `executors.py`, add a `build(model, prompt) -> argv` function. It must run
   the CLI **headless with edits auto-applied and no interactive prompts**, read
   `plan.md`, and exit non-zero on failure.
2. Register it in the `EXECUTORS` dict. If its stdout is a JSON envelope with a
   cost field, add it to `JSON_ENVELOPE_BACKENDS`.
3. Document it in the README's Executors table (command, context file, caveats).
4. Note the auth method and any context-file convention (e.g. `AGENTS.md`).

Keep adapter functions to argv construction only — no side effects, no I/O.

### Improve a prompt

Prompt changes are high-leverage. When tuning `prompts/*.md`, include in the PR a
short before/after example of the behavior you fixed (a plan that improved, a
verdict that got more actionable). Don't broaden a prompt's job — each role stays
narrow on purpose.

## Making a change

1. Fork and branch from `main`.
2. Keep the diff minimal and focused on one thing.
3. Sanity-check the driver still parses and imports:
   ```bash
   python -m py_compile driver.py executors.py
   ```
4. If you touched the loop, do a smoke run against the example task in a throwaway
   git repo (`task.md.example` + `context.md.example`) and confirm the clarity
   gate, an iteration, and the stop conditions still behave.
5. Open a PR describing **what** changed and **why**, and which principle above it
   respects.

## Reporting issues

Useful bug reports include: the backend and models used, the command you ran, and
the relevant contents of the `.loop/` scratch directory (`diff.patch`,
`verify_raw.txt`, `executor_output.txt`) with anything sensitive redacted. Flag
flakiness in third-party CLIs (their flags change often) separately from loop
logic bugs.

## Scope

This tool is deliberately small. Features that turn it into a general agent
framework, add a server/daemon, or require persistent infrastructure are probably
out of scope — open an issue to discuss before building.

By contributing, you agree your contributions are licensed under the repository's
MIT License.
