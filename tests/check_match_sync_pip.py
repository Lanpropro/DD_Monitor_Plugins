"""Picture-in-picture geometry for the match sync plugin; standard library only."""
from pathlib import Path
import sys
import inspect
import time
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.match_sync import pip  # noqa: E402


VIEWER = ROOT / 'plugins/match_sync/viewer.py'


class CropTests(unittest.TestCase):
    def test_clamp_crop_keeps_rect_inside_the_frame(self):
        self.assertEqual(pip.clamp_crop((0.2, 0.2, 0.3, 0.3)), (0.2, 0.2, 0.3, 0.3))
        self.assertEqual(pip.clamp_crop((-1, -1, 3, 3)), (0.0, 0.0, 1.0, 1.0))
        left, top, width, height = pip.clamp_crop((0.9, 0.95, 0.5, 0.5))
        self.assertAlmostEqual(left, 0.9)
        self.assertAlmostEqual(top, 0.95)
        self.assertLessEqual(left + width, 1.0)
        self.assertLessEqual(top + height, 1.0)

    def test_clamp_crop_falls_back_for_unusable_input(self):
        for value in (None, (), ('a', 'b', 'c', 'd'), (float('nan'), 0, 1, 1), (0, 0, 0, 0)):
            with self.subTest(value=value):
                self.assertEqual(pip.clamp_crop(value), pip.FULL)

    def test_crop_pixels_maps_to_the_frame(self):
        self.assertEqual(pip.crop_pixels((0.0, 0.0, 1.0, 1.0), 640, 360), (0, 0, 640, 360))
        self.assertEqual(pip.crop_pixels((0.5, 0.5, 0.5, 0.5), 640, 360), (320, 180, 320, 180))
        left, top, width, height = pip.crop_pixels((0.5, 0.5, 0.5, 0.5), 2, 2)
        self.assertGreaterEqual(width, 1)
        self.assertGreaterEqual(height, 1)
        self.assertLessEqual(left + width, 2)
        self.assertLessEqual(top + height, 2)


class InsetTests(unittest.TestCase):
    def test_every_corner_stays_inside_the_area(self):
        area = (100.0, 50.0, 1280.0, 720.0)
        for corner in pip.CORNERS:
            with self.subTest(corner=corner):
                x, y, width, height = pip.inset(area, 4 / 3, 0.28, corner, margin=16)
                self.assertGreaterEqual(x, area[0] + 16 - 1e-6)
                self.assertGreaterEqual(y, area[1] + 16 - 1e-6)
                self.assertLessEqual(x + width, area[0] + area[2] - 16 + 1e-6)
                self.assertLessEqual(y + height, area[1] + area[3] - 16 + 1e-6)
                self.assertAlmostEqual(width / height, 4 / 3, places=4)
                if corner.startswith('right'):
                    self.assertAlmostEqual(x + width, area[0] + area[2] - 16, places=4)
                else:
                    self.assertAlmostEqual(x, area[0] + 16, places=4)
                if corner.endswith('top'):
                    self.assertAlmostEqual(y, area[1] + 16, places=4)
                else:
                    self.assertAlmostEqual(y + height, area[1] + area[3] - 16, places=4)

    def test_width_ratio_is_measured_against_the_free_width(self):
        area = (0.0, 0.0, 1000.0, 1000.0)
        x, y, width, height = pip.inset(area, 1.0, 0.28, margin=20)
        self.assertAlmostEqual(width, 960 * 0.28, places=4)
        self.assertAlmostEqual(height, width, places=4)

    def test_ratio_is_clamped_to_the_supported_range(self):
        area = (0.0, 0.0, 1000.0, 500.0)
        narrow = pip.inset(area, 1.0, 0.01, margin=0)
        wide = pip.inset(area, 1.0, 0.99, margin=0)
        self.assertAlmostEqual(narrow[2], 1000 * pip.MIN_WIDTH, places=4)
        self.assertAlmostEqual(wide[2], 1000 * pip.MAX_WIDTH, places=4)

    def test_tall_crops_shrink_to_fit_the_height(self):
        x, y, width, height = pip.inset((0.0, 0.0, 1000.0, 300.0), 0.5, 0.45, margin=0)
        self.assertAlmostEqual(height, 300.0, places=4)
        self.assertAlmostEqual(width, 150.0, places=4)

    def test_impossible_areas_return_none(self):
        for args in (((0, 0, 0, 0), 1, .28), ((0, 0, 10, 10), 0, .28),
                     ((0, 0, 10, 10), 1, .28, 'right-bottom', 100)):
            with self.subTest(args=args):
                self.assertIsNone(pip.inset(*args))
        with self.assertRaises(TypeError):
            pip.inset('junk')

    def test_labels_exist_for_every_corner(self):
        self.assertEqual(set(pip.LABELS), set(pip.CORNERS))


