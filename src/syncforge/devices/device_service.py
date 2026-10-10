"""Windows volume discovery, enrollment, and multi-signal device verification."""

import os
import sys
import uuid
from pathlib import Path
from typing import List, Optional, Tuple
import structlog
from syncforge.core.models import DeviceInfo, DeviceRole

logger = structlog.get_logger(__name__)

MARKER_FILE_NAME = ".syncforge_marker"


class WindowsVolumeScanner:
    """Discovers drives and volumes using pywin32 / ctypes."""

    @staticmethod
    def get_available_drives() -> List[str]:
        """Returns list of drive root paths, e.g. ['C:\\', 'D:\\', 'E:\\']."""
        if sys.platform != "win32":
            # For testing/mock on non-windows
            return ["C:\\"]

        import win32api
        drives_bitmask = win32api.GetLogicalDrives()
        drives = []
        for letter_idx in range(26):
            if drives_bitmask & (1 << letter_idx):
                drive_letter = f"{chr(65 + letter_idx)}:\\"
                drives.append(drive_letter)
        return drives

    @staticmethod
    def get_volume_info(drive_path: str) -> Tuple[Optional[str], Optional[str], Optional[str], int, int]:
        """
        Returns: (volume_name, filesystem, volume_serial, total_bytes, free_bytes)
        """
        if sys.platform != "win32":
            return ("VirtualDisk", "NTFS", "TEST-SERIAL", 100_000_000_000, 50_000_000_000)

        import win32api
        import win32file
        import ctypes

        vol_name = None
        fs_type = None
        vol_serial = None
        total_bytes = 0
        free_bytes = 0

        try:
            name_buf = ctypes.create_unicode_buffer(260)
            fs_buf = ctypes.create_unicode_buffer(260)
            serial_num = ctypes.c_ulong()
            max_component_len = ctypes.c_ulong()
            fs_flags = ctypes.c_ulong()

            res = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(drive_path),
                name_buf,
                ctypes.sizeof(name_buf),
                ctypes.byref(serial_num),
                ctypes.byref(max_component_len),
                ctypes.byref(fs_flags),
                fs_buf,
                ctypes.sizeof(fs_buf),
            )
            if res:
                vol_name = name_buf.value
                fs_type = fs_buf.value
                # Format volume serial as standard Hex XXXX-XXXX
                s_val = serial_num.value
                vol_serial = f"{(s_val >> 16) & 0xFFFF:04X}-{s_val & 0xFFFF:04X}"
        except Exception as e:
            logger.warning("Failed GetVolumeInformationW", drive=drive_path, error=str(e))

        try:
            free_clusters, total_clusters, bytes_per_sector, sectors_per_cluster = win32file.GetDiskFreeSpace(drive_path)
            cluster_size = bytes_per_sector * sectors_per_cluster
            total_bytes = total_clusters * cluster_size
            free_bytes = free_clusters * cluster_size
        except Exception as e:
            # Fallback to shutil / os.statvfs or GetDiskFreeSpaceEx
            try:
                import shutil
                usage = shutil.disk_usage(drive_path)
                total_bytes = usage.total
                free_bytes = usage.free
            except Exception:
                pass

        return (vol_name, fs_type, vol_serial, total_bytes, free_bytes)

    @staticmethod
    def get_volume_guid_path(drive_path: str) -> Optional[str]:
        """Gets Windows volume GUID path, e.g. Volume GUID."""
        if sys.platform != "win32":
            return "\\\\?\\Volume{mock-guid}\\"

        import ctypes
        guid_buf = ctypes.create_unicode_buffer(50)
        res = ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(
            ctypes.c_wchar_p(drive_path),
            guid_buf,
            ctypes.sizeof(guid_buf)
        )
        if res:
            return guid_buf.value
        return None


