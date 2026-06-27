<!-- Keep PRs small and focused on one thing. See CONTRIBUTING.md. -->

## What and why

<!-- What changed, and the problem it solves. -->

## Design principles this respects

<!-- Which of the CONTRIBUTING.md principles your change preserves. The load-bearing ones: -->

- [ ] Driver owns control flow; prompts own behavior (no role behavior baked into Python, no loop logic in a prompt).
- [ ] Agents stay stateless one-shots; state lives in the driver / on disk.
- [ ] Standard library only — no new pip dependencies.
- [ ] Non-destructive — no step that commits/pushes/resets/deletes without an explicit, off-by-default flag.
- [ ] If the `verdict.json` shape changed, `prompts/verify.md`, the driver's parser, and `verdict.sample.json` were all updated together.

## Checks

- [ ] `make check` passes (`python -m py_compile driver.py executors.py`).
- [ ] If loop logic changed: smoke-ran the example task in a throwaway repo and confirmed the clarity gate, an iteration, and the stop conditions still behave.

## For an executor change

- [ ] Adapter is argv construction only (no I/O, no side effects).
- [ ] Documented in the README Executors table; noted auth + context-file convention.
