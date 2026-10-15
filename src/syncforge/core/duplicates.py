"""Core logic for discovering and grouping duplicate files."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Callable, Optional
import blake3
from syncforge.core.hasher import compute_blake3_hash, compute_sample_hash


@dataclass
class DuplicateFile:
    path: str
    size_bytes: int
    mtime: float


@dataclass
class DuplicateGroup:
    hash_blake3: str
    size_bytes: int
    files: List[DuplicateFile]

    @property
    def wasted_bytes(self) -> int:
        """Space wasted by duplicates (total size minus one kept copy)."""
        return max(0, (len(self.files) - 1) * self.size_bytes)


class DuplicateFinder:
    """Finds duplicate files based on size, sampling, and streaming BLAKE3 checksums."""

    def __init__(self, min_size_bytes: int = 1, is_cancelled: Optional[Callable[[], bool]] = None):
        self.default_min_size_bytes = min_size_bytes
        self.is_cancelled = is_cancelled or (lambda: False)

    def find_duplicates(
        self,
        directory: str,
        recursive: bool = True,
        min_size_bytes: Optional[int] = None
    ) -> List[DuplicateGroup]:
        """Convenience method to scan a single directory."""
        eff_min = self.default_min_size_bytes if min_size_bytes is None else min_size_bytes
        return self.scan_directories([directory], min_size_bytes=eff_min, recursive=recursive)

    def scan_directories(
        self,
        directories: List[str],
        min_size_bytes: Optional[int] = None,
        recursive: bool = True,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> List[DuplicateGroup]:
        """
        Scans directories, groups files by size, sample hash, and full BLAKE3 hash.
        Returns groups sorted by wasted disk space descending.
        """
        eff_min_size = self.default_min_size_bytes if min_size_bytes is None else min_size_bytes
        if progress_callback:
            progress_callback(5, "Сканування файлової структури...")

        # Step 1: Collect files and group by size
        size_map: Dict[int, List[Path]] = {}
        total_scanned = 0

        for dir_path in directories:
            p = Path(dir_path)
            if not p.exists() or not p.is_dir():
                continue

            if recursive:
                for root, _, files in os.walk(str(p)):
                    if self.is_cancelled():
                        return []
                    for name in files:
                        total_scanned += 1
                        if total_scanned % 500 == 0 and progress_callback:
                            progress_callback(15, f"Знайдено {total_scanned} файлів...")
                        fp = Path(root) / name
                        try:
                            st = fp.stat()
                            if st.st_size >= eff_min_size:
                                size_map.setdefault(st.st_size, []).append(fp)
                        except (OSError, PermissionError):
                            continue
            else:
                try:
                    for entry in os.scandir(str(p)):
                        if self.is_cancelled():
                            return []
                        if entry.is_file():
                            total_scanned += 1
                            st = entry.stat()
                            if st.st_size >= eff_min_size:
                                size_map.setdefault(st.st_size, []).append(Path(entry.path))
                except (OSError, PermissionError):
                    continue

        if self.is_cancelled():
            return []

        # Filter out unique sizes (cannot be duplicates)
        candidates = {sz: paths for sz, paths in size_map.items() if len(paths) >= 2}
        total_candidates = sum(len(paths) for paths in candidates.values())

        if total_candidates == 0:
            if progress_callback:
                progress_callback(100, "Дублікатів не знайдено.")
            return []

        if progress_callback:
            progress_callback(30, f"Фільтрація за вибірковим хешем ({total_candidates} файлів-кандидатів)...")

        # Step 2: Sample Hash Check (64 KiB head+tail)
        sample_map: Dict[str, List[Path]] = {}
        processed_candidates = 0

        for sz, paths in candidates.items():
            for fp in paths:
                if self.is_cancelled():
                    return []
                processed_candidates += 1
                if progress_callback and processed_candidates % 50 == 0:
                    pct = 30 + int((processed_candidates / total_candidates) * 30)
                    progress_callback(pct, f"Вибіркова перевірка ({processed_candidates}/{total_candidates})...")

                if sz <= 128 * 1024:
                    # Small files: group by size directly into full hash phase
                    sample_map.setdefault(f"sz_{sz}", []).append(fp)
                else:
                    sample_h = compute_sample_hash(fp, sample_size=64 * 1024)
                    sample_map.setdefault(f"{sz}_{sample_h}", []).append(fp)

        # Filter candidate groups with >= 2 matching sample hashes
        full_hash_groups = [paths for paths in sample_map.values() if len(paths) >= 2]
        full_hash_candidates = sum(len(paths) for paths in full_hash_groups)

        if progress_callback:
            progress_callback(60, f"Обчислення BLAKE3-хешів ({full_hash_candidates} файлів)...")

        # Step 3: Full BLAKE3 checksum calculation
        final_hash_map: Dict[str, List[DuplicateFile]] = {}
        hashed_count = 0

        for paths in full_hash_groups:
            for fp in paths:
                if self.is_cancelled():
                    return []
                hashed_count += 1
                if progress_callback and hashed_count % 10 == 0:
                    pct = 60 + int((hashed_count / max(1, full_hash_candidates)) * 35)
                    progress_callback(pct, f"BLAKE3 верифікація ({hashed_count}/{full_hash_candidates}) — {fp.name}...")

                h = compute_blake3_hash(fp)
                if not h:
                    continue
                try:
                    st = fp.stat()
                    dup_file = DuplicateFile(path=str(fp.resolve()), size_bytes=st.st_size, mtime=st.st_mtime)
                    final_hash_map.setdefault(h, []).append(dup_file)
                except (OSError, PermissionError):
                    continue

        # Step 4: Build duplicate groups
        result_groups: List[DuplicateGroup] = []
        for h, files in final_hash_map.items():
            if len(files) >= 2:
                # Deduplicate exact same path if scanned multiple times
                seen_paths = set()
                unique_files = []
                for f in files:
                    if f.path.lower() not in seen_paths:
                        seen_paths.add(f.path.lower())
                        unique_files.append(f)
                if len(unique_files) >= 2:
                    result_groups.append(DuplicateGroup(
                        hash_blake3=h,
                        size_bytes=unique_files[0].size_bytes,
                        files=sorted(unique_files, key=lambda f: f.mtime)
                    ))

        # Sort groups by wasted disk space descending
        result_groups.sort(key=lambda g: g.wasted_bytes, reverse=True)

        if progress_callback:
            total_wasted = sum(g.wasted_bytes for g in result_groups)
            mb = total_wasted / (1024 * 1024)
            progress_callback(100, f"Готово! Знайдено {len(result_groups)} груп дублікатів (займають {mb:.2f} МБ).")

        return result_groups
