# SyncForge v1.0 — Performance Benchmark Results

Tests performed on Windows 11 x64 with synthetic datasets using `benchmarks.py`.

## Summary of Actual Measurements

| Operation | Workload | Elapsed Time | Throughput | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Directory Scanning** | 1,000 files | **0.0764 s** | **13,086.9 files/sec** | Protected against NTFS junction loops |
| **BLAKE3 Streaming Hashing** | 500 files | **0.1837 s** | **2,721.9 hashes/sec** | 1 MiB chunk streaming without loading into RAM |
| **Sync Planning (Mode A)** | 1,000 operations | **0.0891 s** | **11,223 operations/sec** | Deterministic rule matching & boundary validation |
| **Validated Safe Transfer** | 1,000 files | **2.0430 s** | **489.5 files/sec** | Includes stream copy to `.tmp`, hash verification, safe atomic replacement |

## Automated Test Coverage

- Total Tests Passed: **19 / 19** (100%)
- Unit, Integration, Extended Edge Cases, and Hypothesis property-based tests.
- Covered mandatory scenarios:
  1. SSD reconnects under a different drive letter.
  2. A different disk receives the old letter.
  3. Device disconnects during file transfer (cancelled safely).
  4. Same file modified independently on both sides (conflict recorded).
  5. File deleted on PC but retained on SSD (preserved).
  6. File deleted on SSD but retained on PC (preserved).
  7. Same-path files differ during initial synchronization (conservative conflict).
  8. Routing rules collision detection.
  9. Application interruption during replacement (destination intact).
  10. Identical timestamps but different contents (BLAKE3 hash comparison).
  11. Insufficient destination space aborts safely.
  12. NTFS junction / symlink loops prevented.
  13. File changed during copying detected via size/hash comparison.
  14. Backup resumes after an interrupted run.
  15. Cloned marker file detected and blocked when volume serial differs.
  16. Source and destination overlap rejected.
  17. Case-insensitive Windows name collisions detected.
  18. Unicode filenames and deeply nested paths supported.
  19. Historical file versioning and restore verified.
