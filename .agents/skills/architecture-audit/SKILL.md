---
name: architecture-audit
description: "Audits SyncForge Python module growth and coupling; incrementally extracts Qt god-classes or duplicated responsibilities. Use for architecture audit or refactoring."
---

# Architecture audit

1. Scope the requested change. Inspect the relevant file sizes, imports, call sites and tests; do NOT dump every source file into context.
2. Identify specific mixed responsibilities (GUI vs planner, DB, watcher, transfer), cycles or duplication. Rank only actionable findings.
3. Propose the smallest extraction with stable interfaces; keep `MainWindow` focused on shell and navigation. Prefer cohesive pages/models/workers over arbitrarily tiny files.
4. Refactor in small verified steps, preserving DB schema, profiles, baseline/tombstone semantics and user behavior. Add focused tests for moved logic.
5. Report before/after module ownership and unresolved debt in a brief summary; update architecture docs only if boundaries actually changed.

File size is a signal, not a target. Avoid unrelated rewrites.
