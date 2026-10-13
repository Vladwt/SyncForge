"""BLAKE3 file hashing with streaming, chunking, I/O throttling, and sampling."""

import os
import time
from pathlib import Path
from typing import Optional, Callable
import blake3

CHUNK_SIZE = 128 * 1024  # 128 KiB default chunk for streaming read


def compute_blake3_hash(
    file_path: Path,
    progress_callback: Optional[Callable[[int], None]] = None,
    chunk_size: int = CHUNK_SIZE,
    yield_delay_ms: float = 0.0,
    speed_limit_mb: float = 0.0,
) -> str:
    """
    Computes a streaming BLAKE3 hexadecimal hash of a file.
    Supports I/O rate limiting and sleep yields between chunks to protect
    external SSDs/HDDs from thermal overload and bus disconnection.
    Safely handles unreadable/corrupted/locked files without crashing.
    """
    hasher = blake3.blake3()
    bytes_read_total = 0
    start_time = time.perf_counter()

    try:
        with open(str(file_path), "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                bytes_read_total += len(chunk)

                if progress_callback:
                    progress_callback(len(chunk))

                # I/O throttling: yield delay between chunks
                if yield_delay_ms > 0:
                    time.sleep(yield_delay_ms / 1000.0)

                # I/O rate limiting: speed cap in MB/s
                if speed_limit_mb > 0:
                    target_sec = (bytes_read_total / (speed_limit_mb * 1024 * 1024))
                    elapsed_sec = time.perf_counter() - start_time
                    if elapsed_sec < target_sec:
                        time.sleep(target_sec - elapsed_sec)

        return hasher.hexdigest()
    except (OSError, IOError, PermissionError):
        return ""


def compute_sample_hash(
    file_path: Path,
    sample_size: int = 64 * 1024,
) -> str:
    """
    Computes a fast heuristic BLAKE3 hash of file head and tail (up to sample_size each).
    Requires minimal I/O (reads <= 128 KiB regardless of file size).
    Safely returns empty string if the file is unreadable (e.g. disk sector error, lock, or disconnect).
    """
    hasher = blake3.blake3()
    try:
        file_size = os.path.getsize(str(file_path))
        with open(str(file_path), "rb") as f:
            head = f.read(sample_size)
            hasher.update(head)

            if file_size > sample_size * 2:
                try:
                    f.seek(file_size - sample_size)
                    tail = f.read(sample_size)
                    hasher.update(tail)
                except (OSError, ValueError):
                    pass

        return hasher.hexdigest()
    except (OSError, IOError, PermissionError):
        return ""
