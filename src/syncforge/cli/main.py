"""SyncForge headless CLI for diagnostics, preview, and automated test runners."""

import sys
import argparse
from pathlib import Path
from syncforge.storage.database import Database
from syncforge.storage.repositories import ProfileRepository, DeviceRepository, BaselineRepository, HistoryRepository
from syncforge.devices.device_service import DeviceService
from syncforge.services.sync_orchestrator import SyncOrchestrator
from syncforge.core.models import SyncProfile, SyncMode, FolderMapping


def main():
    parser = argparse.ArgumentParser(description="SyncForge Diagnostics & CLI Tool")
    subparsers = parser.add_subparsers(dest="command")

    # Command: devices
    subparsers.add_parser("devices", help="List discovered connected storage devices and enrollment status")

    # Command: profiles
    subparsers.add_parser("profiles", help="List all configured profiles")

    # Command: preview
    preview_parser = subparsers.add_parser("preview", help="Generate dry-run synchronization preview")
    preview_parser.add_argument("--profile", required=True, help="Profile ID to preview")

    args = parser.parse_args()

    db = Database()
    device_service = DeviceService()
    dev_repo = DeviceRepository(db)
    prof_repo = ProfileRepository(db)
    base_repo = BaselineRepository(db)
    hist_repo = HistoryRepository(db)

    orchestrator = SyncOrchestrator(
        device_service=device_service,
        device_repo=dev_repo,
        baseline_repo=base_repo,
        history_repo=hist_repo
    )

    if args.command == "devices":
        volumes = device_service.discover_connected_volumes()
        print(f"\n--- Discovered Windows Volumes ({len(volumes)}) ---")
        for v in volumes:
            print(f"Drive: {v.current_mount} | Name: {v.friendly_name} | Serial: {v.volume_serial} | GUID: {v.volume_guid}")

    elif args.command == "profiles":
        profs = prof_repo.list_profiles()
        print(f"\n--- Configured Profiles ({len(profs)}) ---")
        for p in profs:
            print(f"ID: {p.id} | Name: {p.name} | Mode: {p.mode.value} | Mappings: {len(p.mappings)}")

    elif args.command == "preview":
        prof = prof_repo.get_profile(args.profile)
        if not prof:
            print(f"Error: Profile '{args.profile}' not found.")
            sys.exit(1)
        plan = orchestrator.prepare_dry_run(prof)
        print(f"\n--- Dry-Run Plan for Profile '{prof.name}' ({len(plan)} operations) ---")
        for op in plan:
            print(f"[{op.action.value.upper()}] {op.rel_path} -> {op.reason}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
