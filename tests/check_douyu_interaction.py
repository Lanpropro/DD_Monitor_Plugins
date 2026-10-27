"""Offline Douyu interaction contracts and per-tile lifecycle checks."""
import argparse
import asyncio
import importlib
import os
import ssl
from pathlib import Path
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--host", type=Path, required=True)
parser.add_argument("--plugins-dir", type=Path, help="Check the host's installed development plugins")
args = parser.parse_args()
sys.path.insert(0, str(args.host.resolve()))
os.environ["DDM_NO_SAVE"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import aiohttp
import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from ddm.plugins import PluginManager

APP = QApplication.instance() or QApplication([])
MANAGER = PluginManager(plugins_dir=str(args.plugins_dir or ROOT / "plugins"), enabled=["domestic_live"])
MANAGER.load()
assert "douyu" in MANAGER.platforms, MANAGER.summary
api = importlib.import_module("ddm_plugin_domestic_live.douyu_interaction")
ui = importlib.import_module("ddm_plugin_domestic_live.douyu_composer")


def catalog(*, price=10, gift_type=0, **changes):
    item = {"id": 20006, "name": "赞", "basicInfo": {"giftType": gift_type, "bizTag": ""},
            "priceInfo": {"price": price, "priceType": "YUCHI"}, "donateStatus": 1,
            "defaultSkinId": 12, "countLimit": 10}
    item.update(changes)
    return {"error": 0, "data": {"giftList": [item], "onceLimit": 500000}}


def response(data):
    result = Mock(is_redirect=False)
    result.json.return_value = data
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def cookie_jar():
    jar = requests.cookies.RequestsCookieJar()
    for name, value in {"acf_ccn": "csrf", "acf_username": "synthetic",
                        "acf_auth_wl": "not-real", "dy_did": "fixture-device"}.items():
        jar.set(name, value, domain=".douyu.com", path="/")
    return jar


class APITests(unittest.TestCase):
    def client(self, *, changed=False):
        session = Mock(cookies=cookie_jar(), headers={})
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.side_effect = [response({"error": 0, "msg": {"uid": "42"}}),
                                   response(catalog(price=11 if changed else 10)),
                                   response({"error": 0})]
        session.post.return_value = response({"error": 0, "data": {"num": 2}})
        return api.DouyuAPI(cookie_jar(), session_factory=lambda: session), session

    def test_room_validation(self):
        self.assertEqual(api.room_number("douyu:9999"), "9999")
        for room in ("huya:9999", "0", "9999/other", "１２３", "https://www.douyu.com/9999"):
            with self.assertRaises(api.InteractionError):
                api.room_number(room)

    def test_web_cookie_scopes_include_www_but_exclude_message_and_passport(self):
        jar = cookie_jar()
        for domain, value in ((".douyu.com", "root"), ("www.douyu.com", "web"),
                              ("msg.douyu.com", "message"), ("passport.douyu.com", "passport")):
            jar.set("acf_dmjwt_token", value, domain=domain, path="/")
        jar.set("private", "wrong-path", domain="www.douyu.com", path="/other")
        jar.set("expired", "old", domain="www.douyu.com", path="/", expires=1)
        cookies = api.web_cookies(jar, "douyu:473252")
        self.assertEqual(cookies["acf_dmjwt_token"], "web")
        self.assertNotIn("private", cookies)
        self.assertNotIn("expired", cookies)
        self.assertEqual(cookies["dy_did"], "fixture-device")

    def test_gift_price_skin_limits_and_special_flow(self):
        gift = api.gift_catalog(catalog())[0]
        self.assertEqual((gift.id, gift.skin_id, gift.maximum), (20006, 12, 10))
        self.assertEqual(gift.cost(6), "0.6 鱼翅")
        self.assertTrue(api.gift_catalog(catalog(gift_type=6))[0].disabled)
        self.assertTrue(api.gift_catalog(catalog(donateStatus=0))[0].disabled)

    def test_json_string_data_and_malformed_catalog(self):
        import json
        payload = catalog()
        payload["data"] = json.dumps(payload["data"])
        self.assertEqual(len(api.gift_catalog(payload)), 1)
        with self.assertRaises(api.InteractionError):
            api.gift_catalog({"error": 0, "data": []})

    def test_default_skin_name_and_image_follow_official_catalog(self):
        payload = catalog()
        payload["data"]["skinData"] = {"12": {"skinName": "皮肤礼物",
            "picUrlPrefix": "https://gfs-op.douyucdn.cn/dygift",
            "giftPic": [{"type": "image/png", "src": "/gift.png"}]}}
        gift = api.gift_catalog(payload)[0]
        self.assertEqual(gift.name, "皮肤礼物")
        self.assertEqual(gift.image, "https://gfs-op.douyucdn.cn/dygift/gift.png")
        payload["data"]["skinData"]["12"]["isFace"] = 1
        self.assertTrue(api.gift_catalog(payload)[0].disabled)

    def test_foreign_thumbnail_not_loaded(self):
        payload = catalog()
        payload["data"]["giftList"][0]["basicInfo"]["giftPic"] = [
            {"type": "image/png", "src": "https://foreign.invalid/gift.png"}]
        self.assertEqual(api.gift_catalog(payload)[0].image, "")

    def test_categories_keep_official_order_memberships_and_empty_tabs(self):
        payload = catalog(tabIds=[1, 4])
        payload["data"]["tabs"] = [{"id": 1, "name": "礼物"}, {"id": 2, "name": "特权"},
                                    {"id": 4, "name": "钻粉"}, {"id": 5, "name": "粉丝团"}]
        gifts = api.gift_catalog(payload)
        tabs = api.gift_tabs(payload, gifts)
        self.assertEqual([tab.name for tab in tabs], ["礼物", "特权", "钻粉", "粉丝团"])
        self.assertEqual([len(tab.items) for tab in tabs], [1, 0, 1, 0])
        self.assertIs(tabs[0].items[0], tabs[2].items[0])

    def test_unknown_category_membership_stays_visible_in_first_tab(self):
        payload = catalog(tabIds=[999])
        payload["data"]["tabs"] = [{"id": 1, "name": "礼物"}]
        gifts = api.gift_catalog(payload)
        self.assertEqual(api.gift_tabs(payload, gifts)[0].items, gifts)

    def test_wallet_supports_header_gold_object_and_exact_decimal_display(self):
        wallet = api.wallet_data({"error": 0, "msg": {"gold": {"gold": "1234", "ngb": 900}, "silver": "214"}})
        self.assertEqual(wallet, api.Wallet(1234, 214))
        self.assertEqual(api.format_yuchi(wallet.yuchi), "12.34")
        self.assertEqual(api.wallet_data({"error": 0, "msg": {"gold": "0", "silver": 0}}), api.Wallet(0, 0))

    def test_missing_wallet_is_not_reported_as_zero(self):
        for msg in ({"silver": 5}, {"gold": 0}, {"gold": -1, "silver": 0}, {"gold": True, "silver": 0}):
            with self.assertRaises(api.InteractionError):
                api.wallet_data({"error": 0, "msg": msg})

    def test_panel_reads_balance_over_http_without_new_gateway_login(self):
        client, session = self.client()
        session.get.side_effect = [response(catalog()), response({"error": 0, "msg": {"gold": 0, "silver": 214}})]
        panel = client.gift_panel("douyu:9999")
        self.assertEqual(panel.wallet, api.Wallet(0, 214))
        self.assertEqual(len(panel.gifts), 1)
        self.assertEqual(session.get.call_args.args[0], api.BASE + "/lapi/member/api/getInfo")
        session.post.assert_not_called()

    def test_balance_failure_does_not_discard_public_gifts(self):
        client, session = self.client()
        session.get.side_effect = [response(catalog()), requests.Timeout("private detail")]
        panel = client.gift_panel("9999")
        self.assertEqual(len(panel.gifts), 1)
        self.assertIsNone(panel.wallet)
        self.assertIn("余额读取失败", panel.wallet_error)
        self.assertNotIn("private", panel.wallet_error)

    def test_panel_cancellation_after_catalog_prevents_account_read(self):
        client, session = self.client()
        session.get.side_effect = [response(catalog())]
        cancelled = iter([False, True])
        with self.assertRaises(api.InteractionError):
            client.gift_panel("9999", lambda: next(cancelled))
        self.assertEqual(session.get.call_count, 1)

    def test_cancel_after_account_reads_never_spends(self):
        client, session = self.client()
        calls = [False, True]
        with self.assertRaises(api.InteractionError):
            client.send_gift("9999", api.gift_catalog(catalog())[0], 2, lambda: calls.pop(0))
        session.post.assert_not_called()

    def test_gift_send_exact_web_contract_no_retry(self):
        client, session = self.client()
        client.send_gift("douyu:9999", api.gift_catalog(catalog())[0], 2)
        session.post.assert_called_once()
        call = session.post.call_args
        self.assertEqual(call.args, (api.DONATE,))
        self.assertEqual(call.kwargs["data"], {"giftId": 20006, "giftCount": 2,
            "roomId": "9999", "skinId": 12, "bizExt": "{}", "ctn": "csrf"})
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_gift_csrf_can_be_a_www_host_cookie(self):
        client, session = self.client()
        session.cookies.clear(".douyu.com", "/", "acf_ccn")
        session.cookies.set("acf_ccn", "web-csrf", domain="www.douyu.com", path="/")
        client.send_gift("9999", api.gift_catalog(catalog())[0], 2)
        self.assertEqual(session.post.call_args.kwargs["data"]["ctn"], "web-csrf")

    def test_text_http_stages_share_one_budget(self):
        client, session = self.client()
        session.post.return_value = response({"error": 0, "data": {"wss": [
            {"domain": "wsproxy.douyu.com", "port": 6671}]}})
        phases = []
        with patch.object(api, "send_chat", new=AsyncMock(return_value="ack")) as send:
            self.assertEqual(client.send_text("9999", "hello", progress=phases.append), "ack")
            self.assertLessEqual(send.call_args.kwargs["timeout"], api.CHAT_SEND_TIMEOUT)
            self.assertGreater(send.call_args.kwargs["timeout"], 0)
        self.assertEqual(phases, ["account", "gateway"])
        self.assertEqual(session.get.call_args.kwargs["timeout"], (2, 3))
        self.assertEqual(session.post.call_args.kwargs["timeout"], (2, 3))

    def test_account_timeout_never_connects_gateway(self):
        client, session = self.client()
        session.get.side_effect = requests.Timeout("sensitive request details")
        with self.assertRaisesRegex(api.InteractionError, "账号确认超时，本次未发送") as caught:
            client.send_text("9999", "hello")
        self.assertNotIn("sensitive", str(caught.exception))
        session.post.assert_not_called()

    def test_exhausted_preparation_budget_never_starts_websocket(self):
        client, session = self.client()
        with patch.object(api.time, "monotonic", side_effect=[0, 0, 13]), \
                patch.object(api, "send_chat", new=AsyncMock()) as send:
            with self.assertRaisesRegex(api.InteractionError, "准备超时，本次未发送"):
                client.send_text("9999", "hello")
        session.post.assert_not_called()
        send.assert_not_called()

    def test_changed_price_never_spends(self):
        client, session = self.client(changed=True)
        with self.assertRaisesRegex(api.InteractionError, "已变化"):
            client.send_gift("9999", api.gift_catalog(catalog())[0], 2)
        session.post.assert_not_called()

    def test_logged_out_never_spends(self):
        client, session = self.client()
        session.get.side_effect = [response({"error": 901})]
        with self.assertRaisesRegex(api.InteractionError, "未登录"):
            client.send_gift("9999", api.gift_catalog(catalog())[0], 2)
        session.post.assert_not_called()

    def test_cancel_never_spends(self):
        client, session = self.client()
        with self.assertRaises(api.InteractionError):
            client.send_gift("9999", api.gift_catalog(catalog())[0], 2, lambda: True)
        session.post.assert_not_called()

    def test_timeout_gift_result_unknown_and_never_retried(self):
        client, session = self.client()
        session.post.side_effect = requests.Timeout("secret URL must not escape")
        with self.assertRaisesRegex(api.InteractionError, "结果未确认") as caught:
            client.send_gift("9999", api.gift_catalog(catalog())[0], 2)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(session.post.call_count, 1)

    def test_gift_redirect_not_followed(self):
        client, session = self.client()
        session.post.return_value.is_redirect = True
        with self.assertRaisesRegex(api.InteractionError, "结果未确认"):
            client.send_gift("9999", api.gift_catalog(catalog())[0], 2)
        self.assertEqual(session.post.call_count, 1)

    def test_invalid_quantity_and_special_gift_never_spend(self):
        for gift, count in ((api.gift_catalog(catalog())[0], 0),
                            (api.gift_catalog(catalog())[0], True),
                            (api.gift_catalog(catalog())[0], 11),
                            (api.gift_catalog(catalog(gift_type=6))[0], 1)):
            client, session = self.client()
            with self.assertRaises(api.InteractionError):
                client.send_gift("9999", gift, count)
            session.post.assert_not_called()

    def test_packet_escaping_round_trip(self):
        values = {"type": "chatmessage", "content": "a/@=b你好"}
        self.assertEqual(api.douyu_messages(bytearray(api.packet(values))), [values])


class FakeSocket:
    def __init__(self, events):
        self.events, self.sent = events, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send_bytes(self, data):
        self.sent.extend(api.douyu_messages(bytearray(data)))

    def __aiter__(self):
        async def messages():
            for event in self.events:
                if event is None:
                    await asyncio.Future()
                yield SimpleNamespace(type=aiohttp.WSMsgType.BINARY,
                                      data=event if isinstance(event, bytes) else api.packet(event))
        return messages()


class ChatTests(unittest.TestCase):
    def test_gateway_tls_keeps_verification_and_enables_rsa_gcm(self):
        context = api.chat_ssl_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        suites = {cipher["name"] for cipher in context.get_ciphers()}
        self.assertTrue({"AES128-GCM-SHA256", "AES256-GCM-SHA384"} <= suites)

    def send(self, events, cookies=None, *, timeout=api.CHAT_SEND_TIMEOUT):
        socket = FakeSocket(events)
        self.socket = socket
        self.phases = []
        class Client:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            def ws_connect(self, *args, **kwargs):
                assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
                assert kwargs["ssl"].check_hostname
                assert kwargs["timeout"].ws_close <= 0.25
                return socket
        return asyncio.run(api.send_chat({"domain": "wsproxy.douyu.com", "port": 6673},
            "9999", "42", "hello/@", cookies if cookies is not None else
            {"acf_username": "fixture", "acf_auth_wl": "fake", "dy_did": "fixture"},
            lambda: False, client_factory=Client, timeout=timeout, progress=self.phases.append))

    def test_send_waits_for_matching_login_and_ack(self):
        self.assertEqual(self.send([{"type": "loginres", "userid": "42"},
                                    {"type": "chatres", "res": "0"}]), "斗鱼已返回发送回执")
        self.assertEqual([e["type"] for e in self.socket.sent], ["loginreq", "chatmessage"])
        self.assertEqual(self.socket.sent[1]["content"], "hello/@")

    def test_modern_jwt_login_does_not_require_legacy_password(self):
        self.send([{"type": "loginres", "userid": "42"}, {"type": "chatres", "res": "0"}],
                  {"acf_username": "fixture", "acf_dmjwt_token": "fixture-jwt", "dy_did": "fixture"})
        self.assertEqual(self.socket.sent[0]["jwt"], "fixture-jwt")
        self.assertEqual(self.socket.sent[0]["password"], "")

    def test_missing_gateway_token_is_not_misreported_as_expired_login(self):
        with self.assertRaises(api.InteractionError) as caught:
            self.send([], {"dy_did": "fixture", "acf_username": "fixture"})
        self.assertNotIsInstance(caught.exception, api.LoginRequired)
        self.assertNotIn("重新登录", str(caught.exception))

    def test_wrong_login_never_sends_text(self):
        with self.assertRaises(api.InteractionError):
            self.send([{"type": "loginres", "userid": "0"}])
        self.assertEqual(len(self.socket.sent), 1)

    def test_new_gateway_challenge_stops_without_text(self):
        with self.assertRaisesRegex(api.InteractionError, "新版校验"):
            self.send([{"type": "livres"}])
        self.assertEqual(len(self.socket.sent), 1)

    def test_chat_echo_does_not_claim_success(self):
        with self.assertRaisesRegex(api.InteractionError, "未确认"):
            self.send([{"type": "loginres", "userid": "42"}, {"type": "chatmsg", "txt": "hello/@"}])

    def test_duplicate_login_does_not_send_twice(self):
        self.send([{"type": "loginres", "userid": "42"}, {"type": "loginres", "userid": "42"},
                   {"type": "chatres", "res": "0"}])
        self.assertEqual(len(self.socket.sent), 2)

    def test_server_refusal_keeps_failure(self):
        with self.assertRaisesRegex(api.InteractionError, "拒绝弹幕"):
            self.send([{"type": "loginres", "userid": "42"}, {"type": "chatres", "res": "308"}])

    def test_login_batch_notice_cannot_acknowledge_later_write(self):
        batch = api.packet({"type": "loginres", "userid": "42"}) + api.packet({"type": "chatres", "res": "0"})
        with self.assertRaisesRegex(api.InteractionError, "未确认"):
            self.send([batch])

    def test_other_room_ack_cannot_claim_delivery(self):
        with self.assertRaisesRegex(api.InteractionError, "未确认"):
            self.send([{"type": "loginres", "userid": "42"},
                       {"type": "chatres", "res": "0", "rid": "100"}])

    def test_ack_text_is_not_required_to_equal_original(self):
        result = self.send([{"type": "loginres", "userid": "42"},
                            {"type": "chatres", "res": "0", "txt": "normalized reply metadata"}])
        self.assertEqual(result, "斗鱼已返回发送回执")

    def test_error_reply_text_does_not_get_ignored(self):
        with self.assertRaisesRegex(api.InteractionError, "代码 308"):
            self.send([{"type": "loginres", "userid": "42"},
                       {"type": "chatres", "res": "308", "txt": "server explanation"}])

    def test_missing_ack_has_bounded_wait_and_no_retry(self):
        started = time.monotonic()
        with self.assertRaisesRegex(api.InteractionError, "结果未确认"):
            self.send([{"type": "loginres", "userid": "42"}, None], timeout=.03)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(len(self.socket.sent), 2)
        self.assertIn("wait_ack", self.phases)


class UITests(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.owner = SimpleNamespace(platform=SimpleNamespace(_playback_cookies=lambda: cookie_jar()),
            context=SimpleNamespace(notify=Mock(), request_login=Mock()), sending=False, requests=set())
        def retire(request):
            request.wait()
            self.owner.requests.discard(request)
            request.deleteLater()
        self.owner.retire = retire
        self.widget = ui.DouyuComposer(self.owner, self.parent)
        self.widget.set_room({"room_id": "douyu:9999", "platform": "douyu", "uname": "Synthetic"})

    def tearDown(self):
        self.widget.shutdown()
        self.parent.close()
        self.parent.deleteLater()
        APP.processEvents()

    def test_unsupported_rooms_hidden(self):
        self.widget.set_room({"room_id": "huya:9999", "platform": "huya"})
        self.assertTrue(self.widget.isHidden())

    def test_switch_room_cancels_request_and_clears_draft(self):
        request = SimpleNamespace(cancelled=threading.Event())
        self.widget.pending = request
        self.widget.edit.setText("old")
        self.widget.set_room({"room_id": "douyu:100", "platform": "douyu"})
        self.assertTrue(request.cancelled.is_set())
        self.assertEqual(self.widget.edit.text(), "")

    def test_stale_result_cannot_clear_new_room_draft(self):
        key, room = self.widget.snapshot(), dict(self.widget.room)
        self.widget.set_room({"room_id": "douyu:100", "platform": "douyu"})
        self.widget.edit.setText("new")
        self.widget.completed(object(), (key, "text", "old", room), "sent")
        self.assertEqual(self.widget.edit.text(), "new")

    def test_account_change_cannot_clear_draft(self):
        key, room = self.widget.snapshot(), dict(self.widget.room)
        self.widget.edit.setText("keep")
        self.owner.platform._playback_cookies = lambda: requests.cookies.RequestsCookieJar()
        self.widget.completed(object(), (key, "text", "keep", room), "sent")
        self.assertEqual(self.widget.edit.text(), "keep")

    def test_failed_send_preserves_draft(self):
        self.widget.edit.setText("keep")
        self.widget.completed(object(), (self.widget.snapshot(), "text", "keep", dict(self.widget.room)),
                              api.InteractionError("not sent"))
        self.assertEqual(self.widget.edit.text(), "keep")

    def test_success_clears_draft_without_notification(self):
        self.widget.edit.setText("sent")
        self.widget.completed(object(), (self.widget.snapshot(), "text", "sent", dict(self.widget.room)),
                              "斗鱼已返回发送回执")
        self.assertEqual(self.widget.edit.text(), "")
        self.owner.context.notify.assert_not_called()

    def test_gift_success_does_not_raise_notification(self):
        self.widget.completed(object(), (self.widget.snapshot(), "gift", "", dict(self.widget.room)), {})
        self.owner.context.notify.assert_not_called()

    def test_enter_and_keypad_enter_reach_send(self):
        self.parent.show()
        self.widget.show()
        self.widget.edit.setText("native Enter")
        self.widget.edit.setFocus()
        APP.processEvents()
        with patch.object(self.widget, "start") as start:
            QTest.keyClick(self.widget.edit, Qt.Key_Return)
            self.assertEqual(start.call_count, 1)
            QTest.keyClick(self.widget.edit, Qt.Key_Enter)
            self.assertEqual(start.call_count, 2)

    def test_committed_ime_enter_sends_after_candidate_confirmation(self):
        self.widget.edit.inputMethodEvent(QInputMethodEvent("拼", []))
        commit = QInputMethodEvent()
        commit.setCommitString("拼")
        self.widget.edit.inputMethodEvent(commit)
        with patch.object(self.widget, "start") as start:
            QTest.keyClick(self.widget.edit, Qt.Key_Return)
            start.assert_called_once()

    def test_pending_send_keeps_focus_and_releases_for_next_enter(self):
        self.parent.show()
        self.widget.show()
        self.parent.activateWindow()
        self.widget.edit.setText("first")
        self.widget.edit.setFocus()
        APP.processEvents()
        with patch.object(ui.Request, "start"):
            self.widget.send_text()
        request = self.widget.pending
        self.assertTrue(self.widget.isEnabled())
        self.assertTrue(self.widget.edit.isReadOnly())
        self.assertTrue(self.widget.edit.hasFocus())
        self.widget.completed(request, request.key, "斗鱼已返回发送回执")
        self.assertFalse(self.widget.edit.isReadOnly())
        self.assertTrue(self.widget.edit.hasFocus())
        self.widget.edit.setText("second")
        with patch.object(self.widget, "start") as start:
            QTest.keyClick(self.widget.edit, Qt.Key_Return)
            start.assert_called_once()
        self.owner.requests.discard(request)
        request.deleteLater()

    def test_worker_failure_restores_button_and_preserves_text(self):
        self.widget.edit.setText("keep after timeout")
        def fail(room, text, cancelled, *, progress):
            progress("wait_ack")
            raise api.InteractionError("弹幕发送结果未确认，请在直播间核对后再试")
        with patch.object(ui.DouyuAPI, "send_text", side_effect=fail):
            self.widget.send_text()
            deadline = time.monotonic() + 1
            while self.widget.pending is not None and time.monotonic() < deadline:
                QTest.qWait(5)
            QTest.qWait(10)
        self.assertIsNone(self.widget.pending)
        self.assertEqual(self.widget.send.text(), "发送")
        self.assertTrue(self.widget.send.isEnabled())
        self.assertFalse(self.widget.edit.isReadOnly())
        self.assertFalse(self.owner.sending)
        self.assertEqual(self.widget.edit.text(), "keep after timeout")

    def test_progress_log_excludes_message_and_credentials(self):
        self.owner.context.log = Mock()
        self.widget.edit.setText("private draft")
        with patch.object(ui.Request, "start"):
            self.widget.send_text()
        request = self.widget.pending
        self.widget.progress(request, "wait_ack")
        logged = self.owner.context.log.call_args.args[0]
        self.assertIn("阶段=wait_ack", logged)
        self.assertNotIn("private draft", logged)
        self.assertNotIn("not-real", logged)
        self.widget.completed(request, request.key, "ack")
        self.owner.requests.discard(request)
        request.deleteLater()

    def test_picker_and_buttons_use_bili_visual_primitives(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        self.assertIsInstance(popup, ui.AnchoredPopup)
        self.assertEqual(popup.size().width(), 400)
        self.assertEqual(self.widget.gifts.size().width(), 24)
        self.assertEqual(self.widget.send.objectName(), "TileCtrl")
        self.assertEqual(popup.styleSheet(), ui.picker_style())

    def test_expired_login_action_opens_douyu_reauthentication(self):
        self.widget.completed(object(), (self.widget.snapshot(), "text", "keep", dict(self.widget.room)),
                              api.LoginRequired("斗鱼未登录，请重新登录"))
        callback = self.owner.context.notify.call_args.kwargs["on_click"]
        self.assertTrue(callable(callback))
        self.owner.context.request_login.assert_not_called()
        callback()
        self.owner.context.request_login.assert_called_once_with("douyu", reauthenticate=True)

    def test_gateway_error_does_not_request_reauthentication(self):
        self.widget.completed(object(), (self.widget.snapshot(), "text", "keep", dict(self.widget.room)),
                              api.InteractionError("互动网关令牌尚未取得"))
        self.assertIsNone(self.owner.context.notify.call_args.kwargs["on_click"])
        self.owner.context.request_login.assert_not_called()

    def test_gift_picker_does_not_send_on_selection(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.gift_catalog(catalog())[0]
        popup.set_gifts((gift,))
        with patch.object(self.widget, "send_gift") as send:
            popup.choose(gift)
            popup.quantity.setValue(2)
            self.assertIn("0.2 鱼翅", popup.summary.text())
            send.assert_not_called()

    def test_panel_categories_switch_without_leaking_selection(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.gift_catalog(catalog())[0]
        panel = api.GiftPanel((gift,), (api.GiftTab(1, "礼物", (gift,)), api.GiftTab(2, "特权", ())), api.Wallet(200, 214))
        popup.set_panel(panel)
        self.assertEqual([popup.tabs.tabText(i) for i in range(popup.tabs.count())], ["礼物", "特权"])
        self.assertIn("鱼丸：214", popup.wallet_label.text())
        self.assertIn("鱼翅：2", popup.wallet_label.text())
        popup.choose(gift)
        popup.tabs.setCurrentIndex(1)
        self.assertEqual(popup.grid.count(), 0)
        self.assertIsNone(popup.selected)
        self.assertFalse(popup.send.isEnabled())
        popup.set_panel(panel)
        self.assertEqual(popup.tabs.tabData(popup.tabs.currentIndex()), 2)

    def test_zero_balance_disables_confirm_and_keeps_card_inspectable(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.gift_catalog(catalog())[0]
        popup.set_panel(api.GiftPanel((gift,), (api.GiftTab(1, "礼物", (gift,)),), api.Wallet(0, 214)))
        popup.choose(gift)
        self.assertFalse(popup.send.isEnabled())
        self.assertEqual(popup.send.toolTip(), "余额不足")
        with patch.object(self.widget, "send_gift") as send:
            popup.confirm()
            send.assert_not_called()
        self.assertTrue(popup.grid.itemAt(0).widget().isEnabled())

    def test_balance_failure_shows_unknown_and_room_change_clears_balance(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        popup.set_panel(api.GiftPanel((), (), None, "余额读取失败"))
        self.assertEqual(popup.wallet_label.text(), "余额：未知")
        popup.set_panel(api.GiftPanel((), (), api.Wallet(100, 214)))
        self.widget.set_room({"room_id": "douyu:100", "platform": "douyu"})
        self.assertIsNone(popup.wallet)
        self.assertEqual(popup.tabs.count(), 0)
        self.assertEqual(popup.wallet_label.text(), "余额：未知")

    def test_gift_confirm_button_has_bili_control_height_and_bounded_width(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        self.assertEqual(popup.send.height(), ui.theme.TILE_CONTROL_HEIGHT)
        self.assertEqual(popup.send.width(), 96)
        self.assertEqual(popup.send.objectName(), "TileCtrl")
        self.assertEqual(popup.quantity.height(), popup.send.height())

    def test_gift_success_refreshes_visible_balance_without_notification(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        with patch.object(popup, "isVisible", return_value=True), patch.object(self.widget, "load_gifts") as refresh:
            self.widget.completed(object(), (self.widget.snapshot(), "gift", "", dict(self.widget.room)), {})
            refresh.assert_called_once()
        self.owner.context.notify.assert_not_called()

    def test_cross_tile_send_does_not_start_second_request(self):
        self.owner.sending = True
        with patch.object(ui.Request, "start") as start:
            self.widget.start("text", Mock())
            start.assert_not_called()
        self.assertIn("已有斗鱼发送请求", self.widget.edit.toolTip())

    def test_pending_send_cancelled_when_account_changes(self):
        request = SimpleNamespace(key=(self.widget.snapshot(), "gift"), cancelled=threading.Event())
        self.widget.pending = request
        self.owner.platform._playback_cookies = lambda: requests.cookies.RequestsCookieJar()
        self.widget.check_identity()
        self.assertTrue(request.cancelled.is_set())

    def test_input_method_enter_does_not_send(self):
        self.widget.edit.inputMethodEvent(QInputMethodEvent("拼", []))
        with patch.object(self.widget.edit, "returnPressed") as signal:
            self.widget.edit.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
            signal.emit.assert_not_called()


if __name__ == "__main__":
    try:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    finally:
        MANAGER.unload()
