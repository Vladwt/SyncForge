"""Filesystem scanner with NTFS junction detection and boundary enforcement."""

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Callable
import structlog
from syncforge.core.models import FileMetadata

logger = structlog.get_logger(__name__)


class FileScanner:
    """Safe filesystem traversal protecting against recursion loops, symlinks and junctions."""

    def __init__(self, follow_symlinks: bool = False):
        self.follow_symlinks = follow_symlinks

    def scan_directory(
        self,
        root_dir: str,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, FileMetadata]:
        """
        Scans a directory tree safely.
        Returns a mapping: {rel_path: FileMetadata}
        rel_path is normalized with forward slashes.
        """
        root_path = Path(root_dir).resolve()
        results: Dict[str, FileMetadata] = {}

        if not root_path.exists() or not root_path.is_dir():
            logger.warning("Root directory does not exist or is not a directory", path=str(root_path))
            return results

        # Track visited real directory paths to prevent infinite loops (NTFS junctions / symlinks)
        visited_dirs = set()
        visited_dirs.add(str(root_path))

        for dirpath, dirnames, filenames in os.walk(str(root_path), followlinks=False):
            curr_dir = Path(dirpath)

            # Filter out and guard against recursive junctions / symlinks in subdirectories
            safe_dirnames = []
            for d in dirnames:
                sub_dir_path = curr_dir / d
                is_reparse = False
                try:
                    if sub_dir_path.is_symlink():
                        is_reparse = True
                    # Check Windows junction / reparse point
                    if sys.platform == "win32":
                        import stat
                        st = os.lstat(str(sub_dir_path))
                        if hasattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT") and (st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                            is_reparse = True
                except Exception:
                    pass

                if is_reparse and not self.follow_symlinks:
                    logger.debug("Skipping junction or symlink directory for safety", path=str(sub_dir_path))
                    continue

                real_p = str(sub_dir_path.resolve())
                if real_p in visited_dirs:
                    logger.warning("Recursive directory loop detected, pruning traversal", path=str(sub_dir_path))
                    continue

                # Enforce boundary: must remain within root_path
                try:
                    Path(real_p).relative_to(root_path)
                except ValueError:
                    logger.warning("Junction/symlink targets directory outside approved root, skipping", path=str(sub_dir_path))
                    continue

                visited_dirs.add(real_p)
                safe_dirnames.append(d)

            # Mutate dirnames in place so os.walk does not traverse skipped directories
            dirnames[:] = safe_dirnames

            for f in filenames:
                file_path = curr_dir / f
                try:
                    # Windows long path support and lstat
                    st = os.stat(str(file_path))
                    is_symlink = file_path.is_symlink()
                    
                    # Compute relative path from root_path
                    rel_p = str(file_path.relative_to(root_path)).replace("\\", "/")

                    meta = FileMetadata(
                        rel_path=rel_p,
                        abs_path=str(file_path),
                        size_bytes=st.st_size,
                        mtime_ns=int(st.st_mtime_ns),
                        ctime_ns=int(st.st_ctime_ns if hasattr(st, "st_ctime_ns") else st.st_ctime * 1_000_000_000),
                        is_symlink=is_symlink,
                        is_junction=False,
                        is_readonly=not os.access(str(file_path), os.W_OK)
                    )
                    results[rel_p] = meta
                    if progress_callback and (len(results) % 20 == 0):
                        progress_callback(len(results), rel_p)
                except PermissionError as pe:
                    rel_p = str(file_path.relative_to(root_path)).replace("\\", "/")
                    results[rel_p] = FileMetadata(
                        rel_path=rel_p,
                        abs_path=str(file_path),
                        size_bytes=0,
                        mtime_ns=0,
                        ctime_ns=0,
                        error=f"Permission Denied: {pe}"
                    )
                except Exception as ex:
                    logger.warning("Failed to stat file", path=str(file_path), error=str(ex))

        if progress_callback:
            progress_callback(len(results), "")

        return results
