---
name: feature-development
description: "Adds or changes SyncForge functionality with minimal module sprawl, acceptance criteria and focused tests. Use for new GUI features, profiles, rules or automation."
---

# Feature development

1. Translate the request into observable acceptance cases; locate existing owner modules and reuse established abstractions.
2. Keep presentation, coordination, domain decisions and persistence separate. Add a module only for a clear responsibility; avoid extending the god-class.
3. Implement the smallest end-to-end vertical slice (data → service → UI as needed). Validate translated labels and persistence/restart behavior.
4. Test new behavior and its failure case using disposable fixtures. If it mutates files, apply `sync-safety` before implementation.
5. Run targeted tests, then broader checks only at checkpoints. Summarize changed files, verified behavior and gaps; don't rebuild `.exe` unless requested/needed.

Avoid speculative support for future features before current needs justify it.
