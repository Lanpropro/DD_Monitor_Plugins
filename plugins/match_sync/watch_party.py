"""Opt-in measurement and alignment of locally rendered live pictures."""
from bisect import bisect_left
from pathlib import Path
import os
import time

from PySide6.QtCore import Qt, QTimer, Signal
from .local_control import LocalControlServer
from .watch_party_panel import WatchPartyPanel
from .watch_profile import display_name, sidebar_profile
from .watch_room_route import RoutedRoomLink as RoomLink
from .watch_party_progress import LockedProgress, device_rate_ppm


def frame_observation(viewer, *, playback_clock=False):
    row = viewer.rows.get(viewer.alignment.reference)
    if (not viewer.running or viewer.replay.active or viewer.sync_waiting or row is None or row.paused
            or not row.decoder):
        return None
    decoder = row.decoder
    history = decoder.history
    if history.origin is None:
        return None
    if playback_clock:
        shift = viewer.rendered_shifts.get(viewer.alignment.reference)
        if shift is None:
            return None
        stamp = viewer.live_clock() + shift
    elif not viewer.canvas.isVisible() or viewer.window().isMinimized():
        return None
    elif viewer.canvas.native_presenting:
        gpu = viewer.d3d11_picture
        latest = gpu.worker.latest if gpu and gpu.worker is not None else None
        if latest is None or latest.target.decoder is not decoder or not latest.displayed_stamp:
            return None
        stamp = latest.displayed_stamp
    else:
        stamp = viewer.canvas.painted_stamp
    if stamp is None:
        return None
    with history.lock:
        details = history.details
        i = bisect_left(details, stamp, key=lambda item: item.time)
        candidates = [details[j] for j in (i - 1, i) if 0 <= j < len(details)]
    if not candidates:
        return None
    feature = min(candidates, key=lambda item: abs(item.time - stamp))
    if abs(feature.time - stamp) > .045 or feature.texture < 4:
        return None
    return (decoder, history, decoder.crop_generation), stamp, feature


def delay_playback(viewer, stamp, seconds, *, playback_clock=False):
    row = viewer.rows[viewer.alignment.reference]
    history = row.decoder.history
    target = stamp - seconds
    bounds = history.bounds_at(target)
    current = viewer.live_clock() + viewer.rendered_shifts[viewer.alignment.reference] if playback_clock else viewer.canvas.frame_key
    if (not bounds or not bounds[0] <= target <= bounds[1] - .1
            or history.frame_at(target) is None or current is None or abs(current - stamp) > .35):
        return False
    clock = viewer.live_clock()
    viewer.audio.correction -= seconds
    viewer.audio.flush(clock - seconds)
    viewer._presentation_update = None
    return True


