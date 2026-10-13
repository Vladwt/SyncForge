---
trigger: glob
globs: "src/syncforge/**/*.py, tests/**/*.py"
description: "Concise Python quality and safe testing rules for SyncForge source and tests."
---

# Python quality

- Keep interfaces typed and errors observable; no silent `except: pass`, placeholder controls or unsupported success claims.
- Tests use `tmp_path` and an isolated SQLite database; never touch the real app DB or user drives.
- Verify edge cases for Windows paths, symlinks/reparse points, case collisions and disconnects where relevant.
- Use installed tooling and targeted tests first; avoid redundant full-suite runs, dependency churn and repeated PyInstaller builds during development.
