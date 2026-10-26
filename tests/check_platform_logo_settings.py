"""Plugin logo controls follow the current/legacy general grid without loading host pages."""
import argparse
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    host = args.host.resolve()
    sys.path.insert(0, str(host))
    os.chdir(host)
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, qVersion
    from PySide6.QtWidgets import QApplication, QCheckBox, QWidget
    from ddm.config import DEFAULT_SETTINGS
    from ddm.dialogs import SettingsDialog

    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.sidebar = SimpleNamespace(_sync_count=Mock(), _items=[])
    manager = object()
    controls, values = [], {}
    for plugin_id, label, platforms in (
            ('domestic_live', '显示国内平台 Logo', ('bilibili', 'huya', 'douyu', 'douyin')),
            ('global_live', '显示海外平台 Logo', ('twitch', 'youtube'))):
        spec = importlib.util.spec_from_file_location('logo_test_' + plugin_id,
                                                     ROOT / 'plugins' / plugin_id / 'plugin.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        values[plugin_id] = True
        context = SimpleNamespace(window=owner, manager=manager, name=plugin_id,
            setting=lambda _key, default, pid=plugin_id: values.get(pid, default),
            set_setting=lambda _key, value, pid=plugin_id: values.__setitem__(pid, value))
        controls.append(module.PlatformLogoControl(context, platforms, label))

    def exercise(result, *, legacy=False, reset=False):
        dialog = SettingsDialog(dict(DEFAULT_SETTINGS), {}, owner, plugin_manager=manager, preview=True)
        page = dialog.general_page
        if legacy:
            # The older supported host placed live_alert on General rather than Notifications.
            page._checks['live_alert'] = page._checks.pop('sidebar_platform_logos')
            del dialog._plugin_manager
        anchor = page._checks['live_alert' if legacy else 'sidebar_platform_logos']
        grid = page.layout().itemAt(1).layout()
        # Match the screenshot's host-dialog path without constructing download/update workers.
        lazy_access = Mock(return_value=SimpleNamespace(manager=manager))
        try:
            with patch.object(SettingsDialog, 'plugin_page', property(lazy_access)):
                for control in reversed(controls):
                    control.eventFilter(dialog, QEvent(QEvent.Show))
                dialog.setAttribute(Qt.WA_DontShowOnScreen)
                dialog.show()
                app.processEvents()
                checks = [page.findChild(QCheckBox, control.name) for control in controls]
                assert all(check is not None and grid.indexOf(check) >= 0 for check in checks), \
                    'Plugin checkbox exists outside the grid and covers the page title'
                if not legacy:
                    assert lazy_access.call_count == 0, 'General must not construct Plugins just to check its manager'
                rows = [grid.getItemPosition(grid.indexOf(check))[0] for check in checks]
                assert rows == [grid.getItemPosition(grid.indexOf(anchor))[0] + 1,
                                grid.getItemPosition(grid.indexOf(anchor))[0] + 2]
                widgets = [grid.itemAt(i).widget() for i in range(grid.count())
                           if grid.itemAt(i).widget() and not grid.itemAt(i).widget().isHidden()]
                for index, first in enumerate(widgets):
                    assert not any(first.geometry().intersects(other.geometry()) for other in widgets[index + 1:]), \
                        'General grid controls overlap'
                for check in checks:
                    assert check.mapTo(page, QPoint()).y() > anchor.mapTo(page, QPoint()).y()
                    assert len(page.findChildren(QCheckBox, check.objectName())) == 1
                    check.setChecked(False)
                if reset:
                    # Resetting the host's controls must leave the plugin grid intact.
                    page.reset()
                # Hide/show must not duplicate either control or its accepted signal.
                dialog.hide()
                dialog.show()
                app.processEvents()
                assert all(len(page.findChildren(QCheckBox, control.name)) == 1 for control in controls)
                for control in controls:
                    control.save = Mock(wraps=control.save)
                dialog.done(result)
                assert all(control.save.call_count == (1 if result == SettingsDialog.Accepted else 0)
                           for control in controls)
        finally:
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()

    try:
        exercise(SettingsDialog.Rejected)
        assert all(values.values()), 'Cancel must not save plugin settings'
        exercise(SettingsDialog.Accepted, reset=True)
        assert not any(values.values()), 'Save must persist each plugin independently'
        exercise(SettingsDialog.Rejected, legacy=True)
        # Other managers and the isolated settings worker preview have no plugin checkboxes.
        for other in (None, object()):
            dialog = SettingsDialog(dict(DEFAULT_SETTINGS), {}, owner, plugin_manager=other, preview=True)
            for control in controls:
                control.eventFilter(dialog, QEvent(QEvent.Show))
                assert dialog.general_page.findChild(QCheckBox, control.name) is None
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        print('PASS: Qt', qVersion(), 'current/legacy grids, both logo controls, cancel/save/reset/reopen and worker isolation')
    finally:
        for control in controls:
            control.detach()
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    main()
