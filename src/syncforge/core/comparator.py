"""High-performance file comparison utilities for SyncForge.

Supports:
- FAST mode: Pure metadata-first heuristic (size + mtime), 0 disk I/O for unchanged files
- BALANCED mode: Baseline cache + sample checks for ambiguous files
- FULL mode: Streaming BLAKE3 checksums with I/O throttling and rate limiting
"""

from pathlib import Path
from typing import Optional, Dict
from syncforge.core.models import FileMetadata, SyncProfile, AnalysisMode
from syncforge.core.hasher import compute_blake3_hash, compute_sample_hash


class FileComparator:
    """Evaluates whether two files are identical across FAST, BALANCED, and FULL analysis modes."""

    @staticmethod
    def are_files_identical(
        m1: FileMetadata,
        m2: FileMetadata,
        profile_or_verify: object,
        base_entry: Optional[Dict[str, dict]] = None,
    ) -> bool:
        """
        Compares two files using the configured analysis_mode:
        - FAST: metadata-only heuristic (size + mtime), 0 disk I/O for unchanged files
        - BALANCED: metadata + SQLite baseline cache + sample check for ambiguous unbaselined files
        - FULL: streaming BLAKE3 checksum comparison with I/O rate limiting & throttling
        """
        # Fast rejection by file size (0 disk reads)
        if m1.size_bytes != m2.size_bytes:
            return False

        # Support both SyncProfile and legacy bool verify_hashes
        if isinstance(profile_or_verify, SyncProfile):
            profile = profile_or_verify
            mode = getattr(profile, "analysis_mode", AnalysisMode.BALANCED)
            mtime_tol = getattr(profile, "mtime_tolerance_sec", 2.0)
        elif isinstance(profile_or_verify, bool):
            mode = AnalysisMode.FULL if profile_or_verify else AnalysisMode.FAST
            mtime_tol = 2.0
            profile = None
        else:
            mode = AnalysisMode.BALANCED
            mtime_tol = 2.0
            profile = None

        mtime_diff_sec = abs(m1.mtime_ns - m2.mtime_ns) / 1_000_000_000.0

        # FAST MODE: purely metadata-first heuristic (0 disk reads!)
        if mode == AnalysisMode.FAST:
            return mtime_diff_sec <= mtime_tol

        base_entry = base_entry or {}
        src_base = base_entry.get("source")
        dst_base = base_entry.get("dest")

        # Reuse baseline hashes if available and metadata matches baseline (0 disk reads)
        if src_base and not FileComparator.file_differs_from_baseline(m1, src_base, mtime_tol):
            if not m1.hash_blake3 and src_base.get("hash_blake3"):
                m1.hash_blake3 = src_base["hash_blake3"]

        if dst_base and not FileComparator.file_differs_from_baseline(m2, dst_base, mtime_tol):
            if not m2.hash_blake3 and dst_base.get("hash_blake3"):
                m2.hash_blake3 = dst_base["hash_blake3"]

        # If both hashes are already known (e.g. from baseline cache):
        if m1.hash_blake3 and m2.hash_blake3:
            return m1.hash_blake3 == m2.hash_blake3

        # BALANCED MODE: baseline-aware + sample check for ambiguous unbaselined files
        if mode == AnalysisMode.BALANCED:
            if mtime_diff_sec <= mtime_tol:
                # If both files match baseline, they are identical
                if src_base and dst_base:
                    return True
                # If no baseline, perform fast sample check (head + tail, max 128KB) to verify
                sample_kb = getattr(profile, "balanced_sample_size_kb", 64) if profile else 64
                if m1.abs_path and m2.abs_path and Path(m1.abs_path).exists() and Path(m2.abs_path).exists():
                    s1 = compute_sample_hash(Path(m1.abs_path), sample_size=sample_kb * 1024)
                    s2 = compute_sample_hash(Path(m2.abs_path), sample_size=sample_kb * 1024)
                    if s1 and s2:
                        return s1 == s2
                    # If either file could not be read (e.g. read error / device error), fall back to timestamp check
                    return mtime_diff_sec <= mtime_tol
                return True
            else:
                # Different timestamps with equal size
                return False

        # FULL MODE: full streaming BLAKE3 hashing with I/O throttling and rate limiting
        buf_size = getattr(profile, "io_buffer_size_kb", 128) * 1024 if profile else 128 * 1024
        yield_delay = getattr(profile, "io_yield_delay_ms", 0.0) if profile else 0.0
        speed_limit = getattr(profile, "io_speed_limit_mb", 0.0) if profile else 0.0

        if not m1.hash_blake3 and m1.abs_path and Path(m1.abs_path).exists():
            m1.hash_blake3 = compute_blake3_hash(
                Path(m1.abs_path),
                chunk_size=buf_size,
                yield_delay_ms=yield_delay,
                speed_limit_mb=speed_limit,
            )
        if not m2.hash_blake3 and m2.abs_path and Path(m2.abs_path).exists():
            m2.hash_blake3 = compute_blake3_hash(
                Path(m2.abs_path),
                chunk_size=buf_size,
                yield_delay_ms=yield_delay,
                speed_limit_mb=speed_limit,
            )

        if m1.hash_blake3 and m2.hash_blake3:
            return m1.hash_blake3 == m2.hash_blake3

        return mtime_diff_sec <= mtime_tol

    @staticmethod
    def file_differs_from_baseline(
        meta: Optional[FileMetadata],
        base_dict: Optional[dict],
        mtime_tol_sec: float = 2.0
    ) -> bool:
        """Determines if current metadata differs from stored baseline record."""
        if not meta or not base_dict:
            return True
        if meta.size_bytes != base_dict.get("size_bytes"):
            return True
        mtime_diff_sec = abs(meta.mtime_ns - base_dict.get("mtime_ns", 0)) / 1_000_000_000.0
        if mtime_diff_sec > mtime_tol_sec:
            return True
        return False
