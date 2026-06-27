---
name: Executor flags changed / broke
about: A backend CLI (cursor, claude, codex, gemini, antigravity) changed its flags or behavior and the adapter no longer works
title: "[executor] <backend>: "
labels: executor-drift
---

<!--
This is the project's most common bug class: the third-party CLIs move fast and
their flags drift. Adapters live in executors.py (build(model, prompt) -> argv).
Please separate this from loop-logic bugs — use the Bug report template for those.
-->

**Backend**
<!-- cursor | claude | codex | gemini | antigravity -->

**CLI version**
<!-- output of e.g. `cursor-agent --version`, `codex --version`, `agy --version` -->

**What changed**
<!-- which flag/behavior; old vs new. e.g. "--yolo renamed to --auto" -->

**Command the driver ran / error**
<!-- from .loop/executor_output.txt, with anything sensitive redacted -->
```
```

**Link to the CLI's current docs**
<!-- so the adapter can be matched to the new surface -->

**Proposed adapter change (optional)**
<!-- the new argv list for build() in executors.py, if you have it -->
