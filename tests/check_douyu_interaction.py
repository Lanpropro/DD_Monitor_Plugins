"""Offline Douyu interaction contracts and per-tile lifecycle checks."""
import argparse
import asyncio
import importlib
import os
import ssl
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def send(self, events, cookies=None):
        socket = FakeSocket(events)
        self.socket = socket
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
            lambda: False, client_factory=Client))

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

    def test_other_room_or_text_ack_cannot_claim_delivery(self):
        for reply in ({"type": "chatres", "res": "0", "rid": "100"},
                      {"type": "chatres", "res": "0", "txt": "another message"}):
            with self.assertRaisesRegex(api.InteractionError, "未确认"):
                self.send([{"type": "loginres", "userid": "42"}, reply])


class UITests(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.owner = SimpleNamespace(platform=SimpleNamespace(_playback_cookies=lambda: cookie_jar()),
            context=SimpleNamespace(notify=Mock(), request_login=Mock()), sending=False, requests=set())
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
