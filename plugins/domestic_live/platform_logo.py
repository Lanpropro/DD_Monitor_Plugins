"""Platform badge setting and presentation; QObject is owned by the host window."""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel
from ddm.dialogs import SettingsDialog

class PlatformLogoControl(QObject):
    """插件自己的设置入口与关注卡片标识；不修改本体类。"""
    def __init__(self, context, platforms, label):
        super().__init__(context.window)
        self.context = context
        self.platforms = platforms
        self.label = label
        self.name = context.name + "_show_platform_logo"
        self.enabled = bool(context.setting("show_platform_logo", True))
        QApplication.instance().installEventFilter(self)
        self.refresh()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Show and isinstance(watched, SettingsDialog):
            manager = (watched._plugin_manager if hasattr(watched, "_plugin_manager") else
                       watched.plugin_page.manager)
            if manager is self.context.manager:
                page = watched.general_page
                if page.findChild(QCheckBox, self.name) is None:
                    anchor = next((page._checks[key] for key in ("sidebar_platform_logos", "live_alert")
                                   if key in page._checks), None)
                    if anchor is None:
                        return False
                    grid = page.layout().itemAt(1).layout()
                    row = grid.getItemPosition(grid.indexOf(anchor))[0] + 1
                    check = QCheckBox(self.label, page)
                    check.setObjectName(self.name)
                    check.setChecked(self.enabled)
                    for index in range(grid.count() - 1, -1, -1):
                        old_row, column, rows, columns = grid.getItemPosition(index)
                        if old_row >= row:
                            item = grid.takeAt(index)
                            grid.addItem(item, old_row + 1, column, rows, columns)
                    grid.addWidget(check, row, 0, 1, 2)
                    watched.accepted.connect(lambda: self.save(check.isChecked()))
        elif (event.type() in (QEvent.Show, QEvent.ShowToParent, QEvent.DynamicPropertyChange)
                and isinstance(watched, QLabel) and watched.objectName() == "NavPlatformBadge"
                and not self.enabled):
            parent = watched.parentWidget()
            while parent is not None and parent.objectName() != "NavItem":
                parent = parent.parentWidget()
            if parent is not None and parent.window() is self.context.window:
                room = parent.room
                platform = ("bilibili" if str(room.get("room_id", "")).isdigit() else
                            room.get("platform") or str(room.get("room_id", "")).partition(":")[0])
                if platform in self.platforms:
                    if watched.property("showPlatform") is not False:
                        watched.setProperty("showPlatform", False)
                    watched.hide()
        return False

    def save(self, enabled):
        self.enabled = bool(enabled)
        self.context.set_setting("show_platform_logo", self.enabled)
        self.refresh()

    def refresh(self):
        sidebar = self.context.window.sidebar
        # 沿用宿主的单平台、悬停预览和紧凑布局规则。
        sidebar._sync_count()
        if not self.enabled:
            for item in sidebar._items:
                room = item.room
                platform = ("bilibili" if str(room.get("room_id", "")).isdigit() else
                            room.get("platform") or str(room.get("room_id", "")).partition(":")[0])
                if platform in self.platforms:
                    item.platform_badge.setProperty("showPlatform", False)
                    item.platform_badge.hide()
                    item.thumb._layout_overlay()

    def detach(self):
        QApplication.instance().removeEventFilter(self)
        for check in self.context.window.findChildren(QCheckBox, self.name):
            check.hide()
            check.deleteLater()
        self.enabled = True
        self.refresh()
        self.deleteLater()
