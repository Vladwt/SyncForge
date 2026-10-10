"""Safe file versioning and archive storage."""

import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import structlog
from syncforge.core.models import SyncProfile
from syncforge.core.hasher import compute_blake3_hash

logger = structlog.get_logger(__name__)


class VersionManager:
    """Manages historical file versions in a dedicated subfolder."""

    def __init__(self, dest_root: Path, version_subfolder: str = ".syncforge_versions"):
        self.dest_root = dest_root.resolve()
        self.version_dir = self.dest_root / version_subfolder

    def archive_version(self, target_file: Path, rel_path: str, max_versions: int = 10) -> Optional[Path]:
        """
        Creates a timestamped snapshot of target_file prior to overwrite.
        Rotates versions so that total does not exceed max_versions.
        """
        if not target_file.exists():
            return None

        self.version_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target_name = target_file.name
        archive_name = f"{target_name}.{timestamp}.bak"
        
        # Mirror relative parent folder in versions directory
        parent_rel = Path(rel_path).parent
        save_dir = self.version_dir / parent_rel
        save_dir.mkdir(parents=True, exist_ok=True)

        archive_path = save_dir / archive_name

        try:
            shutil.copy2(str(target_file), str(archive_path))
            logger.info("Archived file version", src=str(target_file), archive=str(archive_path))
            self._prune_versions(save_dir, target_name, max_versions)
            return archive_path
        except Exception as e:
            logger.error("Failed to archive file version", error=str(e), file=str(target_file))
            return None

    def list_versions(self, rel_path: str) -> List[Path]:
        """Lists archived historical versions of a relative path sorted newest first."""
        parent_rel = Path(rel_path).parent
        save_dir = self.version_dir / parent_rel
        if not save_dir.exists():
            return []

        target_name = Path(rel_path).name
        versions = []
        for f in save_dir.glob(f"{target_name}.*.bak"):
            versions.append(f)

        versions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return versions

    def restore_version(self, archive_file: Path, restore_destination: Path) -> bool:
        """Restores an archived version to its active path."""
        if not archive_file.exists():
            return False

        restore_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(archive_file), str(restore_destination))
        logger.info("Restored historical file version", archive=str(archive_file), to=str(restore_destination))
        return True

    def _prune_versions(self, save_dir: Path, target_name: str, max_versions: int):
        """Enforces max_versions per file, pruning oldest backups."""
        versions = sorted(save_dir.glob(f"{target_name}.*.bak"), key=lambda p: p.stat().st_mtime)
        while len(versions) > max_versions:
            oldest = versions.pop(0)
            try:
                oldest.unlink(missing_ok=True)
                logger.debug("Pruned old file version", file=str(oldest))
            except Exception:
                pass