class RectLikeTests(unittest.TestCase):
    """QRectF is not iterable; clamping must not silently fall back to FULL."""

    class FakeRect:
        def getRect(self):
            return (0.25, 0.1, 0.3, 0.4)

        def __iter__(self):
            raise TypeError('QRectF is not iterable')

    def test_clamp_crop_accepts_a_qt_style_rect(self):
        self.assertEqual(pip.clamp_crop(self.FakeRect()), (0.25, 0.1, 0.3, 0.4))

    def test_crop_pixels_accepts_a_qt_style_rect(self):
        self.assertEqual(pip.crop_pixels(self.FakeRect(), 1000, 500), (250, 50, 300, 200))

    def test_clamp_crop_still_rejects_junk(self):
        for value in (object(), 42, 'abcd'):
            with self.subTest(value=value):
                self.assertEqual(pip.clamp_crop(value), pip.FULL)


class BoxTests(unittest.TestCase):
    AREA = (100.0, 50.0, 800.0, 450.0)

    def test_normalized_round_trip(self):
        rect = pip.to_normalized((300.0, 140.0, 200.0, 90.0), self.AREA)
        self.assertEqual(rect, (0.25, 0.2, 0.25, 0.2))
        self.assertEqual(pip.from_normalized(rect, self.AREA), (300.0, 140.0, 200.0, 90.0))

    def test_converters_reject_bad_input(self):
        for args in (((1, 2), self.AREA), ((1, 2, 3, 4), (0, 0, 0, 0)), (None, self.AREA)):
            with self.subTest(args=args):
                self.assertIsNone(pip.to_normalized(*args))
        self.assertIsNone(pip.from_normalized('junk', self.AREA))

    def test_fit_keeps_the_box_inside_the_picture(self):
        self.assertEqual(pip.fit((900, 600, 300, 200), self.AREA), (600.0, 300.0, 300.0, 200.0))
        self.assertEqual(pip.fit((-50, -50, 200, 100), self.AREA), (100.0, 50.0, 200.0, 100.0))

    def test_fit_enforces_a_minimum_size(self):
        x, y, width, height = pip.fit((300, 200, 1, 1), self.AREA)
        self.assertGreaterEqual(width, self.AREA[2] * pip.MIN_BOX - 1e-6)
        self.assertGreaterEqual(height, self.AREA[3] * pip.MIN_BOX - 1e-6)

    def test_resize_keeps_the_crop_aspect_ratio(self):
        x, y, width, height = pip.resize_keeping_aspect((300, 150, 200, 100), 320, 2.0, self.AREA)
        self.assertEqual((x, y), (300.0, 150.0))
        self.assertAlmostEqual(width / height, 2.0, places=6)
        self.assertAlmostEqual(width, 320.0, places=6)

    def test_resize_stops_at_the_picture_edge(self):
        x, y, width, height = pip.resize_keeping_aspect((300, 150, 200, 100), 5000, 2.0, self.AREA)
        self.assertLessEqual(x + width, self.AREA[0] + self.AREA[2] + 1e-6)
        self.assertLessEqual(y + height, self.AREA[1] + self.AREA[3] + 1e-6)
        self.assertAlmostEqual(width / height, 2.0, places=6)

    def test_clamp_box_normalizes_saved_settings(self):
        self.assertEqual(pip.clamp_box([1.2, -0.1, 0.5, 0.4]), (0.5, 0.0, 0.5, 0.4))
        self.assertIsNone(pip.clamp_box(None))
        self.assertIsNone(pip.clamp_box([0, 0, 0, 0]))


