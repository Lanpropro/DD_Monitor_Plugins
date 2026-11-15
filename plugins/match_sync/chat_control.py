"""Chat control. Session state remains on the Viewer facade."""
from __future__ import annotations

import time
from .chat_clients import Chat, PlatformChat

def _start_chat(viewer, row):
    viewer.overlay_pending.pop(row.room_id, None)
    previous, row.chat = row.chat, None
    if previous is not None:
        previous.stop()
    row.pending.clear()
    if not row.show_chat.isChecked():
        row.chat_text = "弹幕已关闭"
        row.refresh_status()
        viewer._refresh_chat_status()
        return
    row.chat_text = "弹幕连接中…"
    row.refresh_status()
    chat = (PlatformChat(row.room_id, row.platform, viewer.context.window)
            if row.platform is not None else Chat(row.room_id))
    row.chat = chat
    chat.events.state.connect(lambda text, r=row, c=chat: viewer._state(r, c, text, True))
    chat.events.message.connect(lambda event, r=row, c=chat: viewer._message(r, c, event))
    viewer._refresh_chat_status()
    chat.start()


def _refresh_chat_status(viewer):
    rows = [row for row in viewer.rows.values()
            if viewer.running and row.chat is not None and row.show_chat.isChecked()]
    connected = sum(row.chat_text.startswith(('已连接', '弹幕已连接')) for row in rows)
    if connected:
        text = f'已连接 · {connected}/{len(rows)}路'
    elif any(any(word in row.chat_text for word in ('连接中', '重连', '重试')) for row in rows):
        text = '连接中…'
    else:
        text = '连接已断开' if rows else '未接入'
    if viewer.panel._status != text:
        viewer.panel.set_status(text)
    viewer.panel.count.set_connections(connected)
    detail = '\n'.join(f'{row.label()}：{row.chat_text}' for row in rows)
    viewer.panel.count.setToolTip(detail or text)
    viewer.panel.count.setAccessibleDescription(detail or text)


def _message(viewer, row, worker, event):
    if viewer._valid(row, worker, True) and row.show_chat.isChecked():
        blocker = getattr(viewer.context.window, "_danmaku_blocked", None)
        if event.get("kind", "danmaku") == "danmaku" and blocker and blocker(event.get("text", "")):
            return
        now = time.monotonic()
        received = now + viewer.audio.correction
        decoder = row.decoder
        if decoder is not None:
            latest = decoder.history.latest()
            arrival = getattr(decoder, "last_frame_received", 0)
            if latest is not None and arrival and 0 <= now - arrival <= 3:
                # Messages arrive in wall time; playback follows decoded frame time.
                received = latest[0] + now - arrival
        row.pending.append((received, dict(event)))


def _render_overlay(viewer, clock, shifts):
    if viewer.video_danmaku is None:
        return
    if not viewer.video_danmaku.enabled:
        viewer.overlay_pending.clear()
        return
    keys = list(viewer.rows)
    start = keys.index(viewer.overlay_last) + 1 if viewer.overlay_last in keys else 0
    for key in keys[start:] + keys[:start]:
        pending = viewer.overlay_pending.get(key)
        if pending is None:
            continue
        received, event = pending
        if key not in shifts or not viewer.rows[key].show_chat.isChecked() or clock + shifts[key] - received > 5:
            viewer.overlay_pending.pop(key, None)
        else:
            overlay_event = dict(event)
            if not event.get('color'):
                overlay_event['text'] = f"【{event['source_label']}】 {event.get('text') or ''}"
            if viewer.video_danmaku.add_event(overlay_event):
                viewer.overlay_pending.pop(key, None)
                viewer.overlay_last = key


def _rebuild_replay_chat(viewer, stamp, live=False):
    viewer.panel.begin_replay_section()
    viewer.panel._follow_tail = True
    label = '返回直播' if live else '回看至'
    timestamp = time.strftime('%H:%M:%S', time.localtime(time.time() - (time.monotonic() - stamp)))
    viewer.panel.add_event({'text': f'{label} {timestamp}', '_replay_divider': True})
    viewer.overlay_pending.clear()
    if viewer.video_danmaku is not None:
        viewer.video_danmaku.clear()
    for event in viewer.replay.messages_between(stamp - 30, stamp):
        viewer.panel.add_event(event)
    viewer.panel.end_replay_section()
