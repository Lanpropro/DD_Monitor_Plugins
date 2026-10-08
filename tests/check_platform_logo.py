"""后台验证插件专属 Logo 开关、保存/取消、重载和动态卡片布局。"""
import argparse
import copy
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=Path, required=True)
    args = parser.parse_args()
    host = args.host.resolve()
    sys.path.insert(0, str(host))
    os.chdir(host)
    os.environ["DDM_NO_SAVE"] = "1"
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication, QCheckBox, QWidget
    from ddm.dialogs import SettingsDialog
    from ddm.plugins import PluginManager
    from ddm.widgets import Sidebar
    app = QApplication([])
    plugin_dir = Path(__file__).resolve().parents[1] / "plugins"
    rooms = [{"room_id": "1", "platform": "bilibili", "uname": "B"}] + [
        {"room_id": kind + ":123", "platform": kind, "uname": kind}
        for kind in ("huya", "douyu", "douyin", "twitch", "youtube")]
    saved = {}
    for loaded in ([], ["domestic_live"], ["global_live"], ["domestic_live", "global_live"]):
        window = QWidget()
        window.resize(900, 900)
        window.sidebar = Sidebar(copy.deepcopy(rooms), window, auto_compact=False)
        window.sidebar.resize(300, 880)
        window.show()
        manager = PluginManager(window, str(plugin_dir), enabled=loaded)
        manager.load()
        assert len(manager.plugins) == len(loaded), manager.skipped
        snapshots = []
        manager._save_settings = lambda: snapshots.append(copy.deepcopy(manager.plugin_settings))
        def dialog():
            result = SettingsDialog({}, {}, window, plugin_manager=manager)
            result.show()
            app.processEvents()
            return result
        settings = dialog()
        for plugin_id in ("domestic_live", "global_live"):
            check = settings.general_page.findChild(QCheckBox, plugin_id + "_show_platform_logo")
            assert bool(check) == (plugin_id in loaded), "Unloaded plugin exposes control"
            assert settings.plugin_page.findChild(QCheckBox, plugin_id + "_show_platform_logo") is None, "Logo switch remains on plugin page"
            if check:
                grid = settings.general_page.layout().itemAt(1).layout()
                row = grid.getItemPosition(grid.indexOf(check))[0]
                alert_row = grid.getItemPosition(grid.indexOf(settings.general_page._checks["live_alert"]))[0]
                decode_row = grid.getItemPosition(grid.indexOf(settings.general_page.decode_mode))[0]
                assert alert_row < row < decode_row, "Logo switch is not below live alert"
                check.setChecked(False)
        if len(loaded) == 2:
            grid = settings.general_page.layout().itemAt(1).layout()
            domestic = settings.general_page.findChild(QCheckBox, "domestic_live_show_platform_logo")
            global_live = settings.general_page.findChild(QCheckBox, "global_live_show_platform_logo")
            assert grid.getItemPosition(grid.indexOf(domestic))[0] < grid.getItemPosition(grid.indexOf(global_live))[0]
        settings.reject()
        assert not manager.plugin_settings and not snapshots, "Cancel saved changes"
        settings = dialog()
        for plugin_id in loaded:
            settings.general_page.findChild(QCheckBox, plugin_id + "_show_platform_logo").setChecked(False)
        settings.accept()
        app.processEvents()
        for item in window.sidebar._items:
            disabled = ((item.room["platform"] in ("huya", "douyu", "douyin") and "domestic_live" in loaded)
                        or (item.room["platform"] in ("twitch", "youtube") and "global_live" in loaded))
            assert item.platform_badge.isHidden() == disabled, item.room
        # 宿主刷新、收起展开、列表/封面切换、新增关注均不能重新显示禁用的 Logo。
        window.sidebar._sync_count()
        window.sidebar.set_card_mode(False)
        for item in window.sidebar._items:
            item.set_compact(True)
            item.set_compact(False)
        for plugin_id, kind in (("domestic_live", "douyu"), ("global_live", "twitch")):
            if plugin_id in loaded:
                window.sidebar.add_room({"room_id": kind + ":new", "platform": kind, "uname": "new"})
                app.processEvents()
                item = next(i for i in window.sidebar._items if i.room["room_id"] == kind + ":new")
                assert item.platform_badge.isHidden(), "New card ignored disabled Logo"
        for item in window.sidebar._items:
            item.thumb._set_overlay_visible(True)
            disabled = ((item.room["platform"] in ("huya", "douyu", "douyin") and "domestic_live" in loaded)
                        or (item.room["platform"] in ("twitch", "youtube") and "global_live" in loaded))
            assert item.platform_badge.isHidden() == disabled, "Layout/preview lost Logo choice"
        settings = dialog()
        settings.hide()
        settings.show()
        for plugin_id in loaded:
            assert len(settings.general_page.findChildren(QCheckBox, plugin_id + "_show_platform_logo")) == 1
        settings.reject()
        saved = copy.deepcopy(manager.plugin_settings)
        manager.unload()
        app.processEvents()
        settings = dialog()
        assert not settings.general_page.findChildren(QCheckBox, "domestic_live_show_platform_logo")
        assert not settings.general_page.findChildren(QCheckBox, "global_live_show_platform_logo")
        settings.reject()
        manager = PluginManager(window, str(plugin_dir), enabled=loaded)
        manager.plugin_settings = saved
        manager.load()
        settings = dialog()
        for plugin_id in loaded:
            check = settings.general_page.findChild(QCheckBox, plugin_id + "_show_platform_logo")
            assert not check.isChecked(), "Reload lost saved choice"
        if len(loaded) == 2:
            settings.general_page.findChild(QCheckBox, "domestic_live_show_platform_logo").setChecked(True)
            settings.accept()
            app.processEvents()
            for item in window.sidebar._items:
                if item.room["platform"] in ("huya", "douyu", "douyin"):
                    assert not item.platform_badge.isHidden(), "Enable did not restore domestic Logo"
                elif item.room["platform"] in ("twitch", "youtube"):
                    assert item.platform_badge.isHidden(), "Domestic control changed overseas Logo"
        else:
            settings.reject()
        manager.unload()
        window.close()
        window.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        print("PASS: loaded", loaded, "settings, cancel/save, layouts, new cards, reload and independence")


if __name__ == "__main__":
    main()
