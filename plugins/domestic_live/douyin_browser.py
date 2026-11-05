"""Send through the official editor using native Qt input, then verify real broadcast."""
import json
import threading
import time
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from .douyu_interaction import InteractionError
from .douyin_interaction import DeliveryUnconfirmed

SCRIPT = (Path(__file__).parent / 'douyin_native.js').read_text(encoding='utf-8')
DELIVERY_TIMEOUT = 8


def draft_shape_summary(shape):
    if not isinstance(shape, dict):
        return 'shape_unavailable'
    keys = ('raw_length read_length rendered_length zero_nodes ignored_zero_nodes '
            'string_nodes enter_nodes br_nodes placeholder lf cr tab space nbsp zwsp bom other').split()
    return ' '.join(f'{key}={shape[key]}' for key in keys
                    if type(shape.get(key)) is int and 0 <= shape[key] <= 1000000) or 'shape_unavailable'


def commit_text(browser, text):
    event = QInputMethodEvent()
    event.setCommitString(text)
    QCoreApplication.sendEvent(browser.focusProxy() or browser, event)


def press_enter(browser):
    target = browser.focusProxy() or browser
    for kind in (QEvent.KeyPress, QEvent.KeyRelease):
        QCoreApplication.sendEvent(target, QKeyEvent(kind, Qt.Key_Return, Qt.NoModifier, '\r'))


