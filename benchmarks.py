"""Micro-benchmarks for SyncForge file scanning, BLAKE3 hashing, and copying."""

import os
import time
import tempfile
from pathlib import Path
from syncforge.core.scanner import FileScanner
from syncforge.core.hasher import compute_blake3_hash
from syncforge.core.models import SyncProfile, SyncMode, FolderMapping
from syncforge.core.planner import SyncPlanner
from syncforge.core.transfer_engine import TransferEngine


def benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        src = root / "bench_src"
        dst = root / "bench_dst"
        src.mkdir()
        dst.mkdir()

        # Generate 1,000 synthetic files
        print("Generating 1,000 synthetic test files...")
        t0 = time.perf_counter()
        for i in range(1000):
            (src / f"file_{i:04d}.dat").write_bytes(b"SyncForgeBenchmarkDataPayload" * 10)
        t_gen = time.perf_counter() - t0
        print(f"File generation completed in: {t_gen:.4f}s")

        # 1. Benchmark file scanner
        scanner = FileScanner()
        t0 = time.perf_counter()
        files = scanner.scan_directory(str(src))
        t_scan = time.perf_counter() - t0
        print(f"Scanning 1,000 files took: {t_scan:.4f}s ({len(files)} files found, {len(files)/t_scan:.1f} files/sec)")

        # 2. Benchmark BLAKE3 streaming hashing
        test_file = src / "file_0000.dat"
        t0 = time.perf_counter()
        for _ in range(500):
            compute_blake3_hash(test_file)
        t_hash = time.perf_counter() - t0
        print(f"BLAKE3 500 hash computations took: {t_hash:.4f}s ({500/t_hash:.1f} hashes/sec)")

        # 3. Benchmark planning
        prof = SyncProfile(
            id="p_bench",
            name="Bench",
            mode=SyncMode.ONE_WAY_SAFE,
            dest_root=str(dst),
            mappings=[FolderMapping("m1", "p_bench", str(src), "")]
        )
        planner = SyncPlanner()
        t0 = time.perf_counter()
        plan = planner.generate_plan(prof, str(dst))
        t_plan = time.perf_counter() - t0
        print(f"Planning 1,000 file copies took: {t_plan:.4f}s ({len(plan)} operations planned)")

        # 4. Benchmark transfer engine
        engine = TransferEngine()
        t0 = time.perf_counter()
        status = engine.execute_plan("job_bench", prof, plan)
        t_transfer = time.perf_counter() - t0
        print(f"Transferring 1,000 files with validation took: {t_transfer:.4f}s (status: {status.value})")

        return {
            "files_scanned": len(files),
            "scan_time_sec": t_scan,
            "scan_rate_fps": len(files)/t_scan,
            "hash_500_time_sec": t_hash,
            "hash_rate_hps": 500/t_hash,
            "plan_time_sec": t_plan,
            "transfer_time_sec": t_transfer
        }


if __name__ == "__main__":
    benchmark()
