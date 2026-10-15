"""Dashboard page component for SyncForge."""

from typing import Callable, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton
)
from syncforge.core.models import PlanAction
from syncforge.i18n.translations import tr
from syncforge.storage.repositories import ProfileRepository
from syncforge.devices.device_service import DeviceService


class DashboardPage(QWidget):
    """High-level metrics overview and quick action triggers."""

    def __init__(
        self,
        profile_repo: ProfileRepository,
        device_service: DeviceService,
        on_create_profile: Callable[[], None],
        on_switch_page: Callable[[str], None],
        get_current_plan_conflicts: Callable[[], int],
        on_open_duplicates: Optional[Callable[[], None]] = None,
        parent=None
    ):
        super().__init__(parent)
        self.profile_repo = profile_repo
        self.device_service = device_service
        self.on_create_profile = on_create_profile
        self.on_switch_page = on_switch_page
        self.get_current_plan_conflicts = get_current_plan_conflicts
        self.on_open_duplicates = on_open_duplicates
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(22)

        header = QLabel(tr("dashboard"))
        header.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(header)

        cards_layout = QHBoxLayout()
        self.card_profiles = self._create_card(tr("total_profiles"), "0")
        self.card_devices = self._create_card(tr("connected_devices"), "0")
        self.card_conflicts = self._create_card(tr("pending_conflicts"), "0")
        cards_layout.addWidget(self.card_profiles)
        cards_layout.addWidget(self.card_devices)
        cards_layout.addWidget(self.card_conflicts)
        layout.addLayout(cards_layout)

        action_box = QFrame()
        action_box.setProperty("class", "Card")
        ab_layout = QVBoxLayout(action_box)
        ab_title = QLabel(tr("quick_actions"))
        ab_title.setStyleSheet("font-weight: bold; font-size: 15px;")
        ab_layout.addWidget(ab_title)

        ab_btns = QHBoxLayout()
        quick_prof_btn = QPushButton("+ " + tr("create_profile"))
        quick_prof_btn.setProperty("class", "PrimaryBtn")
        quick_prof_btn.clicked.connect(self.on_create_profile)

        quick_preview_btn = QPushButton(tr("preview_plan"))
        quick_preview_btn.clicked.connect(lambda: self.on_switch_page("profiles"))

        quick_mgr_btn = QPushButton("📁 " + tr("file_manager"))
        quick_mgr_btn.clicked.connect(lambda: self.on_switch_page("file_manager"))

        quick_dup_btn = QPushButton("🔎 " + tr("duplicate_finder"))
        if self.on_open_duplicates:
            quick_dup_btn.clicked.connect(self.on_open_duplicates)
        else:
            quick_dup_btn.clicked.connect(lambda: self.on_switch_page("file_manager"))

        ab_btns.addWidget(quick_prof_btn)
        ab_btns.addWidget(quick_preview_btn)
        ab_btns.addWidget(quick_mgr_btn)
        ab_btns.addWidget(quick_dup_btn)
        ab_btns.addStretch()
        ab_layout.addLayout(ab_btns)
        layout.addWidget(action_box)

        layout.addStretch()

    def _create_card(self, title: str, val: str) -> QFrame:
        card = QFrame()
        card.setProperty("class", "Card")
        l = QVBoxLayout(card)
        t = QLabel(title)
        t.setStyleSheet("color: #949ba4; font-size: 13px;")
        v = QLabel(val)
        v.setStyleSheet("font-size: 26px; font-weight: bold; color: #5865f2;")
        l.addWidget(t)
        l.addWidget(v)
        card.value_lbl = v
        return card

    def refresh(self):
        """Updates summary statistics on dashboard cards."""
        profs = self.profile_repo.list_profiles()
        try:
            devs = self.device_service.discover_connected_volumes()
            devs_count = len(devs)
        except Exception:
            devs_count = 0
        self.card_profiles.value_lbl.setText(str(len(profs)))
        self.card_devices.value_lbl.setText(str(devs_count))
        confls = self.get_current_plan_conflicts()
        self.card_conflicts.value_lbl.setText(str(confls))