class BrowserChat(QObject):
    requested = Signal(object)
    completed = Signal(object, bool)

    def __init__(self, platform, context, parent):
        super().__init__(parent)
        self.platform, self.context = platform, context
        self.active = None
        self._active_request = None
        self.closed = False
        self.requested.connect(self.open)
        self.completed.connect(self.close_request)

    def finish(self, cancelled, *, delivery_unconfirmed=False):
        if not self.closed:
            self.completed.emit(cancelled, delivery_unconfirmed)

    def close_request(self, cancelled, delivery_unconfirmed=False):
        if self._active_request is not None and self._active_request['cancelled'] is cancelled:
            self.active.browser.page().runJavaScript('window.__ddmChatCancelled=true')
            if delivery_unconfirmed and not cancelled():
                self._active_request['retain_for_review']()
            if not self._active_request.get('manual_review') or cancelled():
                self.active.accept()

    def request(self, room, params, cancelled):
        request = dict(room=room, params=dict(params), cancelled=cancelled,
                       done=threading.Event(), result=None, deadline=time.monotonic() + 55)
        self.requested.emit(request)
        while not request['done'].wait(.1):
            if cancelled() or time.monotonic() > request['deadline']:
                request['done'].set()
                raise InteractionError('抖音官网输入发送已取消或超时，结果未确认；请核对直播间，不要立即重复发送')
        result = request['result']
        if isinstance(result, dict) and result.get('broadcast_id'):
            return result
        code = result.get('error', 'cancelled') if isinstance(result, dict) else 'cancelled'
        if code == 'native_unconfirmed':
            raise DeliveryUnconfirmed('已通过官网输入框执行发送，但软件尚未收到本人同内容的新广播；草稿已保留，请先在官网核对，不要立即重复发送。')
        labels = {'review_open': '请先关闭已保留的官网核对窗口，本次未提交',
                  'wrong_room_page': '官网页面与目标直播间不匹配，本次未提交',
                  'wrong_room_session': '官网当前直播与目标直播不一致，本次未提交',
                  'account_mismatch': '官网账号与所选账号不一致，本次未提交',
                  'ambiguous_editor': '官网出现多个输入框，本次未提交',
                  'official_draft_exists': '官网输入框已有草稿，已保留官网窗口，本次未覆盖或提交',
                  'input_focus_lost': '官网输入焦点已变化，本次未提交',
                  'input_mismatch': '官网输入内容与软件草稿不一致，本次未提交',
                  'cancelled_before_send': '提交前已取消', 'cancelled': '窗口已关闭或请求已取消'}
        raise InteractionError('抖音官网输入发送未确认：' + labels.get(code, '官网输入未就绪或返回未知状态') + '。')

    def open(self, request):
        if self.closed or request['done'].is_set() or request['cancelled']():
            request['done'].set()
            return
        if self._active_request is not None and self._active_request.get('manual_review'):
            request['result'] = {'error': 'review_open'}
            request['done'].set()
            return
        from ddm.platform_login import PlatformFollowDialog
        dialog = PlatformFollowDialog(self.platform, self.parent(), login_only=True,
                                      defer_login=True, owner=self.context.window)
        self.active, self._active_request = dialog, request
        dialog.setWindowTitle('抖音 · 官网输入发送')
        dialog.resize(1100, 720)
        dialog.status.setWordWrap(True)
        dialog.status.setText('正在准备官网输入环境，软件将自动输入并发送；如有登录或验证，请在下方完成。')
        dialog.remember.hide()
        dialog.forget_button.hide()
        dialog.read_button.setText('重新加载环境')
        dialog.read_button.clicked.disconnect()
        dialog.read_button.setEnabled(True)
        page = dialog.browser.page()
        for script in page.scripts().find('ddm-follow-list'):
            page.scripts().remove(script)
        page.setAudioMuted(True)
        task = dict(webRid=request['room'].split(':', 1)[1], roomId=request['params']['room_id'],
                    senderUid=request['params'].get('_sender_uid', ''), text=request['params']['content'])
        state = dict(phase='inspect', polling=False, submitted=False, since=None, holding=False, epoch=0)
        review = dict(since=None, text='')
        opened, last_stage = time.monotonic(), None
        def trace(stage):
            nonlocal last_stage
            if last_stage == stage:
                return
            last_stage = stage
            log = getattr(self.context, 'log', None)
            if callable(log):
                log(f"[douyin官网] 房间={request['room']} 阶段={stage} 耗时={time.monotonic() - opened:.2f}s")
        dialog.browser.loadStarted.connect(lambda: trace('page_loading'))
        dialog.browser.loadFinished.connect(lambda ok: trace('page_loaded' if ok else 'page_load_failed'))
        timer, review_timer = QTimer(dialog), QTimer(dialog)
        timer.setObjectName('ddm-chat-poll')
        review_timer.setObjectName('ddm-review-receiver')
        timer.setInterval(150)
        review_timer.setInterval(500)
        def observe_review():
            original = self.platform._chat_delivery.find_own(request['room'], task['senderUid'], task['text'], state['since'])
            if self.platform._chat_delivery.has_own_since(request['room'], task['senderUid'], review['since'], original):
                review_timer.stop()
                dialog.status.setText(review['text'] + '\n软件独立接收：已收到本人新增弹幕（至少1条）；原软件请求仍未确认。')
                trace('review_self_broadcast')
        review_timer.timeout.connect(observe_review)
        def retain_for_review():
            if review['since'] is not None:
                return
            request['manual_review'] = state['holding'] = True
            timer.stop()
            review['since'] = time.monotonic()
            review['text'] = ('官网已执行输入发送，弹幕投递仍未确认，自动发送已停止，草稿保留。'
                              '请核对原消息；若未出现，可在下方官网手动输入不同短文本作对照。')
            dialog.setWindowTitle('抖音 · 官网发送核对')
            dialog.read_button.setText('自动发送已停止')
            dialog.read_button.setEnabled(False)
            dialog.status.setText(review['text'] + '\n软件独立接收：尚未观察到本人新增弹幕（0条）。')
            trace('native_unconfirmed_review')
            review_timer.start()
        request['retain_for_review'] = retain_for_review
        def retry():
            if state['phase'] != 'inspect' or state['submitted'] or request['done'].is_set():
                return
            state.update(polling=False, epoch=state['epoch'] + 1)
            request['deadline'] = time.monotonic() + 55
            trace('user_reload_before_submit')
            dialog.browser.load(QUrl(self.platform.room_url(request['room'])))
        dialog.read_button.clicked.connect(retry)
        def poll():
            if request['cancelled']() or self.closed:
                page.runJavaScript('window.__ddmChatCancelled=true')
                dialog.reject()
                return
            if request['done'].is_set() or state['polling']:
                return
            if state['submitted']:
                mid = self.platform._chat_delivery.find_own(request['room'], task['senderUid'], task['text'], state['since'])
                if mid:
                    request['result'] = {'broadcast_id': mid}
                    state['holding'] = True
                    request['done'].set()
                    timer.stop()
                    trace('native_broadcast')
                elif time.monotonic() - state['since'] >= DELIVERY_TIMEOUT:
                    request['result'] = {'error': 'native_unconfirmed'}
                    retain_for_review()
                    request['done'].set()
                return
            state['polling'] = True
            epoch, action = state['epoch'], state['phase']
            def received(raw):
                if epoch != state['epoch']:
                    return
                state['polling'] = False
                if request['done'].is_set() or request['cancelled']() or self.closed:
                    return
                try:
                    result = json.loads(raw) if isinstance(raw, str) else raw
                except (TypeError, ValueError):
                    result = None
                if not isinstance(result, dict):
                    return
                if result.get('error'):
                    request['result'] = result
                    if result['error'] == 'official_draft_exists':
                        trace('official_draft_exists ' + draft_shape_summary(result.get('draft_shape')))
                        request['manual_review'] = state['holding'] = True
                        timer.stop()
                        dialog.setWindowTitle('抖音 · 输入框核对')
                        dialog.status.setText('检测到官网输入框包含内容，本次未执行输入或发送。原草稿已保留，可在下方核对。')
                        dialog.read_button.setText('自动发送已停止')
                        dialog.read_button.setEnabled(False)
                        request['done'].set()
                        return
                    request['done'].set()
                    dialog.accept()
                    return
                if not result.get('ready'):
                    reason = result.get('reason')
                    trace('prepare_' + (reason if reason in ('page', 'context', 'editor', 'input') else 'unknown'))
                    return
                if action == 'inspect':
                    state['phase'] = 'focus'
                    dialog.read_button.setEnabled(False)
                    dialog.browser.setFocus(Qt.OtherFocusReason)
                    dialog.status.setText('正在将软件草稿输入官网，请勿操作下方输入框。')
                    trace('native_focus')
                elif action == 'focus':
                    state['phase'] = 'verify'
                    commit_text(dialog.browser, task['text'])
                    trace('native_input')
                else:
                    state.update(submitted=True, since=time.monotonic())
                    request['deadline'] = time.monotonic() + DELIVERY_TIMEOUT + 2
                    press_enter(dialog.browser)
                    dialog.status.setText('官网已执行输入发送，正在等待软件独立接收确认，请勿重复发送。')
                    trace('native_submit')
            code = 'JSON.stringify((%s)(%s,%s))' % (SCRIPT, json.dumps(task), json.dumps(action))
            page.runJavaScript(code, received)
        timer.timeout.connect(poll)
        def finished(_result):
            trace('page_closed')
            timer.stop()
            review_timer.stop()
            request['done'].set()
            if self.active is dialog:
                self.active = self._active_request = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.browser.load(QUrl(self.platform.room_url(request['room'])))
        timer.start()
        dialog.show()

    def shutdown(self):
        self.closed = True
        if self.active is not None:
            self.active.reject()
