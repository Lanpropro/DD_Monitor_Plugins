"""Offline Douyu interaction contracts and per-tile lifecycle checks."""
import argparse
import asyncio
import importlib
import os
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
                yield SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=api.packet(event))
        return messages()


class ChatTests(unittest.TestCase):
    def send(self, events):
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
                return socket
        return asyncio.run(api.send_chat({"domain": "wsproxy.douyu.com", "port": 6673},
            "9999", "42", "hello/@", {"acf_username": "fixture", "acf_auth_wl": "fake", "dy_did": "fixture"},
            lambda: False, client_factory=Client))

    def test_send_waits_for_matching_login_and_ack(self):
        self.assertEqual(self.send([{"type": "loginres", "userid": "42"},
                                    {"type": "chatres", "res": "0"}]), "弹幕已发送")
        self.assertEqual([e["type"] for e in self.socket.sent], ["loginreq", "chatmessage"])
        self.assertEqual(self.socket.sent[1]["content"], "hello/@")

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


class UITests(unittest.TestCase):
    def setUp(self):
        self.parent = QWidget()
        self.owner = SimpleNamespace(platform=SimpleNamespace(_playback_cookies=lambda: cookie_jar()),
            context=SimpleNamespace(notify=Mock()), sending=False, requests=set())
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
