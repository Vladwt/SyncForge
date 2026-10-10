"""Rule engine for inclusion/exclusion, path routing, and collision detection."""

import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import List, Optional, Tuple


class RuleType(str, Enum):
    EXCLUDE_GLOB = "exclude_glob"
    INCLUDE_GLOB = "include_glob"
    MAX_SIZE = "max_size"
    MIN_SIZE = "min_size"
    ROUTE_FOLDER = "route_folder"
    ROUTE = "route"


@dataclass
class Rule:
    """Ordered filtering or routing rule."""
    id: str
    rule_type: RuleType
    pattern: str                 # e.g., "*.tmp", "node_modules/**", "target/*", "*.pdf"
    target_subfolder: Optional[str] = None  # e.g. "Documents/PDF" for ROUTE_FOLDER
    priority: int = 0            # Higher priority runs first
    enabled: bool = True


class RuleEngine:
    """Evaluates rules against scanned files."""

    def __init__(self, rules: Optional[List[Rule]] = None):
        # Sort rules by priority descending
        self.rules = sorted(rules or [], key=lambda r: r.priority, reverse=True)

    def should_include(self, rel_path: str, size_bytes: int) -> Tuple[bool, str]:
        """
        Determines if a file should be included or excluded.
        Returns: (is_included, reason)
        """
        # Always exclude SyncForge metadata files
        norm_path = rel_path.replace("\\", "/")
        path_parts = norm_path.split("/")
        if any(part in [".syncforge_marker", ".syncforge_versions", ".git", "Thumbs.db", "desktop.ini"] for part in path_parts):
            if ".syncforge_marker" in path_parts or ".syncforge_versions" in path_parts:
                return False, "SyncForge internal directory or marker excluded"

        # Check explicit rules
        include_rules = [r for r in self.rules if r.enabled and r.rule_type == RuleType.INCLUDE_GLOB]
        has_include_rules = len(include_rules) > 0

        # Check exclusions first
        for rule in self.rules:
            if not rule.enabled:
                continue

            if rule.rule_type == RuleType.EXCLUDE_GLOB:
                if self._match_glob(norm_path, rule.pattern):
                    return False, f"Matched exclude pattern: {rule.pattern}"

            elif rule.rule_type == RuleType.MAX_SIZE:
                try:
                    max_bytes = int(rule.pattern)
                    if size_bytes > max_bytes:
                        return False, f"File size ({size_bytes} bytes) exceeds maximum limit ({max_bytes} bytes)"
                except ValueError:
                    pass

            elif rule.rule_type == RuleType.MIN_SIZE:
                try:
                    min_bytes = int(rule.pattern)
                    if size_bytes < min_bytes:
                        return False, f"File size ({size_bytes} bytes) below minimum limit ({min_bytes} bytes)"
                except ValueError:
                    pass

        # If there are positive include filters, file must match at least one
        if has_include_rules:
            matched = any(self._match_glob(norm_path, r.pattern) for r in include_rules)
            if not matched:
                return False, "Did not match any active include rule"

        return True, "Passed all filter rules"

    def determine_destination_path(self, rel_path: str) -> str:
        """
        Applies routing rules if any match, or preserves relative path structure.
        """
        norm_path = rel_path.replace("\\", "/")
        for rule in self.rules:
            if not rule.enabled or rule.rule_type not in (RuleType.ROUTE_FOLDER, RuleType.ROUTE):
                continue

            if self._match_glob(norm_path, rule.pattern):
                # Route file into target subfolder
                target_folder = (rule.target_subfolder or "").strip("/\\")
                filename = Path(norm_path).name
                if target_folder:
                    return f"{target_folder}/{filename}"
                return filename

        return norm_path

    @staticmethod
    def _match_glob(path_str: str, pattern: str) -> bool:
        """Matches a glob pattern against normalized forward slash path."""
        norm_pat = pattern.replace("\\", "/")
        # Match either exact filename or full relative path
        filename = Path(path_str).name
        if fnmatch.fnmatch(path_str, norm_pat) or fnmatch.fnmatch(filename, norm_pat):
            return True
        # Support directory wildcard patterns like "node_modules/*"
        if norm_pat.endswith("/*") or norm_pat.endswith("/**"):
            base_pat = norm_pat.rstrip("/*")
            if path_str.startswith(base_pat + "/") or path_str == base_pat:
                return True
        return False
