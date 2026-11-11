"""Automatic main quality tries available tiers; explicit selection stays authoritative."""
import argparse
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch


def check(app, host, module):
    from ddm import plugins
    media = import_module(module.__package__ + '.media')
    options = [{'qn': 1014100, 'desc': '2K HDR'}, {'qn': 1014000, 'desc': '2K'},
               {'qn': 1004000, 'desc': 'blue ray'}]
    calls = []
    def play_url(_room, quality):
        calls.append(('play', quality))
        if quality == 1014100:
            raise RuntimeError('HTTP 403: https://private.invalid/?signature=PRIVATE_FIXTURE')
        return 'https://private.invalid/video', 1004000 if quality == 250 else quality, 'huya', {}
    def quality_options(_room):
        calls.append(('metadata', None))
        return options
    platform = SimpleNamespace(kind='huya', room_info=Mock(return_value=None),
                               play_url=Mock(side_effect=play_url), room_quality_options=quality_options)
    decoder = module.Decoder('huya:fixture', {'quality': 250}, platform)
    try:
        for attempt in (0, 1):
            decoder.acquisition.begin(attempt)
            decoder._resolve(attempt)
            receipt = decoder.acquisition.snapshot()
            assert receipt['requested_quality'] == 250 and receipt['actual_quality'] == 1004000
        assert calls == [('play', 250), ('metadata', None)] * 2, 'Never promote auto to the first listed tier'
        decoder.seed['quality'] = 1014000
        decoder.acquisition.begin(2)
        decoder._resolve(2)
        assert platform.play_url.call_args.args == ('huya:fixture', 1014000)
        decoder.seed['quality'] = 1014100
        decoder.acquisition.begin(3)
        try:
            decoder._resolve(3)
        except RuntimeError as error:
            decoder.acquisition.error(error)
            decoder.acquisition.finish('error')
        else:
            raise AssertionError('Explicit restricted-tier failures must remain visible')
        receipt = decoder.acquisition.snapshot()
        assert receipt['requested_quality'] == 1014100 and 'actual_quality' not in receipt
        assert receipt['error_phase'] == 'play_url' and receipt['error_codes'] == ['http_403']
        assert 'PRIVATE_FIXTURE' not in json.dumps(receipt) and 'private.invalid' not in json.dumps(receipt)
        decoder.seed.update(quality=1014000, url='https://private.invalid/retained')
        with patch.object(platform, 'play_url', side_effect=AssertionError('Matching seed must be retained')):
            assert decoder._resolve(0)[0].endswith('/retained')
        decoder.seed.update(highest_quality=True, quality=250)
        calls.clear()
        decoder.acquisition.begin(4)
        decoder._resolve(0)
        assert calls == [('metadata', None), ('play', 1014100), ('play', 1014000)], calls
        assert decoder.seed['quality'] == 1014000 and 'url' not in decoder.seed
        assert decoder.acquisition.snapshot()['actual_quality'] == 1014000
        decoder.seed['balanced_quality'] = True
        calls.clear()
        decoder._resolve(0)
        assert [value for kind, value in calls if kind == 'play'] == [1014000], calls
        decoder.seed.pop('balanced_quality')
        with patch.object(platform, 'play_url', side_effect=RuntimeError('No supported tier')) as failed:
            try:
                decoder._resolve(1)
            except RuntimeError:
                pass
            else:
                raise AssertionError('All-tier failure must remain visible')
            assert [call.args[1] for call in failed.call_args_list] == [item['qn'] for item in options]
        with patch.object(platform, 'room_quality_options', side_effect=RuntimeError('metadata unavailable')):
            decoder.seed['quality'] = 250
            decoder._resolve(1)
            assert platform.play_url.call_args.args[1] == 250
        def interrupted(_room, _quality):
            decoder.cancelled.set()
            raise RuntimeError('cancel during acquisition')
        with patch.object(platform, 'play_url', side_effect=interrupted) as cancelled:
            try:
                decoder._resolve(1)
            except media.bili.Cancelled:
                pass
            else:
                raise AssertionError('Cancellation cannot trigger further tier requests')
            assert cancelled.call_count == 1
        decoder.cancelled.clear()
    finally:
        decoder.stop()
        decoder.events.deleteLater()
    native = module.Decoder('fixture', {'quality': 250, 'highest_quality': True})
    try:
        with patch.object(media.bili, 'room_info', return_value={}), \
                patch.object(media.bili, 'room_quality_options', return_value=[{'qn': 10000}, {'qn': 400}]), \
                patch.object(media.bili, 'play_url', side_effect=[RuntimeError('restricted'),
                    ('https://private.invalid/video', 400, {}, {})]) as fetch:
            native._resolve(0)
            assert [call.args[1] for call in fetch.call_args_list] == [10000, 400]
            assert native.seed['quality'] == 400
    finally:
        native.stop()
        native.events.deleteLater()
    host.settings, host.rooms = {}, []
    with tempfile.TemporaryDirectory() as directory:
        viewer = module.Viewer(plugins.PluginContext(
            plugins.PluginManager(window=host, plugins_dir=directory), 'quality_policy'), {})
        try:
            viewer.add_room('1', {'quality': 1014000, 'quality_auto': False})
            viewer.add_room('2', {'quality': 1004000, 'quality_auto': False})
            clock_button = viewer.rows['1'].clock_button
            clock_button.click()
            assert '至少一路副画面' in viewer.notice.text()
            assert not clock_button.text() and clock_button.size().width() == 32
            for row in viewer.rows.values():
                row.quality_options = [dict(item) for item in options]
                row.platform = platform
            with patch.object(viewer, '_reload_picture') as reload:
                viewer.select_main('2')
                viewer.select_main('1')
                assert not reload.called and [row.quality for row in viewer.rows.values()] == [1014000, 1004000]
                viewer._quality_changed(None, 1014100)
                assert viewer.rows['1'].quality == 1014100 and reload.call_count == 1
                assert not viewer.rows['1'].quality_auto
                assert not viewer.rows['1'].preferences()['quality_auto']
            with patch.object(module.source_control.Decoder, 'start'), patch.object(viewer, '_start_chat'):
                viewer._start_row(viewer.rows['1'])
                assert viewer.rows['1'].decoder.seed['quality'] == 1014100
                assert not viewer.rows['1'].decoder.seed['highest_quality']
                viewer.rows['1'].quality_auto = True
                viewer._start_row(viewer.rows['1'])
                assert viewer.rows['1'].decoder.seed['highest_quality']
            viewer.rows['1'].decoder.seed['highest_quality'] = False
            viewer.running = True
            with patch.object(viewer, '_reload_picture') as reload:
                viewer._main_changed()
                assert reload.call_count == 1
                viewer.rows['1'].decoder.seed['highest_quality'] = True
                viewer._main_changed()
                assert reload.call_count == 1, 'Do not reconnect an already automatic main source'
            viewer.running = False
            with patch.object(viewer.comparison_panel, 'open_clock') as opened:
                assert not clock_button.isHidden() and not viewer.compare.isChecked()
                clock_button.click()
                opened.assert_called_once()
            return dict(passed=True, main_preserves_tier=True, provider_auto_and_retry=True,
                        highest_available_fallback=True, row_clock_entry=True,
                        explicit_hdr_preserved=True, matching_seed_reused=True,
                        failed_tier_in_receipt=True, no_network=True, real_messages=0)
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
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    print(check(app, QWidget(), module))


if __name__ == '__main__':
    main()
