---
name: regression-release
description: "Performs SyncForge regression QA, safe fixture tests and actual Windows PyInstaller GUI smoke tests for releases; avoids fake success reports."
---

# Regression and release verification

1. Check diff and expected behavior. Tests use temporary dirs and isolated DB; protect real application state.
2. During development run affected pytest tests; before a release run the full suite. Add regression tests for fixed defects and lifecycle/Qt events where applicable.
3. For sync changes test preview → execute → verify content/hash → modify/delete/conflict → restart; include device identity and tombstones.
4. For GUI changes test controls and signal flow (`pytest-qt`); include ~8k preview rows, changing profiles while busy, watchers, table state and action counters where relevant.
5. Only for requested release/packaging changes: build PyInstaller, launch the **packaged GUI**, perform a meaningful interaction, inspect logs and close normally. `Test-Path`, process existence and `Responding=True` alone are insufficient.
6. Do not kill user-owned processes. Report exact commands, actual pass/fail, manually checked behavior, unverified scenarios and release blockers. Never claim production-ready from pytest alone.
