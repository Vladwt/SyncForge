"""Rule engine for inclusion/exclusion, path routing, and collision detection."""

import re
import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Pattern


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
    """Evaluates rules against scanned files with high-performance regex precompilation."""

    INTERNAL_EXCLUDES = (".syncforge_marker", ".syncforge_versions", ".git", "Thumbs.db", "desktop.ini")

    def __init__(self, rules: Optional[List[Rule]] = None):
        # Sort rules by priority descending
        self.rules = sorted(rules or [], key=lambda r: r.priority, reverse=True)
        self.include_rules = [r for r in self.rules if r.enabled and r.rule_type == RuleType.INCLUDE_GLOB]
        self.has_include_rules = len(self.include_rules) > 0
        self.has_rules = len(self.rules) > 0

        # Precompile glob patterns into regexes for maximum throughput
        self._compiled_regexes: Dict[str, Tuple[Pattern, Pattern, Optional[str]]] = {}
        for r in self.rules:
            if r.enabled and r.rule_type in (RuleType.EXCLUDE_GLOB, RuleType.INCLUDE_GLOB, RuleType.ROUTE_FOLDER, RuleType.ROUTE):
                norm_pat = r.pattern.replace("\\", "/")
                # Pattern for full relative path
                path_re = re.compile(fnmatch.translate(norm_pat))
                # Pattern for filename only (only if pattern has no directory slashes)
                fn_re = re.compile(fnmatch.translate(norm_pat)) if "/" not in norm_pat else None
                # Base prefix for wildcard directories like "node_modules/*"
                base_pat = norm_pat.rstrip("/*") if (norm_pat.endswith("/*") or norm_pat.endswith("/**")) else None
                self._compiled_regexes[r.id] = (path_re, fn_re, base_pat)

    def should_include(self, rel_path: str, size_bytes: int) -> Tuple[bool, str]:
        """
        Determines if a file should be included or excluded.
        Returns: (is_included, reason)
        """
        norm_path = rel_path.replace("\\", "/")

        # Fast check for SyncForge internal and OS metadata files
        for ign in self.INTERNAL_EXCLUDES:
            if ign in norm_path:
                parts = norm_path.split("/")
                if ign in parts:
                    return False, f"Internal directory or metadata file '{ign}' excluded"

        if not self.has_rules:
            return True, "Passed all filter rules"

        filename = norm_path.rsplit("/", 1)[-1]

        # Check exclusions first
        for rule in self.rules:
            if not rule.enabled:
                continue

            if rule.rule_type == RuleType.EXCLUDE_GLOB:
                if self._match_precompiled(norm_path, filename, rule.id, rule.pattern):
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
        if self.has_include_rules:
            matched = any(
                self._match_precompiled(norm_path, filename, r.id, r.pattern)
                for r in self.include_rules
            )
            if not matched:
                return False, "Did not match any active include rule"

        return True, "Passed all filter rules"

    def determine_destination_path(self, rel_path: str) -> str:
        """
        Applies routing rules if any match, or preserves relative path structure.
        """
        if not self.has_rules:
            return rel_path

        norm_path = rel_path.replace("\\", "/")
        filename = norm_path.rsplit("/", 1)[-1]

        for rule in self.rules:
            if not rule.enabled or rule.rule_type not in (RuleType.ROUTE_FOLDER, RuleType.ROUTE):
                continue

            if self._match_precompiled(norm_path, filename, rule.id, rule.pattern):
                target_folder = (rule.target_subfolder or "").strip("/\\")
                if target_folder:
                    return f"{target_folder}/{filename}"
                return filename

        return norm_path

    def _match_precompiled(self, path_str: str, filename: str, rule_id: str, pattern: str) -> bool:
        """Fast regex match using precompiled patterns with fallback."""
        compiled = self._compiled_regexes.get(rule_id)
        if compiled:
            path_re, fn_re, base_pat = compiled
            if path_re.match(path_str):
                return True
            if fn_re and fn_re.match(filename):
                return True
            if base_pat and (path_str.startswith(base_pat + "/") or path_str == base_pat):
                return True
            return False

        # Fallback if rule wasn't precompiled
        return self._match_glob(path_str, pattern)

    @staticmethod
    def _match_glob(path_str: str, pattern: str) -> bool:
        """Matches a glob pattern against normalized forward slash path."""
        norm_pat = pattern.replace("\\", "/")
        filename = path_str.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(path_str, norm_pat) or fnmatch.fnmatch(filename, norm_pat):
            return True
        if norm_pat.endswith("/*") or norm_pat.endswith("/**"):
            base_pat = norm_pat.rstrip("/*")
            if path_str.startswith(base_pat + "/") or path_str == base_pat:
                return True
        return False