class ViewerWiringTests(unittest.TestCase):
    """The canvas and settings row are Qt code; check the wiring at source level."""

    def setUp(self):
        self.source = VIEWER.read_text(encoding='utf-8')

    def test_geometry_module_is_free_of_qt(self):
        source = (ROOT / 'plugins/match_sync/pip.py').read_text(encoding='utf-8')
        self.assertNotIn('PySide6', source)
        self.assertNotIn('import Qt', source)

    def test_canvas_draws_and_clears_the_inset(self):
        for text in ('def set_pip', 'def clear_pip', 'def _paint_pip', 'self._paint_pip(painter)'):
            with self.subTest(text=text):
                self.assertIn(text, self.source)
        self.assertIn('pip_image', self.source)

    def test_canvas_moves_the_inset_with_the_mouse(self):
        for text in ('pip_geometry = Signal(float, float, float, float)', 'def _pip_box',
                     'def _pip_handle', 'def _drag_pip', 'def _hover_pip',
                     'self.pip_geometry.emit(*self.pip_rect)', 'SizeFDiagCursor', 'SizeAllCursor',
                     'self.canvas.pip_geometry.connect(self._pip_geometry_changed)'):
            with self.subTest(text=text):
                self.assertIn(text, self.source)

    def test_the_crop_reaches_the_canvas_as_a_plain_tuple(self):
        self.assertIn('self.canvas.pip_crop = crop', self.source)
        self.assertIn('tuple(row.pip_crop) if row is not None else (0.0, 0.0, 1.0, 1.0)', self.source)
        self.assertNotIn('self.canvas.pip_crop = QRectF(', self.source)

    def test_the_crop_comes_from_the_dialog_or_the_detector(self):
        for text in ('self.pip_crop_button.clicked.connect(self._pip_crop_clicked)',
                     'self.pip_auto_button.clicked.connect(self._auto_pip_crop_clicked)',
                     'row.pip_crop = rect', 'self._keep_pip_centre(previous, rect)',
                     'rect, _score = detect_auto_crop(frames, faces=True)',
                     'row.pip_crop = clamp_auto_crop(rect)'):
            with self.subTest(text=text):
                self.assertIn(text, self.source)
        for stale in ('snap_gray', '_snap_selection', 'detect_best', 'pip_follow', 'pip_follower',
                      '_auto_pip_first', 'auto_pip_done'):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, self.source)

    def test_the_automatic_crop_keeps_the_inset_centre_too(self):
        start = self.source.index('def _auto_pip_crop_for')
        end = self.source.index('def _pip_crop_clicked', start)
        body = self.source[start:end]
        for text in ('previous = tuple(row.pip_crop)', 'self._keep_pip_centre(previous, row.pip_crop)',
                     'self._sync_pip_geometry()'):
            with self.subTest(text=text):
                self.assertIn(text, body)
        self.assertNotIn('QApplication.setOverrideCursor', body)

    def test_the_crop_change_keeps_the_inset_centre(self):
        start = self.source.index('def _keep_pip_centre')
        end = self.source.index('def _read_pip', start)
        body = self.source[start:end]
        for text in ('old_aspect = float(previous[2]) / float(previous[3]) * ratio',
                     'new_aspect = float(current[2]) / float(current[3]) * ratio',
                     'shown = width * ratio / old_aspect',
                     'wanted = width * ratio / new_aspect',
                     'centre_x = x + width / 2.0',
                     'centre_y = y + shown / 2.0',
                     'self.pip_rect = (centre_x - width / 2.0, top, width, wanted)'):
            with self.subTest(text=text):
                self.assertIn(text, body)

    def test_the_inset_has_no_accent_border(self):
        start = self.source.index('def _paint_pip')
        end = self.source.index('def show_at', start)
        body = self.source[start:end]
        self.assertNotIn('theme.qcolor(theme.ACCENT)', body)
        self.assertNotIn('QPen(border', body)
        self.assertIn('if not (self.pip_drag or self.pip_hover):', body)

    def test_the_corner_menu_and_size_slider_are_gone(self):
        for text in ('pip_corner_box', 'pip_size', 'pip_width_label', 'addWidget(QLabel("位置"))'):
            with self.subTest(text=text):
                self.assertNotIn(text, self.source)
        self.assertIn('self.pip_hint = QLabel("小窗可拖动，拖右下角缩放")', self.source)
        self.assertIn('"rect": list(self.pip_rect) if self.pip_rect else None', self.source)

    def test_room_preferences_keep_the_pip_crop(self):
        self.assertIn('"pip_crop": list(self.pip_crop)', self.source)
        self.assertIn('preferences.get("pip_crop"', self.source)

    def test_viewer_settings_round_trip(self):
        for text in ('self.pip_check = QCheckBox("画中画")', 'self.pip_crop_button = QPushButton("框选画面")',
                     'def _render_pip', 'def _pip_crop_clicked', 'def pip_active_room',
                     'self.context.set_setting("pip"', 'self._update_pip_choices()'):
            with self.subTest(text=text):
                self.assertIn(text, self.source)

    def test_pip_source_stays_full_resolution(self):
        self.assertIn('pip_room = self.pip_active_room()', self.source)
        self.assertIn('shown.add(pip_room)', self.source)

    def test_clicks_outside_the_inset_still_reach_the_tile(self):
        """The canvas used to ignore every mouse event; dragging must not eat plain clicks."""
        for text in ('super().mousePressEvent(event)', 'super().mouseReleaseEvent(event)',
                     'super().mouseMoveEvent(event)'):
            with self.subTest(text=text):
                self.assertIn(text, self.source)


    def test_the_detector_answer_stays_on_the_status_line(self):
        for text in ('self.notice_hold = 0.0', 'def say(self, text, hold=0.0)',
                     'def _status_says(self, text)',
                     'self.notice_hold = time.monotonic() + hold',
                     'self.say("已自动框选摄像头小窗',
                     'if time.monotonic() >= self.notice_hold'):
            with self.subTest(text=text):
                self.assertIn(text, self.source)
        self.assertNotIn('self.notice.setText("已自动框选', self.source)
        self.assertIn('self._status_says(f"主画面：', self.source)

    def test_the_detector_reads_the_frame_the_inset_shows(self):
        for text in ("self.pip_stamp = ('', 0.0)",
                     'self.pip_stamp = (room_id, timestamp)',
                     'room_id, shown = self.pip_stamp',
                     'timestamp = shown - back'):
            with self.subTest(text=text):
                self.assertIn(text, self.source)


