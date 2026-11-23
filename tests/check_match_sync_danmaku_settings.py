"""Check independent overlay settings and the host's playback popup material."""
import argparse
from copy import deepcopy
from importlib import import_module
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch


def check_material(app, host, menu, button, output):
    from ddm import theme
    from ddm.settings_material import BASE_COLORS
    from PySide6.QtCore import Qt

    menu.setAttribute(Qt.WA_DontShowOnScreen)
    try:
        for mode in ('dark', 'light'):
            theme.apply(mode)
            for enabled, opacity, expected_alpha in ((False, 0, 255), (True, 0, 214), (True, 100, 255)):
                host.settings.update(settings_glass_enabled=enabled, settings_glass_opacity=opacity)
                button.click()
                app.processEvents()
                assert menu.isVisible() and menu.width() > 300 and menu.height() < 600
                assert button.screen().availableGeometry().contains(menu.geometry())
                image = menu.grab().toImage()
                color = image.pixelColor(image.width() // 2, image.height() - 4)
                expected = theme.qcolor(BASE_COLORS[theme.is_light()]['background'] if enabled else theme.ELEVATED)
                assert abs(color.alpha() - expected_alpha) <= 2, (mode, enabled, opacity, color.getRgb())
                assert all(abs(actual - target) <= 2
                           for actual, target in zip(color.getRgb()[:3], expected.getRgb()[:3])), color.getRgb()
                if output and mode == 'dark' and enabled and opacity == 0:
                    image.save(str(output))
                menu.hide()
    finally:
        theme.apply('dark')


def check(app, host, module, output=None):
    from ddm import plugins
    from ddm.widgets import Tile
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QCheckBox, QLabel
    own = import_module(module.__package__ + '.danmaku_settings')
    host.settings = dict(video_danmaku_color_mode='custom', video_danmaku_color='#ffffff',
        video_danmaku_area=25, video_danmaku_size=20, video_danmaku_speed=90,
        video_danmaku_opacity=60, video_danmaku_outline_width=1, video_danmaku_scale=True,
        video_danmaku_scale_curve=[[0, 0], [720, 1], [1440, 4]],
        super_chat_enabled=False)
    host.rooms = []
    original = deepcopy(host.settings)
    host.open_settings = Mock(side_effect=AssertionError('Must not open global settings'))
    with tempfile.TemporaryDirectory() as directory:
        context = plugins.PluginContext(plugins.PluginManager(window=host, plugins_dir=directory), 'own_danmaku')
        viewer = module.Viewer(context, {})
        normal = Tile({}, host)
        reopened = None
        try:
            viewer.setAttribute(Qt.WA_DontShowOnScreen)
            viewer.resize(1500, 900)
            viewer.show()
            viewer.add_room('1', {'alias': 'Blue', 'color': '#38bdf8'})
            viewer.add_room('2', {'alias': 'Pink', 'color': '#fb7185'})
            menu = viewer.picture.danmaku_settings_menu
            overlay = viewer.video_danmaku
            assert isinstance(menu, own.MatchDanmakuSettings)
            assert viewer.picture.video_danmaku_overrides is not None
            assert overlay.settings['video_danmaku_color_mode'] == 'original'
            assert overlay.settings['video_danmaku_scale_curve'] is None
            assert overlay.settings['video_danmaku_size'] == 20
            assert overlay.settings['super_chat_enabled'] is False, 'Keep host super-chat settings'
            assert len(menu.findChildren(QCheckBox)) == 1, 'Only screen scaling; no global/independent switch'
            assert not any('缩放曲线' in label.text() for label in menu.findChildren(QLabel))
            assert not hasattr(menu, 'color_button') and not hasattr(menu, 'scale_curve')
            for row in viewer.rows.values():
                assert overlay._comment_color({'color': row.color}).name() == row.color
            normal.apply_video_danmaku_settings(host.settings)
            assert normal.video_danmaku._comment_color({'color': '#38bdf8'}).name() == '#ffffff'
            assert normal.video_danmaku_overrides is None
            assert hasattr(normal.danmaku_settings_menu, 'color_button'), 'Host menu remains intact'

            menu.controls['video_danmaku_size'].setValue(37)
            menu.controls['video_danmaku_outline_width'].setValue(0)
            menu.scale.setChecked(False)
            assert overlay.settings['video_danmaku_size'] == 37
            assert overlay.settings['video_danmaku_outline_width'] == 0
            assert menu.labels['video_danmaku_outline_width'].text() == '0 px'
            assert overlay.settings['video_danmaku_scale'] is False
            assert host.settings == original, 'Plugin sliders cannot write host settings'
            saved = context.setting('overlay_settings')
            assert saved['video_danmaku_size'] == 37 and saved['video_danmaku_color_mode'] == 'original'
            assert saved['video_danmaku_scale_curve'] is None

            # The public Tile API may reapply host settings; independent values still win.
            changed = dict(host.settings, video_danmaku_color='#00ff00', video_danmaku_size=55)
            viewer.picture.apply_video_danmaku_settings(changed)
            assert overlay.settings['video_danmaku_size'] == 37
            assert overlay._comment_color({'color': '#fb7185'}).name() == '#fb7185'
            viewer.select_main('2')
            assert viewer.picture.video_danmaku_overrides == saved
            assert overlay.settings['video_danmaku_color_mode'] == 'original'
            with patch.object(menu, 'open_at') as opened:
                viewer.picture.danmaku_settings_button.click()
                opened.assert_called_once()
                opened.reset_mock()
                viewer._open_danmaku_settings()
                opened.assert_called_once()
            host.open_settings.assert_not_called()
            check_material(app, host, menu, viewer.picture.danmaku_settings_button, output)
            reopened = module.Viewer(context, {})
            assert reopened.video_danmaku.settings['video_danmaku_size'] == 37
            assert reopened.video_danmaku.settings['video_danmaku_scale'] is False
            assert reopened.video_danmaku.settings['video_danmaku_color_mode'] == 'original'
            assert reopened.video_danmaku.settings['video_danmaku_scale_curve'] is None
        finally:
            if reopened is not None:
                reopened.close()
                reopened.deleteLater()
            normal.close()
            normal.deleteLater()
            viewer.close()
            viewer.deleteLater()
            app.processEvents()
    return dict(passed=True, room_colors_preserved=True, plugin_settings_persisted=True,
                host_settings_untouched=True, host_menu_preserved=True,
                no_curve_or_color_picker=True, local_button_entry=True, host_playback_material=True,
                host_reapply_and_main_switch=True, real_messages=0, media_playback=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.host), str(Path(__file__).resolve().parents[1])]
    os.environ['DDM_NO_SAVE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from plugins.match_sync import viewer as module
    app = QApplication([])
    module.theme.apply('dark')
    print(check(app, QWidget(), module, args.output))