class WatchRoom(WatchPartyPanel):
    received = Signal(object, object)

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.local_control = LocalControlServer(self, self.control_request)
        self.link = self.source = self.last_frame = self.pending = None
        self.last_seen = 0
        self.progress = LockedProgress()
        self.saved_directory = None
        self.side = None
        self.last_screen = self.previous_diag = None
        self.screen_changed = self.diag_due = self.last_tick = self.gui_gap = 0.
        self.members = []
        self.member_list.hide()
        self.code.textEdited.connect(lambda: self.set_phase(""))
        self.create.clicked.connect(lambda: self.start(None))
        self.join.clicked.connect(lambda: self.start(self.code.text().strip()))
        self.code.returnPressed.connect(self.join.click)
        self.manual.clicked.connect(self.recheck)
        self.exit_room.clicked.connect(self.leave)
        self.received.connect(self.handle, Qt.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(90)
        self.timer.timeout.connect(self.sample)
        self.controls()

    def show_members(self, members):
        if self.members != members:
            self.set_members(members, self.side)
        self.members = members

    def controls(self, *, reconnecting=False):
        link = self.link
        self.set_room(link is not None, link.code if link and self.side is not None else None,
                      link.mode if link else None, len(self.members), reconnecting=reconnecting)

    def control_request(self, request):
        link = self.link
        if not link:
            raise ValueError("No active watch party")
        if request == {"command": "recheck"}:
            requested = self.recheck()
            return {"requested": requested, "status": self.status.text()}
        if request == {"command": "state"}:
            return {"room": link.code if self.side is not None else None, "side": self.side,
                    "members_online": len(self.members), "online": link.online, "locked": link.locked, "status": self.status.text()}
        raise ValueError("Unknown watch-party command")

    def recheck(self):
        if self.link and self.link.online:
            self.pending = None
            self.link.commands.append({"type":"manual"})
            self.set_phase("matching", "正在复核画面", "会用一段新的共同画面重新测量。")
            return True
        return False

    def start(self, code):
        if self.link:
            return
        if code is not None and not (len(code) == 6 and code.isascii() and code.isdigit()):
            self.set_phase("", "请输入六位房间码。")
            return
        if not self.viewer.running:
            self.set_phase("", "请先开始播放共同主播的直播。")
            return
        link = RoomLink(code, lambda msg: self.received.emit(link, msg), mode=self.mode.currentData())
        link.profile = sidebar_profile(self.viewer)
        self.profile_due = time.monotonic() + 2
        self.link = link
        self.controls()
        self.set_phase("connecting", "正在连接同看房间…")
        gpu = self.viewer.d3d11_picture
        packaged = Path(__file__).resolve().parent / "native" / "d3d11"
        if gpu and gpu.directory != packaged and (packaged / "presenter.exe").is_file():
            self.saved_directory = gpu.directory
            gpu.stop()
            gpu.directory = packaged
        self.local_control.listen(f"ddm-match-watch-party-{os.getpid()}")
        link.worker.start()
        self.viewer.canvas.watch_last_paint = time.perf_counter()
        self.timer.start()

    def leave(self, *_):
        self.local_control.close()
        if self.link:
            self.link.stopped.set()
        self.link = self.source = self.pending = self.last_frame = None
        self.side = None
        self.previous_diag = self.last_screen = None
        self.screen_changed = self.diag_due = self.last_tick = self.gui_gap = 0.
        self.progress = LockedProgress()
        self.timer.stop()
        self.viewer.canvas.watch_last_paint = None
        self.viewer.canvas.watch_paint_gap = 0.
        if self.saved_directory is not None:
            gpu = self.viewer.d3d11_picture
            gpu.stop()
            gpu.directory, self.saved_directory = self.saved_directory, None
        self.show_members([])
        self.set_phase("")
        self.controls()

    def handle(self, link, msg):
        if self.link is not link:
            return
        kind = msg["type"]
        if kind == "seat":
            self.side = msg["side"]
            self.controls()
        elif kind == "members":
            self.show_members(msg["members"])
        elif kind == "state":
            self.show_members(msg["members"])
            self.progress = LockedProgress()
            if msg["both"]:
                self.viewer.playback_rate = 1.0
            self.controls()
            self.pending = None
            if msg["both"]:
                self.set_phase("matching", "正在匹配共同画面", "找到可比对的画面后会自动复核。")
            else:
                self.set_phase("waiting", "等好友加入", "把六位房间码发给好友，最多 3 人同看。")
        elif kind == "network":
            self.show_members([])
            self.controls(reconnecting=self.side is not None)
            self.pending = None
            if self.side is None:
                self.set_phase("connecting", msg["text"])
            else:
                self.set_phase("reconnecting", "正在重新连接", "连接恢复后会重新确认画面对齐。")
        elif kind == "error":
            self.leave()
            self.set_phase("", {"room_missing":"房间号不存在或已结束，请核对。",
                                 "connect_failed":"无法连接房间服务，请检查网络后重试。",
                                 "tunnel_integrity":"隧道程序校验失败，请重新建房。",
                                 "tunnel_start":"本机公网入口启动失败，可重试或选择云端建房。",
                                 "tunnel_stopped":"本机公网入口已关闭，请重新建房。",
                                 "lease_lost":"房间码续期失败，请重新建房。",
                                 "host_offline":"房主暂时离线。", "room_full":"房间已满（3/3 人）。"
                                 }.get(msg["reason"], "连接已失效，请重新建房或加入。"))
        elif kind == "measurement":
            lag = msg["lag"]
            if msg.get("reset") or msg.get("locked"):
                self.progress = LockedProgress()
            if lag is None:
                self.pending = None
                if "held" in msg:
                    self.set_phase("matching", "正在匹配共同画面", f"短暂失配，保留上次偏差 {abs(msg['held'])} ms。")
                else:
                    title = "正在复核画面" if msg.get("manual") else "正在匹配共同画面"
                    self.set_phase("matching", title, "会用一段新的共同画面重新测量。")
                return
            if len(self.members) == 3:
                difference = f"三人最大画面时差 {msg['spread']} ms"
            elif lag:
                side = msg["leading"]
                profile = next((m["profile"] for m in self.members if m["side"] == side), None)
                leading = "你的" if side == self.side else f"{display_name(profile)}的"
                difference = f"{leading}画面领先 {abs(lag)} ms"
            else:
                difference = "画面一致"
            report = difference
            if msg.get("clock"):
                report += "（含后台播放时钟估计）"
            if msg.get("locked"):
                self.set_phase("locked", "画面已锁定", "上次复核 · " + report,
                               (abs(lag), msg["p95"], msg["rtt"]))
                return
            elif msg.get("verifying"):
                report += "。正在用新画面复核。"
            if "delays" in msg:
                report += "。正在约时校准，随后复测。"
                delay = msg["delays"].get(str(self.side))
                if delay and self.source and link.offset is not None:
                    self.pending = (self.source, (msg["at"] - link.offset) / 1000, delay / 1000, msg["at"])
            self.set_phase("matching", "正在复核画面", report)

    def sample(self):
        link = self.link
        at = time.monotonic()
        if link and at >= self.profile_due:
            link.profile = sidebar_profile(self.viewer)
            self.profile_due = at + 2
        if not link or not link.online:
            self.pending = None
            self.previous_diag = None
            self.last_tick = self.gui_gap = 0.
            return
        viewer = self.viewer
        row = viewer.rows.get(viewer.alignment.reference)
        if row is None or row.decoder is None:
            if link.locked:
                self.progress.interrupted()
            self.pending = None
            self.previous_diag = None
            self.last_tick = self.gui_gap = 0.
            return
        source = row.decoder, row.decoder.history, row.decoder.crop_generation
        if self.source != source:
            if self.source is not None:
                if link.locked:
                    if self.source[:2] != source[:2]:
                        self.progress.interrupted()
                else:
                    link.reset_requested.set()
            self.source, self.pending, self.last_frame = source, None, None
            self.last_screen, self.screen_changed = None, at
        background = not viewer.window().isActiveWindow()
        self.diagnose(row.decoder, at, background)
        if link.locked:
            shift = viewer.rendered_shifts.get(viewer.alignment.reference)
            position = None
            if shift is not None and not (viewer.sync_waiting or row.paused or viewer.replay.active):
                position = viewer.live_clock() + shift
                if self.progress.recovering and row.decoder.history.frame_at(position) is None:
                    position = None
            device_rate = device_rate_ppm(viewer.audio.clock_observation, time.perf_counter())
            normal_rate = viewer.playback_rate == 1. and device_rate in (None, 1_000_000)
            if self.progress.recovered(at, position, normal_rate):
                link.reset_requested.set()
                self.set_phase("matching", "正在复核画面", "播放进度已改变，恢复后会复核全员画面。")
            return
        obs = frame_observation(viewer)
        if obs and self.last_screen != obs[1]:
            self.last_screen, self.screen_changed = obs[1], at
        use_clock = (viewer.window().isMinimized() or not viewer.canvas.isVisible()
                     or (background and at - self.screen_changed > .6))
        if use_clock:
            obs = frame_observation(viewer, playback_clock=True)
        link.basis = "clock" if use_clock else "screen"
        if obs is None:
            if self.pending and at >= self.pending[1]:
                link.commands.append(dict(type="applied", at=self.pending[3], ok=0))
                self.pending = None
            return
        source, stamp, feature = obs
        if stamp != self.last_frame:
            self.last_seen, self.last_frame = at, stamp
            link.points.append((at + feature.time - stamp, feature.signature, link.basis))
        if self.pending:
            owner, when, seconds, request_at = self.pending
            if at >= when:
                self.pending = None
                applied = (source == owner and at - when <= .45 and at - self.last_seen < .25
                           and delay_playback(viewer, stamp, seconds, playback_clock=use_clock))
                link.commands.append(dict(type="applied", at=request_at, ok=int(applied)))
                if applied:
                    self.set_phase("matching", "正在复核画面", f"本机已回调 {round(seconds * 1000)} ms。")
                else:
                    self.set_phase("matching", "正在匹配共同画面", "本机缓存或画面状态已变化。")

    def diagnose(self, decoder, now, background):
        if self.last_tick:
            self.gui_gap = max(self.gui_gap, now - self.last_tick)
        self.last_tick = now
        if now < self.diag_due:
            return
        self.diag_due = now + 2
        viewer = self.viewer
        canvas = viewer.canvas
        gpu = viewer.d3d11_picture
        latest = gpu.worker.latest if canvas.native_presenting and gpu and gpu.worker else None
        counts = (decoder.history.sequence, decoder.history.received_frames,
                  latest.submitted if latest else canvas.presented,
                  latest.presented if latest else canvas.painted_frames)
        key = decoder.history, latest.process_id if latest else None, background
        shift = viewer.rendered_shifts.get(viewer.alignment.reference)
        position = viewer.live_clock() + shift if shift is not None and not viewer.replay.active else None
        if self.previous_diag and self.previous_diag[0] == key:
            _, before, prev, previous_position = self.previous_diag
            bounds = decoder.history.bounds()
            ahead = max(0., bounds[1] - position) if bounds and position is not None else 0.
            fields = ("video_packets", "decoded", "submitted", "presented")
            report = {name:max(0, a-b) for name, a, b in zip(fields, counts, prev)}
            perf = time.perf_counter()
            received = decoder.received_cadence.stats(perf, decoder.fps)
            gpu_gap = max((g["gap_ms"] for t, g in tuple(gpu.worker.publish_gaps)
                           if t >= perf - (now - before)), default=0) if latest else 0
            self.link.commands.append(dict(type="diagnostic", duration_ms=round((now-before)*1000),
                rate_ppm=round(viewer.playback_rate * 1_000_000),
                device_rate_ppm=device_rate_ppm(viewer.audio.clock_observation, perf),
                progress_rate_ppm=(round((position - previous_position) / (now - before) * 1_000_000)
                                   if position is not None and previous_position is not None else None),
                gui_gap_ms=round(self.gui_gap*1000), buffer_ms=round(ahead*1000),
                decode_gap_ms=round(received["gap_max_ms"] or 0), gpu_submit_gap_ms=round(gpu_gap),
                paint_gap_ms=0 if latest else round(canvas.watch_paint_gap * 1000),
                fps=round(decoder.fps or 0), buffering=int(bool(viewer.sync_waiting)),
                native=int(latest is not None), background=int(background), **report))
        self.previous_diag = key, now, counts, position
        self.gui_gap = 0.
        canvas.watch_paint_gap = 0.
