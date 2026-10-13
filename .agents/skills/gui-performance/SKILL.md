---
name: gui-performance
description: "Diagnoses frozen PySide6 GUI, slow preview tables (8k+ files), inaccurate counters, Qt workers, progress and watchdog rescan storms in SyncForge."
---

# Qt GUI performance

1. Reproduce on synthetic ~8k files; time **scan, planning, serialization, table population, sorting and rendering separately**. Measure main-thread response, not only scan completion.
2. Offload IO and planning. Workers send bounded progress/results via signals; widgets are updated on the GUI thread only. Cancel safely, reject stale results by job/profile ID.
3. For large previews prefer `QTableView` + `QAbstractTableModel` and delegates over thousands of `QTableWidget` cell widgets or per-row `QComboBox` objects; update models in batches.
4. Debounce/deduplicate filesystem events on BOTH sides; avoid own-write loops and overlapping scans. Do not use `QApplication.processEvents()` as a structural workaround.
5. Progress is indeterminate until total work is known; distinguish discovered files, actionable operations, SKIP and displayed rows. Show 100% only once model/UI updates have completed.
6. Verify GUI interactions while processing, cancellation/close, profile switches and actual action counts. Compare measured before/after results; report remaining stalls.
