"""Path normalization, boundary enforcement, and routing validation."""

from pathlib import Path, PureWindowsPath
from typing import List, Tuple, Set


class PathValidator:
    """Guarantees strict filesystem boundaries, cycle detection, and collision prevention."""

    @staticmethod
    def normalize_rel_path(p: str) -> str:
        """Normalizes relative path using standard forward slashes, stripping leading/trailing slashes."""
        # Clean forward/backward slashes first
        clean = p.replace("\\", "/").strip("/")
        parts = [part for part in clean.split("/") if part and part != "."]
        safe_parts = [part for part in parts if not part.endswith(":") and not part.startswith("//")]
        return "/".join(safe_parts)

    @staticmethod
    def is_strictly_within_root(target_path: Path, root_path: Path) -> bool:
        """
        Validates that target_path is strictly inside or equal to root_path,
        preventing directory traversal attacks (e.g. '../').
        """
        try:
            target_res = target_path.resolve()
            root_res = root_path.resolve()
            target_res.relative_to(root_res)
            return True
        except ValueError:
            return False

    @staticmethod
    def validate_mappings(source_roots: List[str], dest_roots: List[str]) -> Tuple[bool, List[str]]:
        """
        Detects invalid mappings, source/destination overlap, routing cycles,
        and unintended recursive synchronization.
        """
        errors = []
        resolved_sources = [Path(s).resolve() for s in source_roots]
        resolved_dests = [Path(d).resolve() for d in dest_roots]

        # Check for overlap between sources and destinations
        for src in resolved_sources:
            for dst in resolved_dests:
                if src == dst:
                    errors.append(f"Джерело та призначення не можуть збігатися (Source and destination cannot be identical): {src}")
                try:
                    dst.relative_to(src)
                    errors.append(f"Папка призначення '{dst}' знаходиться всередині джерела '{src}' (Destination is inside source, risking infinite recursive loop).")
                except ValueError:
                    pass

                try:
                    src.relative_to(dst)
                    errors.append(f"Папка-джерело '{src}' знаходиться всередині призначення '{dst}' (Source is inside destination, risking nested sync conflicts).")
                except ValueError:
                    pass

        # Check for duplicate sources
        seen_srcs: Set[str] = set()
        for s in resolved_sources:
            str_s = str(s).lower()  # Windows case-insensitive
            if str_s in seen_srcs:
                errors.append(f"Duplicate source folder configured: {s}")
            seen_srcs.add(str_s)

        return (len(errors) == 0, errors)

    @staticmethod
    def detect_case_collisions(rel_paths: List[str]) -> List[Tuple[str, str]]:
        """
        Detects Windows case-insensitive filename collisions,
        e.g., 'File.txt' and 'file.txt' mapped to the same destination.
        """
        collisions = []
        seen = {}
        for p in rel_paths:
            lower = p.lower()
            if lower in seen:
                collisions.append((seen[lower], p))
            else:
                seen[lower] = p
        return collisions
