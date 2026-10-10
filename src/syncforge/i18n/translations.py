"""Centralized Ukrainian and English translations for SyncForge."""

TRANSLATIONS = {
    "uk": {
        "app_title": "SyncForge — Менеджер синхронізації та резервного копіювання",
        "dashboard": "Панель керування",
        "devices": "Пристрої",
        "profiles": "Профілі",
        "rules": "Правила",
        "preview": "Попередній перегляд",
        "conflicts": "Конфлікти",
        "history": "Історія",
        "settings": "Налаштування",
        
        # Dashboard cards
        "total_profiles": "Всього профілів",
        "connected_devices": "Підключені пристрої",
        "last_sync": "Остання синхронізація",
        "pending_conflicts": "Очікують вирішення",
        "quick_actions": "Швидкі дії",
        "run_sync_now": "Запустити синхронізацію",
        "preview_plan": "Створити попередній план",

        # Devices
        "device_name": "Назва пристрою",
        "mount_point": "Точка монтування",
        "volume_guid": "GUID тому",
        "volume_serial": "Серійний номер",
        "status": "Статус",
        "role": "Роль",
        "enroll_device": "Зареєструвати пристрій",
        "refresh_devices": "Оновити список пристроїв",
        "verified": "Перевірено",
        "unverified": "Не перевірено",
        "role_primary_sync": "Основний швидкий SSD",
        "role_backup_hdd": "Холодний архівний HDD",

        # Profiles
        "create_profile": "Створити профіль",
        "profile_name": "Назва профілю",
        "sync_mode": "Режим синхронізації",
        "mode_one_way": "Режим A: Безпечне одностороннє оновлення",
        "mode_bidirectional": "Режим B: Безпечна двостороння синхронізація",
        "mode_backup": "Режим C: Версійний холодний бекап",
        "source_folders": "Вихідні папки",
        "dest_device": "Цільовий пристрій",
        "dest_folder": "Цільова папка",

        # Preview & Table
        "source_file": "Вихідний файл",
        "dest_file": "Цільовий файл",
        "action": "Дія",
        "size": "Розмір",
        "reason": "Причина",
        "execute_plan": "Виконати план синхронізації",
        
        # Conflict buttons
        "keep_source": "Залишити версію ПК",
        "keep_dest": "Залишити версію SSD",
        "keep_both": "Зберегти обидві версії",
        "skip": "Пропустити",

        # History
        "job_time": "Час запуску",
        "job_status": "Результат",
        "files_count": "Оброблено файлів",
        "bytes_count": "Обсяг передачі",

        # Settings
        "language": "Мова інтерфейсу",
        "theme": "Тема оформлення",
        "dark": "Темна",
        "light": "Світла",
        "save_settings": "Зберегти налаштування"
    },
    "en": {
        "app_title": "SyncForge — File Synchronization & Backup Manager",
        "dashboard": "Dashboard",
        "devices": "Devices",
        "profiles": "Profiles",
        "rules": "Rules",
        "preview": "Preview",
        "conflicts": "Conflicts",
        "history": "History",
        "settings": "Settings",
        
        "total_profiles": "Total Profiles",
        "connected_devices": "Connected Devices",
        "last_sync": "Last Sync",
        "pending_conflicts": "Pending Conflicts",
        "quick_actions": "Quick Actions",
        "run_sync_now": "Run Sync Now",
        "preview_plan": "Generate Preview",

        "device_name": "Device Name",
        "mount_point": "Mount Point",
        "volume_guid": "Volume GUID",
        "volume_serial": "Volume Serial",
        "status": "Status",
        "role": "Role",
        "enroll_device": "Enroll Device",
        "refresh_devices": "Refresh Devices",
        "verified": "Verified",
        "unverified": "Unverified",
        "role_primary_sync": "Primary Sync SSD",
        "role_backup_hdd": "Cold Backup HDD",

        "create_profile": "Create Profile",
        "profile_name": "Profile Name",
        "sync_mode": "Sync Mode",
        "mode_one_way": "Mode A: Safe One-Way Update",
        "mode_bidirectional": "Mode B: Safe Bidirectional Sync",
        "mode_backup": "Mode C: Versioned Cold Backup",
        "source_folders": "Source Folders",
        "dest_device": "Target Device",
        "dest_folder": "Destination Folder",

        "source_file": "Source File",
        "dest_file": "Destination File",
        "action": "Action",
        "size": "Size",
        "reason": "Reason",
        "execute_plan": "Execute Sync Plan",

        "keep_source": "Keep PC Version",
        "keep_dest": "Keep SSD Version",
        "keep_both": "Keep Both Versions",
        "skip": "Skip",

        "job_time": "Job Time",
        "job_status": "Status",
        "files_count": "Files Processed",
        "bytes_count": "Bytes Transferred",

        "language": "Interface Language",
        "theme": "Theme",
        "dark": "Dark",
        "light": "Light",
        "save_settings": "Save Settings"
    }
}

CURRENT_LANG = "uk"  # Default Ukrainian as requested


def set_language(lang_code: str):
    global CURRENT_LANG
    if lang_code in TRANSLATIONS:
        CURRENT_LANG = lang_code


def tr(key: str) -> str:
    """Translates key to current language with fallback to English or key itself."""
    return TRANSLATIONS.get(CURRENT_LANG, {}).get(key, TRANSLATIONS["en"].get(key, key))
