# Examples

Worked examples of the loop, including the artifacts a real run produced — so you
can see exactly what `agentic-loop` does before pointing it at your own repo.

| Example | What it shows |
| --- | --- |
| [`romannumbers/`](romannumbers/) | The smallest end-to-end run: a stubbed function + a stdlib test gate, taken to a verified PASS in one iteration. Includes the real plan, diff, and verdict. |

Each example keeps its inputs (`task.md`, `context.md`, the starting source, the
test gate) and a `run/` directory with the captured outputs of an actual run
(`plan.md`, the diff, `verdict.json`, test output). The tool itself is **not**
copied in — run an example by dropping the loop in with `install.sh`, as each
example's README explains.
