"""Offline Douyu interaction contracts and per-tile lifecycle checks."""
import argparse
import asyncio
import importlib
import os
import ssl
import struct
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
from PySide6.QtGui import QHideEvent, QInputMethodEvent, QKeyEvent, QPalette
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from ddm.plugins import PluginManager

APP = QApplication.instance() or QApplication([])
MANAGER = PluginManager(plugins_dir=str(args.plugins_dir or ROOT / "plugins"), enabled=["domestic_live"])
MANAGER.load()
assert "douyu" in MANAGER.platforms, MANAGER.summary
api = importlib.import_module("ddm_plugin_domestic_live.douyu_interaction")
ui = importlib.import_module("ddm_plugin_domestic_live.douyu_composer")
huya = importlib.import_module("ddm_plugin_domestic_live.huya_interaction")
receive = importlib.import_module("ddm_plugin_domestic_live.douyu_danmaku")


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

    def test_wallet_only_refresh_does_not_fetch_gift_catalog(self):
        client, session = self.client()
        session.get.side_effect = [response({"error": 0, "msg": {"gold": 100, "silver": 214}})]
        self.assertEqual(client.wallet("9999"), api.Wallet(100, 214))
        session.get.assert_called_once()
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
            self.assertEqual(client.send_text("9999", "hello", progress=phases.append), api.ChatReceipt("42", "我", "hello"))
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

    def test_quantity_has_visible_increment_decrement_and_limits(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.Gift(1, "赞", 10, "YUCHI", 0, 3)
        popup.choose(gift)
        self.assertEqual(popup.quantity.buttonSymbols(), ui.QSpinBox.NoButtons)
        self.assertFalse(popup.decrease.isEnabled())
        self.assertEqual(popup.increase.text(), "+")
        self.assertEqual(popup.decrease.text(), "−")
        popup.increase.click()
        self.assertEqual(popup.quantity.value(), 2)
        popup.increase.click()
        self.assertFalse(popup.increase.isEnabled())
        popup.decrease.click()
        self.assertEqual(popup.quantity.value(), 2)
        self.assertIn("减少", popup.decrease.toolTip())
        self.assertIn("增加", popup.increase.accessibleName())

    def test_composer_text_and_placeholder_follow_live_theme_switch(self):
        from ddm import theme
        try:
            for mode in ("dark", "light", "dark"):
                theme.apply(mode)
                self.widget.show()
                self.widget.edit.ensurePolished()
                APP.processEvents()
                palette = self.widget.edit.palette()
                self.assertEqual(palette.color(QPalette.Text), theme.qcolor(theme.TEXT1))
                self.assertEqual(palette.color(QPalette.PlaceholderText), theme.qcolor(theme.TEXT3))
        finally:
            theme.apply("dark")

    def test_only_acknowledged_current_room_text_reaches_receiver(self):
        received = Mock()
        self.widget.chat_relay.confirmed.connect(received)
        key = self.widget.snapshot()
        receipt = api.ChatReceipt("42", "测试用户", "hello")
        self.widget.completed(object(), (key, "text", "hello", dict(self.widget.room)), api.InteractionError("timeout"))
        received.assert_not_called()
        self.widget.completed(object(), (("douyu:123", key[1]), "text", "hello", {}), receipt)
        received.assert_not_called()
        self.widget.completed(object(), (key, "text", "hello", dict(self.widget.room)), receipt)
        received.assert_called_once_with("douyu:9999", receipt)

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
        previous_card = popup.grid.itemAt(0).widget()
        previous_card.show()
        popup.tabs.setCurrentIndex(1)
        self.assertEqual(popup.grid.count(), 0)
        self.assertTrue(previous_card.isHidden())
        self.assertIsNone(popup.selected)
        self.assertFalse(popup.send.isEnabled())
        popup.set_panel(panel)
        self.assertEqual(popup.tabs.tabData(popup.tabs.currentIndex()), 2)

    def test_wallet_update_keeps_gift_cards_selection_quantity_and_category(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.gift_catalog(catalog())[0]
        tabs = (api.GiftTab(1, "礼物", (gift,)), api.GiftTab(2, "特权", (gift,)))
        popup.set_panel(api.GiftPanel((gift,), tabs, api.Wallet(200, 214)))
        popup.tabs.setCurrentIndex(1)
        popup.choose(gift)
        popup.quantity.setValue(6)
        card = popup.grid.itemAt(0).widget()
        with patch.object(popup, "set_gifts") as rebuild, patch.object(popup, "stop_images") as stop:
            popup.set_panel(api.GiftPanel((gift,), tabs, api.Wallet(300, 114)))
            rebuild.assert_not_called()
            stop.assert_not_called()
        self.assertIs(popup.grid.itemAt(0).widget(), card)
        self.assertIs(popup.selected, gift)
        self.assertEqual(popup.quantity.value(), 6)
        self.assertEqual(popup.tabs.tabData(popup.tabs.currentIndex()), 2)
        self.assertIn("鱼丸：114", popup.wallet_label.text())

    def test_temporarily_hiding_picker_does_not_cancel_retained_images(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        reply = Mock()
        popup.images.add(reply)
        popup.hideEvent(QHideEvent())
        reply.abort.assert_not_called()
        self.widget.shutdown()
        reply.abort.assert_called_once()

    def test_balance_completion_does_not_clear_another_tile_send_lock(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        self.owner.sending = True
        self.widget.completed(object(), (self.widget.snapshot(), "wallet", "", dict(self.widget.room)), api.Wallet(100, 214))
        self.assertTrue(self.owner.sending)
        self.assertEqual(popup.wallet, api.Wallet(100, 214))
        self.owner.context.notify.assert_not_called()

    def test_wallet_error_keeps_cards_and_does_not_report_send_failure(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.gift_catalog(catalog())[0]
        popup.set_panel(api.GiftPanel((gift,), (api.GiftTab(1, "礼物", (gift,)),), api.Wallet(100, 214)))
        popup.choose(gift)
        card = popup.grid.itemAt(0).widget()
        self.widget.completed(object(), (self.widget.snapshot(), "wallet", "", dict(self.widget.room)), api.InteractionError("余额暂不可用"))
        self.assertIs(popup.grid.itemAt(0).widget(), card)
        self.assertIs(popup.selected, gift)
        self.assertEqual(popup.wallet_label.text(), "余额：未知")
        self.owner.context.notify.assert_not_called()

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
        with patch.object(popup, "isVisible", return_value=True), \
                patch.object(self.widget, "refresh_wallet") as refresh, patch.object(self.widget, "load_gifts") as catalog_refresh:
            self.widget.completed(object(), (self.widget.snapshot(), "gift", "", dict(self.widget.room)), {})
            refresh.assert_called_once()
            catalog_refresh.assert_not_called()
        self.owner.context.notify.assert_not_called()

    def test_gift_confirmation_has_clear_transaction_fields_and_safe_default(self):
        gift = api.Gift(7, "100鱼丸", 100, "YUWAN", 0, 10)
        room = {"room_id": "douyu:6979222", "uname": "玩机器、Machine <主播>"}
        dialog = ui.GiftConfirmDialog(room, gift, 1, api.Wallet(0, 214), self.parent)
        try:
            self.assertEqual(dialog.recipient.text(), room["uname"])
            self.assertEqual(dialog.recipient.textFormat(), Qt.PlainText)
            self.assertEqual(dialog.room_label.text(), "斗鱼房间 6979222")
            self.assertEqual(dialog.gift_label.text(), "100鱼丸")
            self.assertEqual(dialog.quantity_label.text(), "1")
            self.assertEqual(dialog.cost_label.text(), "100 鱼丸")
            self.assertEqual(dialog.cancel.text(), "取消")
            self.assertEqual(dialog.confirm.text(), "确认赠送")
            self.assertTrue(dialog.cancel.isDefault())
            self.assertFalse(dialog.confirm.autoDefault())
        finally:
            dialog.deleteLater()

    def test_cancelled_confirmation_never_sends(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.gift_catalog(catalog())[0]
        popup.set_gifts((gift,))
        popup.choose(gift)
        with patch.object(ui.GiftConfirmDialog, "exec", return_value=ui.QDialog.Rejected), \
                patch.object(self.widget, "send_gift") as send:
            popup.confirm()
            send.assert_not_called()

    def test_account_change_during_confirmation_prevents_spending(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.gift_catalog(catalog())[0]
        popup.set_gifts((gift,))
        popup.choose(gift)
        def accept_after_account_change():
            self.owner.platform._playback_cookies = lambda: requests.cookies.RequestsCookieJar()
            return ui.QDialog.Accepted
        with patch.object(ui.GiftConfirmDialog, "exec", side_effect=accept_after_account_change), \
                patch.object(self.widget, "send_gift") as send:
            popup.confirm()
            send.assert_not_called()

    def test_accepted_confirmation_sends_exact_captured_selection_once(self):
        popup = ui.GiftPopup(self.widget)
        self.widget.popup = popup
        gift = api.gift_catalog(catalog())[0]
        popup.set_gifts((gift,))
        popup.choose(gift)
        popup.quantity.setValue(3)
        key = self.widget.snapshot()
        with patch.object(ui.GiftConfirmDialog, "exec", return_value=ui.QDialog.Accepted), \
                patch.object(self.widget, "send_gift") as send:
            popup.confirm()
            send.assert_called_once_with(gift, 3, key)

    def test_cross_tile_send_does_not_start_second_request(self):
        self.owner.sending = True
        with patch.object(ui.Request, "start") as start:
            self.widget.start("text", Mock())
            start.assert_not_called()
        self.assertIn("已有发送请求", self.widget.edit.toolTip())

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


class ReceiveTests(unittest.TestCase):
    def setUp(self):
        self.jar = cookie_jar()
        self.jar.set("acf_uid", "42", domain=".douyu.com", path="/")
        self.platform = SimpleNamespace(normalize=lambda value: "douyu:" + str(value).removeprefix("douyu:"),
            _playback_cookies=lambda: self.jar, room_info=Mock(return_value=SimpleNamespace(room_id="douyu:8888")))
        self.client = receive.DouyuDanmakuClient("9999", self.platform)
        self.events = []
        self.client.message.connect(self.events.append)
        self.receipt = api.ChatReceipt("42", "用户", "hello")

    def tearDown(self):
        self.client.stop()
        self.client.deleteLater()
        APP.processEvents()

    def broadcast(self, uid="42", text="hello"):
        self.client.receive_broadcast({"uid": uid, "txt": text, "nn": "用户"})

    def test_broadcast_preserves_uid_and_marks_only_current_user(self):
        self.broadcast()
        self.broadcast("24")
        self.assertEqual(self.events[0]["uid"], "42")
        self.assertTrue(self.events[0]["self_sent"])
        self.assertFalse(self.events[1]["self_sent"])
        self.jar.set("acf_uid", "24", domain=".douyu.com", path="/")
        with patch.object(receive.time, "monotonic", return_value=self.client._identity_deadline + .01):
            self.broadcast()
        self.assertFalse(self.events[-1]["self_sent"])

    def test_confirmed_send_visible_without_public_broadcast_and_boxed(self):
        receive.relay_for(self.platform).confirmed.emit("douyu:9999", self.receipt)
        self.assertEqual(len(self.events), 1)
        self.assertTrue(self.events[0]["self_sent"])
        self.assertEqual(self.events[0]["delivery"], "send_receipt")


    def test_confirmed_event_uses_shared_bilibili_outline_renderer(self):
        from ddm.video_danmaku import VideoDanmaku
        self.client.receive_confirmed("douyu:9999", self.receipt)
        overlay = VideoDanmaku()
        overlay.resize(640, 360)
        overlay.enabled = overlay.active = True
        overlay.show()
        APP.processEvents()
        try:
            with patch.object(VideoDanmaku, "_draw_self_sent_box", wraps=VideoDanmaku._draw_self_sent_box) as draw:
                self.assertTrue(overlay.add_event(self.events[0]))
                draw.assert_called_once()
            self.assertTrue(overlay.comments[0].self_sent)
        finally:
            overlay.close()
            overlay.deleteLater()

    def test_receipt_then_public_broadcast_emits_once(self):
        self.client.receive_confirmed("douyu:9999", self.receipt)
        self.broadcast()
        self.assertEqual(len(self.events), 1)

    def test_public_broadcast_then_receipt_emits_once(self):
        self.broadcast()
        self.client.receive_confirmed("douyu:9999", self.receipt)
        self.assertEqual(len(self.events), 1)
        self.assertTrue(self.events[0]["self_sent"])

    def test_identical_messages_from_others_and_repeated_sends_not_lost(self):
        for _ in range(2):
            self.client.receive_confirmed("douyu:9999", self.receipt)
        self.broadcast("24")
        self.broadcast()
        self.broadcast()
        self.assertEqual(len(self.events), 3)

    def test_busy_room_does_not_evict_pending_own_receipt(self):
        self.client.receive_confirmed("douyu:9999", self.receipt)
        for number in range(400):
            self.broadcast("24", str(number))
        self.broadcast()
        self.assertEqual(len(self.events), 401)

    def test_old_match_expires_and_stop_or_other_room_cannot_echo(self):
        with patch.object(receive.time, "monotonic", return_value=0):
            self.client.receive_confirmed("douyu:9999", self.receipt)
        with patch.object(receive.time, "monotonic", return_value=31):
            self.broadcast()
        self.assertEqual(len(self.events), 2)
        self.client.receive_confirmed("douyu:123", self.receipt)
        self.client.stop()
        self.client.receive_confirmed("douyu:9999", self.receipt)
        self.broadcast()
        self.assertEqual(len(self.events), 2)

    def test_bursts_do_not_repeatedly_load_credentials_on_gui_thread(self):
        with patch.object(self.platform, "_playback_cookies", return_value=self.jar) as load:
            with patch.object(receive.time, "monotonic", return_value=100):
                for index in range(1000):
                    self.broadcast("42", str(index))
            self.assertEqual(load.call_count, 1)
            self.assertEqual(len(self.events), 1000)
            self.assertTrue(all(event["self_sent"] for event in self.events))
            self.jar.clear()
            with patch.object(receive.time, "monotonic", return_value=101.01):
                self.broadcast()
            self.assertEqual(load.call_count, 2)
            self.assertFalse(self.events[-1]["self_sent"])

    def test_real_tcp_server_requires_ping_reply_to_continue_chat(self):
        async def scenario():
            received, closed = asyncio.Event(), asyncio.Event()
            replies = []
            async def serve(reader, writer):
                buffer = bytearray()
                try:
                    while chunk := await reader.read(4096):
                        buffer.extend(chunk)
                        for event in api.douyu_messages(buffer):
                            if event.get("type") == "loginreq":
                                writer.write(api.packet({"type": "loginres"}))
                            elif event.get("type") == "joingroup":
                                writer.write(api.packet({"type": "pingreq", "tick": "first"}))
                            elif event.get("type") == "pingres":
                                replies.append(event.get("tick"))
                                writer.write(api.packet({"type": "chatmsg", "uid": "42", "txt": event["tick"]}))
                                if event["tick"] == "first":
                                    writer.write(api.packet({"type": "pingreq", "tick": "second"}))
                                else:
                                    received.set()
                        await writer.drain()
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
                    closed.set()
            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            connect = asyncio.open_connection
            async def redirect(_host, _port):
                return await connect("127.0.0.1", server.sockets[0].getsockname()[1])
            task = None
            try:
                with patch.object(receive.asyncio, "open_connection", redirect):
                    task = asyncio.create_task(self.client._douyu())
                    await asyncio.wait_for(received.wait(), 1)
                    for _ in range(10):
                        if len(self.events) == 2:
                            break
                        await asyncio.sleep(.005)
                    self.assertEqual([event["text"] for event in self.events], ["first", "second"])
                    self.assertEqual(replies, ["first", "second"])
            finally:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await asyncio.wait_for(closed.wait(), 1)
                server.close()
                await server.wait_closed()
        asyncio.run(scenario())

    def test_silent_socket_times_out_and_close_cannot_block_reconnect(self):
        async def scenario():
            first = True
            async def read(_size):
                nonlocal first
                if first:
                    first = False
                    return api.packet({"type": "loginres"})
                await asyncio.Future()
            async def stuck_close():
                await asyncio.Future()
            writer = SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock(),
                wait_closed=stuck_close, transport=Mock())
            with patch.object(receive.asyncio, "open_connection", new=AsyncMock(return_value=(SimpleNamespace(read=read), writer))), \
                    patch.object(receive, "DOUYU_HEARTBEAT", .01), \
                    patch.object(receive, "DOUYU_RECEIVE_TIMEOUT", .04), \
                    patch.object(receive, "DOUYU_CLOSE_TIMEOUT", .01):
                with self.assertRaisesRegex(ValueError, "Douyu response timeout"):
                    await asyncio.wait_for(self.client._douyu(), .3)
            sent = api.douyu_messages(bytearray(b"".join(call.args[0] for call in writer.write.call_args_list)))
            self.assertGreaterEqual(sum(event.get("type") == "mrkl" for event in sent), 2)
            writer.transport.abort.assert_called_once()
        asyncio.run(scenario())

    def test_quiet_room_stays_connected_when_heartbeat_replies_continue(self):
        async def scenario():
            closed = asyncio.Event()
            heartbeats = []
            async def serve(reader, writer):
                buffer = bytearray()
                try:
                    while chunk := await reader.read(4096):
                        buffer.extend(chunk)
                        for event in api.douyu_messages(buffer):
                            if event.get("type") == "loginreq":
                                writer.write(api.packet({"type": "loginres"}))
                            elif event.get("type") == "mrkl":
                                heartbeats.append(event)
                                writer.write(api.packet({"type": "mrkl"}))
                        await writer.drain()
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
                    closed.set()
            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            connect = asyncio.open_connection
            async def redirect(_host, _port):
                return await connect("127.0.0.1", server.sockets[0].getsockname()[1])
            task = None
            try:
                with patch.object(receive.asyncio, "open_connection", redirect), \
                        patch.object(receive, "DOUYU_HEARTBEAT", .03), \
                        patch.object(receive, "DOUYU_RECEIVE_TIMEOUT", .2):
                    task = asyncio.create_task(self.client._douyu())
                    await asyncio.sleep(.45)
                    self.assertFalse(task.done(), f"Quiet chat must stay connected: {task.exception() if task.done() else None}, replies={len(heartbeats)}")
                    self.assertGreaterEqual(len(heartbeats), 4)
                    self.assertEqual(self.events, [])
            finally:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await asyncio.wait_for(closed.wait(), 1)
                server.close()
                await server.wait_closed()
        asyncio.run(scenario())

    def test_reconnect_reuses_resolved_real_room_without_another_http_lookup(self):
        writer = SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock(), wait_closed=AsyncMock())
        with patch.object(receive.asyncio, "open_connection", new=AsyncMock(return_value=(SimpleNamespace(read=AsyncMock(return_value=b"")), writer))):
            for attempt in range(2):
                with self.assertRaises(OSError):
                    asyncio.run(self.client._douyu(attempt))
        self.platform.room_info.assert_called_once_with("douyu:9999")
        sent = api.douyu_messages(bytearray(b"".join(call.args[0] for call in writer.write.call_args_list)))
        self.assertTrue(all(event["roomid"] == "8888" for event in sent if event.get("type") == "loginreq"))

    def test_server_ping_is_answered_immediately_and_keeps_receiving(self):
        login = api.packet({"type": "loginres"})
        first = api.packet({"type": "pingreq", "tick": "1793118592460"})
        second = api.packet({"type": "pingreq", "tick": "next/@"})
        chat = api.packet({"type": "chatmsg", "uid": "42", "txt": "after ping", "nn": "用户"})
        chunks = iter([login + first[:8], first[8:], second, chat, b""])
        writer = SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock(), wait_closed=AsyncMock())
        def sent():
            return api.douyu_messages(bytearray(b"".join(call.args[0] for call in writer.write.call_args_list)))
        async def read(_size):
            chunk = next(chunks)
            if chunk == second:
                self.assertTrue(any(event == {"type": "pingres", "tick": "1793118592460"} for event in sent()),
                                "Must answer ping before waiting for another read or heartbeat")
            if chunk == chat:
                self.assertTrue(any(event == {"type": "pingres", "tick": "next/@"} for event in sent()))
            return chunk
        with patch.object(receive.asyncio, "open_connection", new=AsyncMock(return_value=(SimpleNamespace(read=read), writer))):
            with self.assertRaises(OSError):
                asyncio.run(self.client._douyu())
        APP.processEvents()
        self.assertEqual([event["text"] for event in self.events], ["after ping"])
        self.assertEqual(sum(event.get("type") == "joingroup" for event in sent()), 1)
        self.assertEqual(sum(event.get("type") == "pingres" for event in sent()), 2)

    def test_split_packets_canonical_room_and_subscription_survive(self):
        packet = api.packet({"type": "loginres"}) + api.packet({"type": "chatmsg", "uid": "42", "txt": "hello", "nn": "用户"})
        reader = SimpleNamespace(read=AsyncMock(side_effect=[packet[:9], packet[9:27], packet[27:], b""]))
        writer = SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock(), wait_closed=AsyncMock())
        with patch.object(receive.asyncio, "open_connection", new=AsyncMock(return_value=(reader, writer))):
            with self.assertRaises(OSError):
                asyncio.run(self.client._douyu())
        APP.processEvents()
        self.assertEqual(self.client.canonical_room, "douyu:8888")
        self.assertEqual(len(self.events), 1)
        self.assertTrue(self.events[0]["self_sent"])
        sent = api.douyu_messages(bytearray(b"".join(call.args[0] for call in writer.write.call_args_list)))
        self.assertTrue(any(event.get("type") == "joingroup" and event.get("rid") == "8888" for event in sent))
        writer.close.assert_called_once()


class HuyaTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(cookies=cookie_jar(), headers={})
        self.session.__enter__ = Mock(return_value=self.session)
        self.session.__exit__ = Mock(return_value=False)
        self.session.get.return_value = response({})
        self.session.get.return_value.text = 'var TT_ROOM_DATA = {"id":42,"channel":100,"sid":200};'
        self.platform = SimpleNamespace(normalize=lambda room: room if str(room).startswith("huya:") else "huya:"+str(room),
            room_url=lambda room: "https://www.huya.com/"+room.split(":")[-1],
            account_info=Mock(return_value={"uid":"42"}), _playback_cookies=cookie_jar)
        self.client = huya.HuyaAPI(self.platform, cookie_jar(), session_factory=lambda: self.session)
        self.session.post.side_effect = self.ack

    def ack(self, *args, **kwargs):
        envelope = huya.tars_fields(kwargs["data"][4:])
        packet = self.reply(envelope[4])
        result = response({})
        result.content = packet
        return result

    def reply(self, request_id=7, status=0, method="sendMessage", *, missing_status=False):
        payload = b"\x0a" + (b"" if missing_status else huya.tars_int(0,status)) + b"\x0b"
        values = b"\x08"+huya.tars_int(0,1)+huya.huya_string(0,"tRsp")+huya.tars_bytes(1,payload)
        body = huya.tars_int(1,3)+huya.tars_int(4,request_id)+huya.huya_string(5,"liveuif")+huya.huya_string(6,method)+huya.tars_bytes(7,values)
        return struct.pack(">I",len(body)+4)+body

    def test_wup_send_has_explicit_success_and_correct_room_and_text(self):
        self.assertEqual(self.client.send_text("huya:lpl", " 123 "), "虎牙已确认发送")
        self.session.post.assert_called_once()
        call=self.session.post.call_args
        self.assertEqual(call.args[0],"https://cdnws.api.huya.com/")
        self.assertFalse(call.kwargs["allow_redirects"])
        env=huya.tars_fields(call.kwargs["data"][4:])
        self.assertEqual((env[5],env[6]),("liveuif","sendMessage"))
        values=huya.tars_fields(env[7])[0]
        body=huya.tars_fields(dict(zip(values[::2],values[1::2]))["tReq"])[0]
        self.assertEqual((body[1],body[2],body[3],body[8]),(100,200,"123",42))
        self.assertEqual(body[0][0],42)

    def test_reply_matches_id_method_and_status(self):
        for data in [self.reply(8), self.reply(method="other"), self.reply(status=1),self.reply(missing_status=True),b"bad"]:
            with self.assertRaises(api.InteractionError):huya.parse_reply(data,7)
        self.assertEqual(huya.parse_reply(self.reply(),7),"虎牙已确认发送")

    def test_timeout_not_retried_or_exposed(self):
        self.session.post.side_effect=requests.Timeout("secret baseinfo=private")
        with self.assertRaises(api.InteractionError) as caught:self.client.send_text("huya:lpl","123")
        self.assertNotIn("private",str(caught.exception))
        self.session.post.assert_called_once()

    def test_redirect_not_success(self):
        self.session.post.side_effect=None
        self.session.post.return_value=response({})
        self.session.post.return_value.is_redirect=True
        with self.assertRaises(api.InteractionError):self.client.send_text("huya:lpl","123")
        self.session.post.assert_called_once()

    def test_login_required_does_not_send(self):
        self.platform.account_info.side_effect=RuntimeError("expired")
        with self.assertRaises(api.LoginRequired):self.client.send_text("huya:lpl","123")
        self.session.post.assert_not_called()

    def test_invalid_draft_cancel_and_closed_room_do_not_send(self):
        for value in ("", " ", "x"*101):
            with self.assertRaises(api.InteractionError):self.client.send_text("huya:lpl",value)
        with self.assertRaises(api.InteractionError):self.client.send_text("huya:lpl","123",lambda: True)
        self.session.get.return_value.text='var TT_ROOM_DATA = {"id":0,"channel":0,"sid":0};'
        with self.assertRaises(api.InteractionError):self.client.send_text("huya:lpl","123")
        self.session.post.assert_not_called()

    def test_huya_composer_enter_login_and_platform_visibility(self):
        parent=QWidget()
        owner=SimpleNamespace(platform=self.platform,context=SimpleNamespace(notify=Mock(),request_login=Mock()),sending=False,requests=set())
        widget=ui.HuyaComposer(owner,parent)
        try:
            parent.show()
            widget.set_room({"platform":"huya","room_id":"huya:lpl"})
            APP.processEvents()
            self.assertFalse(widget.isHidden())
            self.assertTrue(widget.gifts.isHidden())
            widget.edit.setText("123")
            with patch.object(widget,"start") as start:
                QTest.keyClick(widget.edit,Qt.Key_Return)
                start.assert_called_once()
            widget.feedback("登录过期",login_required=True)
            owner.context.notify.call_args.kwargs["on_click"]()
            owner.context.request_login.assert_called_once_with("huya",reauthenticate=True)
            widget.completed(object(),(widget.snapshot(),"text","123",dict(widget.room)),"虎牙已确认发送")
            self.assertEqual(widget.edit.text(),"")
            widget.set_room({"platform":"douyu","room_id":"douyu:123"})
            self.assertTrue(widget.isHidden())
        finally:
            widget.shutdown();parent.close();parent.deleteLater();APP.processEvents()


if __name__ == "__main__":
    try:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    finally:
        MANAGER.unload()
