---
name: sync-safety
description: "Reviews risky SyncForge file writes, deletion, archive/restore, tombstones, device identity, conflict handling and stale sync plans; prevents data loss."
---

# Sync/data safety

1. Model both sides and the last baseline: distinguish new, modified, intentionally deleted and never-tracked files. Persist tombstone decisions across restart.
2. Missing/inaccessible device or scan error ≠ deletion. A drive letter or copied marker alone ≠ trusted device. Block ambiguous writes.
3. A cached preview is not execution authority. Before each consequential write, revalidate identity, roots, both file states and user authorization; reject stale results.
4. Handle path traversal, reparse points, case collisions and overlapping mappings; stay inside approved roots.
5. Stage transfers, verify content, protect the last good version; commit journal/baseline only after confirmed results. Respect filesystem limitations.
6. `Archive a copy` must COPY, not MOVE. Delete/move/overwrite need explicit semantics and approval; no silent destructive conflict resolution.
7. Test on disposable folders and isolated DB: first sync, unilateral/dual edits, deletion, restart, disconnected device, interrupted copy, archive, restore.

Never test destructive operations on personal data. For future encryption/vault, use audited libraries and a separate threat model, never log secrets.
