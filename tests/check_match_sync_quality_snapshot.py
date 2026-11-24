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
    from PySide6.QtWidgets import QMenu
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'quality_snapshot'), {})
        try:
            for key in ('1', '2'):
                viewer.add_room(key, {'alias': key, 'quality_auto': False})
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
            platform.quality = 400
            with patch.object(viewer, '_reload_picture') as reload:
                viewer.select_main(platform.room_id)
            assert reload.call_count == 0 and platform.quality == 400, 'Main selection must preserve the chosen quality'
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

            tiers = [{'qn': 10000, 'desc': '最高可用'}]
            douyu_tiers = [{'qn': 10000, 'desc': '原画'}, {'qn': 2000004, 'desc': '高清'}]

            def refused(_room, _quality):
                tiers[:] = douyu_tiers
                raise plugins.LoginRequiredError('fixture')

            douyu = SimpleNamespace(kind='douyu', label='斗鱼', playback_mode='stream',
                                    matches=lambda room: str(room).startswith('douyu:'), normalize=lambda room: room,
                                    room_info=Mock(return_value=None), play_url=Mock(side_effect=refused),
                                    room_quality_options=Mock(side_effect=lambda _room: list(tiers)))
            viewer.context.manager.platforms['douyu'] = douyu
            with patch.object(module.Decoder, 'start'), patch.object(viewer, '_start_chat'):
                assert viewer.add_room('douyu:3', {'quality': 10000, 'quality_auto': False})
            viewer.select_main('douyu:3')
            douyu_row = viewer.rows['douyu:3']
            decoder = douyu_row.decoder
            decoder.acquisition.begin(0)
            assert not viewer.picture._quality_choices()
            try:
                decoder._resolve(0)
            except plugins.LoginRequiredError:
                pass
            else:
                raise AssertionError('Fixture must reproduce the failed original-quality request')
            assert viewer.picture.quality_options == douyu_tiers and not viewer.picture.actual_quality

            menu_labels = []

            class QualityMenu(QMenu):
                def exec(self, _position):
                    actions = {action.text(): action for action in self.actions()}
                    menu_labels.extend(actions)
                    if '高清' in actions:
                        actions['高清'].trigger()

            lookup_count = douyu.room_quality_options.call_count
            with patch('ddm.tile_widgets.QMenu', QualityMenu), patch.object(viewer, '_reload_picture') as reload:
                viewer.picture.quality_button.click()
                reload.assert_called_once()
            assert set(menu_labels) == {'原画', '高清'}
            assert douyu_row.quality == 2000004 and not douyu_row.quality_auto
            assert douyu.room_quality_options.call_count == lookup_count

            def recovered(_room, quality):
                tiers[:] = douyu_tiers
                return 'https://private.invalid/fixture', quality, 'douyu', {}

            tiers[:] = [{'qn': 10000, 'desc': '最高可用'}]
            decoder.seed.update(quality=10000, highest_quality=True)
            decoder.acquisition.begin(1)
            douyu.play_url.side_effect = recovered
            decoder._resolve(1)
            assert viewer.picture.quality_options == douyu_tiers and viewer.picture.actual_quality == 10000

            tiers[:] = [{'qn': 10000, 'desc': '最高可用'}]
            huya_tiers = [{'qn': 1000000, 'desc': '原画'}, {'qn': 1004000, 'desc': '蓝光'}]

            def resolve_huya(_room, _quality):
                tiers[:] = huya_tiers
                return 'https://private.invalid/fixture', 1004000, 'huya', {}

            huya = SimpleNamespace(kind='huya', label='虎牙', playback_mode='stream',
                                   matches=lambda room: str(room).startswith('huya:'), normalize=lambda room: room,
                                   room_info=Mock(return_value=None), play_url=Mock(side_effect=resolve_huya),
                                   room_quality_options=Mock(side_effect=lambda _room: list(tiers)))
            viewer.context.manager.platforms['huya'] = huya
            with patch.object(module.Decoder, 'start'), patch.object(viewer, '_start_chat'):
                assert viewer.add_room('huya:3', {'quality': 10000})
                viewer.select_main('huya:3')
            huya_row = viewer.rows['huya:3']
            assert huya_row.decoder.seed['highest_quality']
            huya_row.decoder._resolve(0)
            assert viewer.picture.quality_options == huya_tiers
            assert viewer.picture.actual_quality == 1004000
            assert {quality for _, quality in viewer.picture._quality_choices()} == {1000000, 1004000}
            huya_row.decoder.video_size = (1920, 1080)
            huya_row.decoder.fps = 30.
            viewer.sync_picture()
            hint = viewer.picture.quality_button.toolTip()
            assert '1920×1080' in hint and '30 帧/秒' in hint and '原画' not in hint, hint

            return dict(passed=True, platform_calls_on_gui=0, native_path=True,
                        source_switch=True, selected_quality_preserved=True, partial_and_stale_events=True,
                        empty_keeps_last_known=True, metadata_refreshes_per_event=1,
                        douyu_menu_during_login_failure=True, douyu_refreshes_provisional_tiers=True,
                        huya_menu_after_cold_resolve=True, actual_huya_resolution_and_rate=True)
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
