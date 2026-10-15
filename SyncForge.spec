# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

a = Analysis(
    ['src/syncforge/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'blake3',
        'watchdog',
        'structlog',
        'win32api',
        'win32file',
        'syncforge.i18n.translations',
        'syncforge.core.models',
        'syncforge.storage.database',
        'syncforge.devices.device_service',
        'syncforge.services.sync_orchestrator',
        'syncforge.rules.rule_engine',
        'syncforge.gui.main_window',
        'syncforge.gui.dialogs',
        'syncforge.gui.styles',
        'syncforge.gui.workers',
        'syncforge.gui.workers.sync_workers',
        'syncforge.gui.pages',
        'syncforge.gui.pages.dashboard_page',
        'syncforge.gui.pages.devices_page',
        'syncforge.gui.pages.profiles_page',
        'syncforge.gui.pages.rules_page',
        'syncforge.gui.pages.preview_page',
        'syncforge.gui.pages.conflicts_page',
        'syncforge.gui.pages.history_page',
        'syncforge.gui.pages.settings_page',
        'syncforge.gui.pages.faq_page',
        'syncforge.core.comparator',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SyncForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SyncForge',
)