class AutoCropPathTests(unittest.TestCase):
    """Runs the real Viewer wiring on a bare instance, with Qt offscreen."""

    @classmethod
    def setUpClass(cls):
        try:
            import match_sync.viewer as module
        except Exception as error:  # the host or Qt is not installed here
            raise unittest.SkipTest(f"viewer unavailable: {error}")
        cls.module = module

    def _viewer(self, grab):
        module = self.module
        viewer = module.Viewer.__new__(module.Viewer)
        image = module.QImage(320, 180, module.QImage.Format_RGB32)
        image.fill(0x204060)
        viewer.canvas = types.SimpleNamespace(pip_image=image, image_rect=module.QRect(0, 0, 1600, 900))
        thumbs = [viewer._pip_thumb(image), viewer._pip_thumb(image)]
        viewer._pip_frames = lambda row: list(thumbs)
        viewer.pip_rect = (0.10, 0.10, 0.30, 0.30)
        viewer.pip_stamp = ('', 0.0)
        viewer.pip_active_room = lambda: ''
        viewer.live_clock = lambda: 0.0
        viewer.shifts = lambda: {}
        viewer._grab_picture = grab
        viewer.pip_enabled = True
        viewer.messages = []
        viewer.say = lambda text, hold=0.0: viewer.messages.append(text)
        viewer._sync_pip_geometry = lambda: None
        viewer.render = lambda: None
        viewer.changed = lambda: None
        return viewer

    def test_the_inset_frame_is_the_fallback_when_past_frames_are_not_ready(self):
        viewer = self._viewer(lambda row, back=0.0: None)
        frames = self.module.Viewer._pip_frames(viewer, object())
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].shape, (540, 960))
        self.assertEqual(frames[0].dtype.name, 'uint8')

    def test_the_auto_crop_uses_the_detected_box_and_says_so(self):
        viewer = self._viewer(lambda row, back=0.0: None)
        row = types.SimpleNamespace(pip_crop=(0.0, 0.0, 1.0, 1.0))
        original = self.module.detect_auto_crop
        self.module.detect_auto_crop = lambda frames, faces=True: ((0.60, 0.04, 0.30, 0.30), 1.0)
        try:
            result = self.module.Viewer._auto_pip_crop_for(viewer, row)
        finally:
            self.module.detect_auto_crop = original
        self.assertTrue(result)
        self.assertLess(row.pip_crop[2], 1.0)
        self.assertGreater(row.pip_crop[0], 0.5)
        self.assertIn('已自动框选摄像头小窗', viewer.messages[-1])

    def test_a_failed_detection_says_why_and_keeps_the_crop(self):
        viewer = self._viewer(lambda row, back=0.0: None)
        row = types.SimpleNamespace(pip_crop=(0.0, 0.0, 1.0, 1.0))
        original = self.module.detect_auto_crop
        self.module.detect_auto_crop = lambda frames, faces=True: (None, 0.0)
        try:
            result = self.module.Viewer._auto_pip_crop_for(viewer, row)
        finally:
            self.module.detect_auto_crop = original
        self.assertFalse(result)
        self.assertEqual(row.pip_crop, (0.0, 0.0, 1.0, 1.0))
        self.assertIn('没认出摄像头小窗', viewer.messages[-1])

    def test_a_detector_crash_is_reported_instead_of_vanishing(self):
        viewer = self._viewer(lambda row, back=0.0: None)
        row = types.SimpleNamespace(pip_crop=(0.0, 0.0, 1.0, 1.0))
        original = self.module.detect_auto_crop

        def boom(frames, faces=True):
            raise RuntimeError('cascade exploded')

        self.module.detect_auto_crop = boom
        try:
            result = self.module.Viewer._auto_pip_crop_for(viewer, row)
        finally:
            self.module.detect_auto_crop = original
        self.assertFalse(result)
        self.assertIn('cascade exploded', viewer.messages[-1])

    def test_an_empty_crop_source_retries_before_giving_up(self):
        viewer = self._viewer(lambda row, back=0.0: None)
        viewer.canvas.pip_image = self.module.QImage()
        viewer._pip_frames = lambda row: []
        row = types.SimpleNamespace(pip_crop=(0.0, 0.0, 1.0, 1.0))
        result = self.module.Viewer._auto_pip_crop_for(viewer, row, retries=1)
        self.assertFalse(result)
        self.assertIn('正在取这一路的画面', viewer.messages[-1])


    def test_the_auto_crop_leaves_a_line_in_the_host_log(self):
        lines = []
        viewer = types.SimpleNamespace(context=types.SimpleNamespace(log=lines.append))
        self.module.Viewer._auto_pip_log(viewer, "hello")
        self.assertEqual(lines, ["自动框选：hello"])

    def test_logging_survives_a_host_without_a_log(self):
        self.module.Viewer._auto_pip_log(types.SimpleNamespace(), "hello")
        self.module.Viewer._auto_pip_log(types.SimpleNamespace(context=object()), "hello")

    def test_the_click_reports_which_room_it_could_not_find(self):
        lines = []
        viewer = types.SimpleNamespace(
            context=types.SimpleNamespace(log=lines.append),
            pip_source=types.SimpleNamespace(currentData=lambda: None),
            pip_room='', rows={}, messages=[])
        viewer.say = lambda text, hold=0.0: viewer.messages.append(text)
        viewer._auto_pip_log = lambda text: self.module.Viewer._auto_pip_log(viewer, text)
        self.module.Viewer._auto_pip_crop_clicked(viewer)
        self.assertTrue(any('row=无' in line for line in lines), lines)
        self.assertIn('请先把要放进画中画的那一路', viewer.messages[-1])


