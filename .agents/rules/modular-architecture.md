---
trigger: model_decision
description: "Use when adding substantial SyncForge features, refactoring modules, or editing oversized Python/Qt classes."
---

# Cohesive architecture

- Before multi-module changes, identify the owner of each responsibility; use existing boundaries.
- `MainWindow` handles application shell/navigation; place pages, dialogs, Qt models/delegates and workers in separate cohesive modules when needed. Keep engine and SQLite operations outside UI classes.
- Review files above ~400 lines for mixed responsibilities; above ~600 lines prefer targeted extraction when changing them. These are review triggers, NOT mandatory splits.
- Preserve interfaces and avoid circular imports (`core`, `storage`, `rules`, `devices` must not import `gui`).
- Split only for a demonstrated need; no giant rewrite or excessive tiny files. Verify moved behavior with focused tests.
