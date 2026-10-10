"""BLAKE3 file hashing with streaming and chunking."""

from pathlib import Path
from typing import Optional, Callable
import blake3

CHUNK_SIZE = 1024 * 1024  # 1 MiB chunk for streaming read


def compute_blake3_hash(file_path: Path, progress_callback: Optional[Callable[[int], None]] = None) -> str:
    """
    Computes a streaming BLAKE3 hexadecimal hash of a file without reading the whole file into RAM.
    """
    hasher = blake3.blake3()
    with open(str(file_path), "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
            if progress_callback:
                progress_callback(len(chunk))
    return hasher.hexdigest()