class ViewerStatusTests(unittest.TestCase):
    """The frame status must never hide an answer, and must never crash render."""

    @classmethod
    def setUpClass(cls):
        try:
            import match_sync.viewer as module
        except Exception as error:
            raise unittest.SkipTest(f"viewer unavailable: {error}")
        cls.module = module

    def test_the_viewer_itself_owns_the_status_helpers(self):
        for name in ('say', '_status_says'):
            with self.subTest(name=name):
                self.assertTrue(hasattr(self.module.Viewer, name),
                                f'Viewer does not define {name}')
        self.assertIn('self._status_says(', inspect.getsource(self.module.Viewer))
        self.assertNotIn('self._status_says(', inspect.getsource(self.module.CompactStatus))

    def test_the_frame_status_waits_for_the_hold_to_expire(self):
        viewer = self.module.Viewer.__new__(self.module.Viewer)
        written = []
        viewer.notice = types.SimpleNamespace(setText=written.append)
        viewer.notice_hold = time.monotonic() + 5.0
        self.module.Viewer._status_says(viewer, 'frame status')
        self.assertEqual(written, [])
        self.module.Viewer.say(viewer, 'answer', hold=0.0)
        self.assertEqual(written[-1], 'answer')
        viewer.notice_hold = 0.0
        self.module.Viewer._status_says(viewer, 'frame status again')
        self.assertEqual(written[-1], 'frame status again')


