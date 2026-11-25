"""Exercise individual and bulk folder controls with an isolated, native host sidebar."""
import argparse
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def main(host):
    sys.path[:0] = [str(host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from dev.selfcheck_sidebar_folder_control import SidebarFolderControlTests
    from plugins.match_sync.panel_probe import PanelProbe

    case = SidebarFolderControlTests()
    case.setUpClass()
    case.setUp()
    sidebar = case.sidebar
    try:
        second = sidebar.create_folder('Events')
        sidebar.move_to_folder(['1001'], second)
        sidebar.toggle_folder(second)
        sidebar.set_folder_collapse_mode('previous')
        sidebar._bulk_fold_previous = {second}
        sidebar._folders_folded_by_drag.add(second)
        sidebar._search_expands_folders = False
        sidebar._refresh_folders(notify=False)
        sidebar.show()
        case.app.processEvents()

        def snapshot():
            return ([folder['collapsed'] for folder in sidebar.folders], set(sidebar._folders_folded_by_drag),
                    sidebar._search_expands_folders, sidebar._bulk_fold_previous and sidebar._bulk_fold_previous.copy(),
                    sidebar.navigation.folder_toggle_button.toolTip())

        before = snapshot()
        assert len(set(before[0])) == 2

        def click(button):
            assert button.isVisible() and button.isEnabled()
            button.click()
            return True

        bridge = SimpleNamespace(viewer=SimpleNamespace(window=lambda: SimpleNamespace(sidebar=sidebar)),
                                 buttons=SimpleNamespace(click=click))
        probe = SimpleNamespace(bridge=bridge)
        for bulk, count in ((False, 4), (True, 1)):
            result = PanelProbe.probe_folders(probe, bulk)
            assert result == dict(timings=[True] * count, restored=True), result
            assert snapshot() == before, (bulk, snapshot(), before)
        print('Folder probe restored individual, bulk, and auxiliary fold state')
    finally:
        sidebar.close()
        case.tearDown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    main(parser.parse_args().host)
