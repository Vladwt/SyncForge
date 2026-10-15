"""Tests for DuplicateFinder engine and synthetic duplicate scenarios."""

import os
import tempfile
from pathlib import Path
from syncforge.core.duplicates import DuplicateFinder, DuplicateGroup


def test_duplicate_finder_exact_duplicates(tmp_path: Path):
    """Verifies that identical files across directories are accurately detected and grouped."""
    folder_a = tmp_path / "folder_a"
    folder_b = tmp_path / "folder_b"
    folder_a.mkdir()
    folder_b.mkdir()

    # Create identical files (size 256 bytes)
    content_dup = b"SyncForge Duplicate Test Content 1234567890" * 6
    file_1 = folder_a / "document1.txt"
    file_2 = folder_b / "document1_copy.txt"
    file_3 = folder_b / "sub_doc.txt"
    file_1.write_bytes(content_dup)
    file_2.write_bytes(content_dup)
    file_3.write_bytes(content_dup)

    # Create a unique file of different size
    unique_file = folder_a / "unique.txt"
    unique_file.write_bytes(b"Unique content that is completely different.")

    # Create two files with the same size but different contents
    diff_a = folder_a / "same_size_diff_a.dat"
    diff_b = folder_b / "same_size_diff_b.dat"
    diff_a.write_bytes(b"A" * 128)
    diff_b.write_bytes(b"B" * 128)

    finder = DuplicateFinder(min_size_bytes=10)
    groups = finder.find_duplicates(str(tmp_path), recursive=True)

    assert len(groups) == 1
    grp = groups[0]
    assert grp.size_bytes == len(content_dup)
    assert len(grp.files) == 3
    assert grp.wasted_bytes == len(content_dup) * 2

    paths = [os.path.normpath(f.path) for f in grp.files]
    assert os.path.normpath(str(file_1)) in paths
    assert os.path.normpath(str(file_2)) in paths
    assert os.path.normpath(str(file_3)) in paths


def test_duplicate_finder_min_size_filter(tmp_path: Path):
    """Verifies that min_size_bytes properly filters out small files."""
    tiny_dup1 = tmp_path / "tiny1.txt"
    tiny_dup2 = tmp_path / "tiny2.txt"
    tiny_dup1.write_bytes(b"tiny")
    tiny_dup2.write_bytes(b"tiny")

    finder = DuplicateFinder(min_size_bytes=1024)
    groups = finder.find_duplicates(str(tmp_path), recursive=True)
    assert len(groups) == 0


def test_duplicate_finder_non_recursive(tmp_path: Path):
    """Verifies non-recursive scan restricts detection to immediate directory."""
    sub = tmp_path / "sub"
    sub.mkdir()
    f1 = tmp_path / "root.txt"
    f2 = sub / "sub.txt"
    content = b"Non recursive test duplicate content" * 10
    f1.write_bytes(content)
    f2.write_bytes(content)

    finder = DuplicateFinder(min_size_bytes=10)
    # Non-recursive should only see root.txt, thus no duplicates
    groups = finder.find_duplicates(str(tmp_path), recursive=False)
    assert len(groups) == 0

    # Recursive finds them
    groups_rec = finder.find_duplicates(str(tmp_path), recursive=True)
    assert len(groups_rec) == 1