class ConsoleQualityTests(unittest.TestCase):
    """Every row owns its quality switch, and the splitter must not re-flow mid-drag."""

    def setUp(self):
        self.viewer = VIEWER.read_text(encoding='utf-8')
        self.sending = (ROOT / 'plugins/match_sync/sending.py').read_text(encoding='utf-8')

    def test_every_row_has_its_own_quality_switch(self):
        for text in ('class QualityButton(QToolButton)', 'qualityChanged = Signal(int)',
                     'self.quality_button = QualityButton()',
                     'self.quality_button.qualityChanged.connect',
                     'self.volume_number, self.channel, self.quality_button,',
                     'def _row_quality_changed', 'row.quality_button.set_options',
                     'row.quality_button.setCurrentData(row.quality, notify=False)'):
            with self.subTest(text=text):
                self.assertIn(text, self.viewer)

    def test_the_quality_value_stays_a_number(self):
        """row.quality is the requested qn; the widget lives beside it, not on top of it."""
        self.assertIn('self.quality = int(preferences.get("quality"', self.viewer)
        self.assertIn('"quality": self.quality,', self.viewer)
        self.assertNotIn('self.quality = QualityButton()\n        self.quality.setCurrentData', self.viewer)

    def test_switching_a_secondary_row_restarts_only_that_row(self):
        start = self.viewer.index('def _row_quality_changed')
        body = self.viewer[start:start + 900]
        self.assertIn('self._stop_row(row)', body)
        self.assertIn('self._start_row(row)', body)
        self.assertIn('self.changed()', body)

    def test_the_splitter_does_not_reflow_while_dragging(self):
        for text in ('self._split_settle = QTimer(self)', 'def _split_settled',
                     'self._split_dragging = True', 'self._split_settle.start()'):
            with self.subTest(text=text):
                self.assertIn(text, self.viewer)
        self.assertIn('if self._split_dragging:\n                    '
                      '# Re-laying out the bottom bar mid-drag changes the left pane', self.viewer)

    def test_the_console_reflows_only_after_the_drag(self):
        for text in ("if getattr(self.viewer, '_split_dragging', False):", 'self._reflow_pending = True',
                     'def _apply_responsive_layout', 'width >= 540', 'width >= 500'):
            with self.subTest(text=text):
                self.assertIn(text, self.sending)


