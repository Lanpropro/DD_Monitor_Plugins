"""Reuse capture metadata on the GUI thread without calling a provider."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch


def check(app, host, module):
    from ddm import plugins
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'quality_snapshot'), {})
        try:
            for key in ('1', '2'):
                viewer.add_room(key, {'alias': key})
                viewer.rows[key].decoder = module.Decoder(key)
            native, platform = viewer.rows.values()
            provider = Mock(side_effect=AssertionError('GUI queried platform metadata'))
            platform.platform = SimpleNamespace(kind='huya', room_quality_options=provider)
            viewer.running = True
            first = [{'qn': 10000, 'desc': 'native highest'}]
            viewer._information(native, native.decoder, {'quality_options': first, 'actual_quality': 10000})
            assert native.platform is None, 'Native Bilibili uses the host path'
            assert viewer.picture.quality_options == first and viewer.picture.actual_quality == 10000
            second = [{'qn': 20000, 'desc': '2K'}, {'qn': 400, 'desc': '720P'}]
            viewer._information(platform, platform.decoder, {'quality_options': second})
            assert viewer.picture.quality_options == first, 'Secondary metadata must not replace the main menu'
            second[0]['desc'] = 'changed after delivery'
            assert platform.quality_options[0]['desc'] == '2K', 'Own a snapshot of the delivered metadata'
            with patch.object(viewer, '_reload_picture') as reload:
                viewer.select_main(platform.room_id)
            assert reload.call_count == 1 and platform.quality == 20000, 'Keep highest-tier selection'
            assert viewer.picture.quality_options == platform.quality_options
            with patch.object(viewer, 'sync_picture', wraps=viewer.sync_picture) as sync:
                viewer._information(platform, platform.decoder, {'actual_quality': 400})
                assert sync.call_count == 1, 'One metadata event needs one main UI refresh'
            assert viewer.picture.actual_quality == 400
            viewer._information(platform, platform.decoder, {'title': 'new title'})
            assert viewer.picture.quality_options == platform.quality_options, 'Partial information keeps tiers'
            viewer._information(platform, object(), {'quality_options': []})
            assert platform.quality_options, 'Ignore stale decoder events'
            for _ in range(100):
                viewer.sync_picture()
            provider.assert_not_called()
            viewer._information(platform, platform.decoder, {'quality_options': []})
            assert platform.quality_options and viewer.picture.quality_options == platform.quality_options, \
                'Empty provider results keep the last known menu, as the native tile does'
            with patch.object(viewer, '_reload_picture'):
                viewer.select_main(native.room_id)
            assert viewer.picture.quality_options == first and viewer.picture.actual_quality == 10000
            return dict(passed=True, platform_calls_on_gui=0, native_path=True,
                        source_switch=True, highest_tier=True, partial_and_stale_events=True,
                        empty_keeps_last_known=True, metadata_refreshes_per_event=1)
        finally:
            viewer.close()
            viewer.deleteLater()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['DDM_NO_SAVE'] = '1'
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
