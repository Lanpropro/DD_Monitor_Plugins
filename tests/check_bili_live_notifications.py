"""Synthetic host/report integration: room identity, login actions and stale guards."""
import importlib.util
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bili_live_support import manager
from PySide6.QtWidgets import QApplication, QWidget
from ddm.plugins import PluginContext, PluginManager

APP = QApplication.instance() or QApplication([])


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self._closing = False
        self.notices, self.logins = [], []

    def _show_notice(self, message, **options):
        self.notices.append((message, options))

    def open_login(self, platform, *, reauthenticate=False):
        self.logins.append((platform, reauthenticate))


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('ddm_plugin_bili_live.notifications'),
                             'missing shared notification reporter')
        from ddm_plugin_bili_live.notifications import NotificationReporter
        self.window = Window()
        self.manager = PluginManager(self.window)
        self.context = PluginContext(self.manager, 'bili_live')
        self.reporter = NotificationReporter(self.context)

    def tearDown(self):
        if hasattr(self, 'reporter'):
            self.reporter.close()
            self.manager.unload()
            self.window.close()
            self.window.deleteLater()
            APP.processEvents()

    def flush(self):
        for _ in range(3):
            APP.processEvents()

    def test_send_error_retains_captured_room_identity(self):
        room = {'room_id': '120', 'uname': 'synthetic anchor', 'title': 'test stream'}
        self.assertTrue(self.reporter.error('发送失败：网络超时', room=room))
        room.update(room_id='999', uname='changed')
        self.flush()
        message, options = self.window.notices[0]
        self.assertIn('120', message)
        self.assertIn('synthetic anchor', message)
        self.assertIn('网络超时', message)
        self.assertNotIn('999', message)
        self.assertEqual(options['kind'], 'warning')
        self.assertIsNone(options['on_click'])
        self.assertEqual(options['rooms'][0]['room_id'], '120')

    def test_expired_credentials_click_opens_deferred_reauthentication(self):
        self.reporter.error('发送失败：登录失效 (-101)', room={'room_id': '120'})
        self.flush()
        message, options = self.window.notices[0]
        self.assertIn('重新登录', message)
        self.assertIsNotNone(options['on_click'])
        options['on_click']()
        self.assertEqual(self.window.logins, [])
        self.flush()
        self.assertEqual(self.window.logins, [('bilibili', True)])

    def test_server_authentication_attribute_survives_generic_message(self):
        error = ValueError('server rejected request')
        error.login_required = True
        self.reporter.error(error, room={'room_id': '120'})
        self.flush()
        self.assertIsNotNone(self.window.notices[0][1]['on_click'])

    def test_missing_credentials_can_be_marked_explicitly(self):
        self.reporter.error('需要账号', room='120', login_required=True)
        self.flush()
        self.assertIsNotNone(self.window.notices[0][1]['on_click'])

    def test_permission_denials_and_network_failures_do_not_request_login(self):
        for error in ('余额不足', '网络超时', '等级不足，无法使用此表情', '未购买表情，请登录网页购买'):
            self.reporter.error(error, room={'room_id': '120'})
            self.flush()
            self.assertIsNone(self.window.notices[-1][1]['on_click'])
        self.assertEqual(self.window.logins, [])

    def test_explicit_non_login_error_overrides_text_classification(self):
        self.reporter.error('CSRF 服务诊断失败', login_required=False)
        self.flush()
        self.assertIsNone(self.window.notices[0][1]['on_click'])

    def test_late_results_and_retained_clicks_after_close_are_inert(self):
        self.reporter.error('登录失效', room={'room_id': '120'})
        self.flush()
        callback = self.window.notices[0][1]['on_click']
        self.reporter.close()
        callback()
        self.assertFalse(self.reporter.error('late failure'))
        self.flush()
        self.assertEqual(len(self.window.notices), 1)
        self.assertEqual(self.window.logins, [])

    def test_context_predicate_blocks_old_control_results(self):
        from ddm_plugin_bili_live.notifications import NotificationReporter
        live = [True]
        reporter = NotificationReporter(self.context, active=lambda: live[0])
        reporter.error('登录失效')
        self.flush()
        live[0] = False
        self.window.notices[0][1]['on_click']()
        self.assertFalse(reporter.error('stale'))
        self.flush()
        self.assertEqual(self.window.logins, [])

    def test_older_host_falls_back_without_private_window_access(self):
        from ddm_plugin_bili_live.notifications import NotificationReporter
        class OlderContext:
            def __init__(self):
                self.logs = []
            def log(self, message):
                self.logs.append(message)
        context = OlderContext()
        reporter = NotificationReporter(context)
        self.assertFalse(reporter.error('network failure', room={'room_id': '120'}))
        self.assertEqual(len(context.logs), 1)
        self.assertIn('120', context.logs[0])

    def test_notification_only_host_does_not_advertise_an_unavailable_login(self):
        from ddm_plugin_bili_live.notifications import NotificationReporter
        class NotifyOnlyContext:
            def __init__(self):
                self.notices = []
            def notify(self, message, **options):
                self.notices.append((message, options))
                return True
        context = NotifyOnlyContext()
        self.assertTrue(NotificationReporter(context).error('登录失效'))
        message, options = context.notices[0]
        self.assertNotIn('点击', message)
        self.assertIsNone(options['on_click'])

    def test_headless_host_returns_false(self):
        from ddm_plugin_bili_live.notifications import NotificationReporter
        self.assertFalse(NotificationReporter(PluginContext(manager, 'bili_live')).error(
            'network failure', room={'room_id': '120'}))


if __name__ == '__main__':
    unittest.main()