class SplitterReachTests(unittest.TestCase):
    """The chat splitter must follow the cursor both ways instead of hitting a floor."""

    def setUp(self):
        self.viewer = (ROOT / 'plugins/match_sync/viewer.py').read_text(encoding='utf-8')
        self.sending = (ROOT / 'plugins/match_sync/sending.py').read_text(encoding='utf-8')

    def test_neither_pane_keeps_a_wide_hard_floor(self):
        for text in ('self.left_pane.setMinimumWidth(200)', 'right_pane.setMinimumWidth(160)',
                     'width = max(160, min(self.panel_width, max(160, total - 200)))',
                     'self.panel.setMinimumWidth(180)',
                     'class PaneHost(QWidget):', 'return QSize(140, hint.height())',
                     'self.left_pane = PaneHost(self)', 'right_pane = PaneHost()'):
            with self.subTest(text=text):
                self.assertIn(text, self.viewer)

    def test_the_drag_is_ours_not_qt_constraint_math(self):
        for text in ('self._split_press = (event.globalPosition().x(), tuple(self.picture_split.sizes()))',
                     'self._drag_split(event.globalPosition().x())',
                     'def _drag_split(self, global_x):',
                     'right = max(160, min(total - 200, sizes[1] - delta))'):
            with self.subTest(text=text):
                self.assertIn(text, self.viewer)

    def test_the_handle_freezes_reflow_on_the_first_pixel(self):
        for text in ('self._split_handle = split.handle(1)',
                     'self._split_handle.installEventFilter(self)',
                     "if watched is getattr(self, '_split_handle', None):",
                     'if kind == QEvent.MouseButtonPress:',
                     'self._split_settle.start()',
                     'if QApplication.mouseButtons() & Qt.LeftButton:'):
            with self.subTest(text=text):
                self.assertIn(text, self.viewer)

    def test_double_click_restores_a_default_width(self):
        self.assertIn('def _reset_panel_width', self.viewer)
        start = self.viewer.index('def _reset_panel_width')
        self.assertIn('int(total * 0.42)', self.viewer[start:start + 400])
        self.assertIn('elif kind == QEvent.MouseButtonDblClick:', self.viewer)

    def test_the_console_can_be_narrower_than_its_composer(self):
        for text in ('def minimumSizeHint', 'return QSize(200, hint.height())',
                     'self.rooms.setMaximumWidth(176)',
                     'self.rooms.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)',
                     'bar.setVisible(width >= 430)'):
            with self.subTest(text=text):
                self.assertIn(text, self.sending)
        self.assertNotIn('self.rooms.setFixedWidth(176)', self.sending)


class MediaQualityTests(unittest.TestCase):
    """Bilibili rows must learn their tiers from the stream, not from a guess."""

    def setUp(self):
        self.media = (ROOT / 'plugins/match_sync/media.py').read_text(encoding='utf-8')

    def test_bilibili_rows_report_their_quality_tiers(self):
        start = self.media.index('def _bili_options')
        body = self.media[start:start + 760]
        self.assertIn('bili.room_quality_options(self.room_id)', body)
        self.assertIn('self._quality_options = cached', body)

    def test_tiers_are_reported_before_a_reused_url_returns(self):
        start = self.media.index('def _resolve')
        body = self.media[start:start + 1600]
        self.assertIn('if self.platform is None:', body)
        self.assertIn('"quality_options": options', body)
        self.assertLess(body.index('"quality_options": options'), body.index('reuse_seed'))

    def test_the_resolved_quality_report_carries_the_tiers(self):
        self.assertIn('"quality_options": self._bili_options()', self.media)
        self.assertIn('else self._bili_options())', self.media)


if __name__ == '__main__':
    unittest.main()
