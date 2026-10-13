# SyncForge — agent defaults

Windows 11 / Python 3.13 / PySide6 / SQLite / watchdog / BLAKE3. Continue the existing repository; actual code and tests, not earlier chat claims, are the source of truth.

## Always

1. **Protect data.** Never test on real user files or registered SSD/HDD without explicit approval. Unknown devices, unreadable folders and missing media are NOT deletions. No silent overwrite or delete; revalidate cached plans before writes.
2. **Separate concerns.** `gui` presents state; `services` coordinates; `core` plans/transfers; `storage` persists; `devices` verifies volumes; `rules` evaluates filters. Do not add filesystem, SQL or business logic to `gui/main_window.py`.
3. **Keep Qt responsive.** Scan, hash, plan and heavy DB work off the GUI thread; batch/model large result tables. Never manipulate widgets from workers.
4. **Make changes narrowly.** Inspect affected code, callers and relevant tests; don't rescan or reread the whole repository repeatedly. Prefer small cohesive modules; avoid speculative rewrites.
5. **Verify honestly.** Add targeted regression tests for behavioral fixes, using temp paths and isolated DBs. Run focused tests during work; full suite at meaningful checkpoints. Package/test `.exe` only for release requests or when packaging changes. Never equate file existence with successful GUI verification.
6. **Report briefly.** State what changed, tests actually run, unverified behavior, and remaining risks. Never conceal failures or log passwords, keys or user files.

## Skill loading

Use only task-relevant `.agents/skills/*/SKILL.md`, not all five by default. Involve `sync-safety` for operations that can change, overwrite, restore or delete files. Check scoped `.agents/rules/` when applicable. Keep implementation notes concise; update project docs for material architectural decisions.