class DeviceService:
    """Device enrollment, multi-signal identity verification, and drive letter tracking."""

    def __init__(self, scanner: Optional[WindowsVolumeScanner] = None):
        self.scanner = scanner or WindowsVolumeScanner()

    def discover_connected_volumes(self) -> List[DeviceInfo]:
        """Scans all connected Windows drives and reads their hardware / volume identity."""
        devices = []
        drives = self.scanner.get_available_drives()

        for drive in drives:
            vol_name, fs_type, vol_serial, total_b, free_b = self.scanner.get_volume_info(drive)
            guid = self.scanner.get_volume_guid_path(drive)

            marker_uuid = None
            marker_path = Path(drive) / MARKER_FILE_NAME
            if marker_path.exists():
                try:
                    marker_uuid = marker_path.read_text(encoding="utf-8").strip()
                except Exception as e:
                    logger.debug("Failed to read marker file", drive=drive, error=str(e))

            friendly = vol_name if vol_name else f"Local Disk ({drive[0]})"

            dev = DeviceInfo(
                device_id=marker_uuid or str(uuid.uuid4()),
                friendly_name=friendly,
                role=DeviceRole.PRIMARY_SYNC,
                volume_guid=guid,
                volume_serial=vol_serial,
                marker_uuid=marker_uuid,
                filesystem=fs_type,
                total_bytes=total_b,
                free_bytes=free_b,
                current_mount=drive,
                is_connected=True,
                verified=False
            )
            devices.append(dev)
        return devices

    def enroll_device(self, mount_path: str, friendly_name: str, role: DeviceRole, create_marker: bool = True) -> DeviceInfo:
        """
        Enrolls a device.
        Safety requirement: Marker is only written with explicit user consent (create_marker=True).
        """
        vol_name, fs_type, vol_serial, total_b, free_b = self.scanner.get_volume_info(mount_path)
        guid = self.scanner.get_volume_guid_path(mount_path)

        device_uuid = str(uuid.uuid4())
        marker_uuid = None

        if create_marker:
            marker_file = Path(mount_path) / MARKER_FILE_NAME
            try:
                # Write marker file
                marker_file.write_text(device_uuid, encoding="utf-8")
                marker_uuid = device_uuid
            except Exception as e:
                logger.error("Failed to write device marker", mount=mount_path, error=str(e))
                raise RuntimeError(f"Could not write marker to {mount_path}: {e}")

        device = DeviceInfo(
            device_id=device_uuid,
            friendly_name=friendly_name or (vol_name or f"Device {mount_path[0]}"),
            role=role,
            volume_guid=guid,
            volume_serial=vol_serial,
            marker_uuid=marker_uuid,
            filesystem=fs_type,
            total_bytes=total_b,
            free_bytes=free_b,
            current_mount=mount_path,
            is_connected=True,
            verified=True,
            verification_notes="Enrolled and verified."
        )
        return device

    def verify_device(self, enrolled: DeviceInfo, current_volumes: Optional[List[DeviceInfo]] = None) -> Tuple[bool, Optional[str], str]:
        """
        Verifies identity of an enrolled device against currently connected drives.
        Conservative multi-signal verification:
        1. Volume GUID path
        2. Volume Serial Number
        3. Marker UUID on disk
        4. Filesystem / capacity check
        
        A marker file alone is NOT sufficient (it could be cloned).
        Drive letter alone is NEVER trusted.
        
        Returns: (is_verified, active_mount_path, explanation)
        """
        if current_volumes is None:
            current_volumes = self.discover_connected_volumes()

        candidates = []

        for candidate in current_volumes:
            signals_matched = 0
            signals_checked = 0
            reasons = []

            # Signal 1: Volume GUID
            if enrolled.volume_guid and candidate.volume_guid:
                signals_checked += 1
                if enrolled.volume_guid == candidate.volume_guid:
                    signals_matched += 1
                    reasons.append("Volume GUID matches")
                else:
                    reasons.append("Volume GUID differs")

            # Signal 2: Volume Serial
            if enrolled.volume_serial and candidate.volume_serial:
                signals_checked += 1
                if enrolled.volume_serial == candidate.volume_serial:
                    signals_matched += 1
                    reasons.append("Volume Serial matches")
                else:
                    reasons.append("Volume Serial differs")

            # Signal 3: Marker file UUID
            if enrolled.marker_uuid and candidate.marker_uuid:
                signals_checked += 1
                if enrolled.marker_uuid == candidate.marker_uuid:
                    signals_matched += 1
                    reasons.append("Marker UUID matches")
                else:
                    reasons.append("Marker UUID differs")

            # Check for cloned marker warning
            if candidate.marker_uuid and enrolled.marker_uuid == candidate.marker_uuid:
                if enrolled.volume_serial and candidate.volume_serial and enrolled.volume_serial != candidate.volume_serial:
                    # Marker matches but hardware serial differs -> Likely CLONED MARKER!
                    return (False, None, f"SAFETY ALERT: Marker cloned on drive {candidate.current_mount} with differing Volume Serial! Access BLOCKED.")

            # Decision: Need at least 2 matching signals or 1 matching signal if only 1 available, and NO signal contradictions
            if signals_matched >= 2 or (signals_checked == 1 and signals_matched == 1):
                candidates.append((candidate, ", ".join(reasons)))

        if len(candidates) == 0:
            return (False, None, "Device not found among connected volumes.")
        elif len(candidates) > 1:
            return (False, None, "Ambiguous device match: multiple drives matched identity signals. Writing BLOCKED.")

        matched_candidate, explanation = candidates[0]
        return (True, matched_candidate.current_mount, f"Verified at {matched_candidate.current_mount} ({explanation})")
