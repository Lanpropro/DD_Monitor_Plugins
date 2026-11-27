"""Check the installable plugin, real Qt entry, and live room clients using test signatures."""
import argparse
import os
from pathlib import Path
import queue
from random import Random
import sys
import subprocess
import tempfile
import time
from types import SimpleNamespace


def protocol(link_module, endpoint, diagnostic):
    from live_match import LiveMatch, picture_delta
    signatures = [Random(i).getrandbits(512) for i in range(800)]
    a = [(i * 90, sig) for i, sig in enumerate(signatures[:120])]
    b = [(t + 900, sig) for t, sig in a]
    assert picture_delta(a, b, b[-1][0]) == 900
    assert picture_delta(b, a, b[-1][0]) == -900
    static = [(t, 0) for t, _ in a]
    assert picture_delta(static, static, a[-1][0]) is None
    assert picture_delta(a, [(t, signatures[i+200]) for i,(t,_) in enumerate(a)], a[-1][0]) is None
    try:
        LiveMatch().observe(0, {"type":"frames","points":[[0,"x"*128]],"rtt":12}, 0)
        raise AssertionError("invalid signature accepted")
    except ValueError:
        pass
    policy = LiveMatch()
    assert "adjust" not in policy.decide((0, 200), 0) and policy.verification == (0, 200)
    assert policy.decide(None, 10000)["held"] == 200
    assert policy.decide(None, 21000)["held"] == 200 and list(policy.errors) == [200]
    assert "held" not in policy.decide(None, 22500) and not policy.errors
    for i, value in enumerate((480, -490, -505, None, -450, -455)):
        assert "adjust" not in policy.decide(None if value is None else (0, value), 25000 + i * 1800)
    assert policy.decide((0, -460), 37000)["side"] == 1
    check = LiveMatch()
    assert check.decide((0, 60), 0)["verifying"]
    assert check.decide((0, -100), 5000)["verifying"]
    assert check.decide((0, -80), 10000)["locked"]
    assert check.observe(1, {}, 99999) is None and not any(check.frames)
    check.reset(100000)
    assert not check.locked and check.verification is None
    boxes = [queue.Queue(), queue.Queue()]
    options = {} if endpoint == "packaged" else {"endpoint": endpoint}
    host = link_module.RoomLink(None, boxes[0].put, **options)
    host.worker.start()
    peer = None
    try:
        seat_deadline = time.monotonic() + 25
        while True:
            remaining = seat_deadline - time.monotonic()
            assert remaining > 0, "host could not reach the room endpoint"
            msg = boxes[0].get(timeout=remaining)
            if msg["type"] == "seat":
                break
            assert msg["type"] != "error", msg
        peer = link_module.RoomLink(msg["code"], boxes[1].put, **options)
        peer.worker.start()
        start = time.monotonic()
        initial = residual = deadline = None
        expected_side = None
        manual_received = set()
        locked_at = None
        recovered = False
        sent_diagnostic = False
        while time.monotonic() - start < 80 and len(manual_received) != 2:
            now = time.monotonic()
            tick = int((now - start) / .09) + 20
            if peer.online and not sent_diagnostic:
                host.commands.append(diagnostic)
                peer.commands.append(diagnostic)
                sent_diagnostic = True
            if host.locked and peer.locked:
                if locked_at is None:
                    locked_at = now
                if now - locked_at > 3:
                    assert len(host.points) == len(peer.points) == 16, "locked clients drained fingerprint queues"
                    if not recovered:
                        host.reset_requested.set()
                    else:
                        peer.commands.append({"type":"manual"})
                    locked_at = now
            for index, link in enumerate((host, peer)):
                adjusted = deadline is not None and now >= deadline
                delayed = index == 1 or adjusted
                link.points.append((now, signatures[tick - 10 * delayed], "screen"))
                while not boxes[index].empty():
                    update = boxes[index].get_nowait()
                    assert update["type"] != "error", update
                    if update.get("reset") and not update.get("manual"):
                        recovered = True
                        locked_at = None
                    if update.get("manual"):
                        manual_received.add(index)
                    if update["type"] == "measurement" and update["lag"] is not None:
                        if "adjust" in update:
                            if deadline is None:
                                initial = update["lag"]
                                expected_side = update["side"]
                                deadline = (update["at"] - link.offset) / 1000
                        elif adjusted and residual is None and abs(update["lag"]) <= 200:
                            residual = update["lag"]
            time.sleep(.09)
        assert initial is not None and abs(initial - 900) <= 200 and expected_side == 0, initial
        assert residual is not None, "missing independent post-adjustment measurement"
        assert recovered and manual_received == {0, 1}
    finally:
        for link in (host, peer):
            if link:
                link.stopped.set()
                link.worker.join(10)
                assert not link.worker.is_alive()
    print("PASS: known test lag, correct leading device, scheduled adjustment and fresh residual measurement")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", type=Path)
    source.add_argument("--installed", type=Path, help="Check the development installation using an isolated host")
    parser.add_argument("--monitor-python", type=Path, help="Run the stdio MCP check against the hidden fixture room")
    parser.add_argument("--endpoint", help="WebSocket URL, or packaged to exercise the plugin default")
    parser.add_argument("--output", type=Path, help="Save offscreen screenshots of the right console")
    args = parser.parse_args()
    monitor_python = args.monitor_python.resolve() if args.monitor_python else None
    host_root = args.host.resolve()
    entry = (args.installed / "plugin.py").resolve() if args.installed else None
    archive = args.zip.resolve() if args.zip else None
    output = args.output.resolve() if args.output else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(host_root), str(repo / "tools/watch_party")]
    os.chdir(host_root)
    os.environ["DDM_NO_SAVE"] = "1"
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget
    from ddm.account_widgets import AccountRow
    from ddm.avatar_widgets import Avatar
    from ddm import plugins, theme
    app = QApplication([])
    window = QWidget()
    window.setAttribute(Qt.WA_DontShowOnScreen)
    if output:
        window.setStyleSheet(theme.qss())
    window.settings, window.rooms = {}, []
    with tempfile.TemporaryDirectory() as temp:
        manager = plugins.PluginManager(window=window, plugins_dir=temp)
        if entry is not None:
            installed = manager._load_module("match_sync", str(entry))
            assert installed is not None
        else:
            manager.install_zip(str(archive))
            manager.load()
            installed, = manager.plugins
        module = sys.modules[type(installed).__module__]
        viewer = module.Viewer(plugins.PluginContext(manager, "match_sync"), {})
        try:
            viewer.setAttribute(Qt.WA_DontShowOnScreen)
            viewer.resize(1666, 1131)
            viewer.show()
            app.processEvents()
            from PySide6.QtTest import QTest
            console = viewer.send_console
            tabs = console.console_tabs
            dialog = viewer.watch_room
            def capture(name):
                app.processEvents()
                source = dialog.grab()
                preview = QPixmap(source.size())
                preview.setDevicePixelRatio(source.devicePixelRatio())
                preview.fill(theme.qcolor(theme.CONTENT))
                painter = QPainter(preview)
                painter.drawPixmap(0, 0, source)
                painter.end()
                assert preview.save(str(output / name))
            assert console.isAncestorOf(tabs) and console.isAncestorOf(dialog)
            assert tabs.tabText(0) == "弹幕控制台" and tabs.tabText(1) == "好友同看"
            assert not dialog.isWindow() and not dialog.isVisible()
            def select(index):
                QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
                app.processEvents()
                assert tabs.currentIndex() == index
            for width in (1666, 1050, 1666):
                viewer.resize(width, 1131)
                app.processEvents()
                height = console.height()
                select(1)
                assert dialog.isVisible() and not console.body_scroll.isVisible()
                assert tabs.rect().contains(tabs.tabRect(1))
                for control in (dialog.create, dialog.code, dialog.join):
                    assert control.isVisible() and dialog.rect().contains(control.geometry())
                assert console.height() == height
                select(0)
                assert console.body_scroll.isVisible() and not dialog.isVisible()
            select(1)
            assert dialog.mode.currentData() == "local" and dialog.mode.itemData(1) == "cloud"
            if output:
                capture("entry.png")
            dialog.code.setText("123")
            dialog.join.click()
            assert "六位" in dialog.status.text()
            dialog.create.click()
            assert "先开始播放" in dialog.status.text()
            package = sys.modules[dialog.__class__.__module__]
            from threading import RLock
            sample = SimpleNamespace(time=100., texture=9, signature=42)
            history = SimpleNamespace(origin=1, lock=RLock(), details=[sample],
                                      bounds_at=lambda _: (90, 101), frame_at=lambda stamp:(stamp, None))
            row = SimpleNamespace(decoder=SimpleNamespace(history=history,crop_generation=0), paused=False)
            flushes = []
            fake = SimpleNamespace(rows={"one":row}, alignment=SimpleNamespace(reference="one"),
                running=True, replay=SimpleNamespace(active=False), sync_waiting=False, window=lambda:window,
                canvas=SimpleNamespace(isVisible=lambda:True, native_presenting=False, painted_stamp=100., frame_key=100.),
                live_clock=lambda:99., rendered_shifts={"one":1.}, audio=SimpleNamespace(correction=0.,flush=flushes.append))
            assert package.frame_observation(fake)[2] is sample
            assert not package.delay_playback(fake, 100., 12)
            assert package.delay_playback(fake, 100., .9) and abs(fake.audio.correction + .9) < 1e-8
            assert flushes == [98.1]
            fake.canvas.isVisible = lambda: False
            fake.canvas.frame_key = None
            assert package.frame_observation(fake) is None
            assert package.frame_observation(fake, playback_clock=True)[2] is sample
            assert package.delay_playback(fake, 100., .4, playback_clock=True)
            from collections import deque
            history.sequence, history.received_frames = 20, 10
            history.bounds = lambda: (90., 101.)
            decoder = row.decoder
            decoder.received_cadence = SimpleNamespace(stats=lambda *args: {"gap_max_ms":22})
            decoder.fps = 60
            fake.d3d11_picture = None
            fake.canvas.presented, fake.canvas.painted_frames, fake.canvas.watch_paint_gap = 7, 5, .06
            fake.playback_rate = 1.01
            fake.audio.clock_observation = (time.perf_counter(), 10, 90, 0., [(0,90,0.,1.009)])
            monitor = SimpleNamespace(viewer=fake, link=SimpleNamespace(commands=deque()), last_tick=0.,
                                      diag_due=0., previous_diag=None, gui_gap=0.)
            package.WatchRoom.diagnose(monitor, decoder, 10., False)
            history.sequence += 119
            history.received_frames += 118
            fake.canvas.presented += 100
            fake.canvas.painted_frames += 88
            fake.canvas.watch_paint_gap = .08
            fake.live_clock = lambda: 101.02
            package.WatchRoom.diagnose(monitor, decoder, 12., False)
            diagnostic, = monitor.link.commands
            assert diagnostic["device_rate_ppm"] == 1009000 and diagnostic["rate_ppm"] == 1010000
            assert diagnostic["progress_rate_ppm"] == 1010000
            progress = package.LockedProgress()
            for i in range(1000):
                assert not progress.recovered(i/10, i/10+50)
            # Freeze, then wait for one second of continuous progress.
            for i in range(101):
                assert not progress.recovered(100+i/10, None)
            assert not progress.recovered(111., 151.)
            assert not progress.recovered(111.8, 151.8)
            assert progress.recovered(112., 152.)
            assert not progress.recovered(113., 153.)
            # Accumulated drift at non-unit rate: don't request a check until normal speed resumes.
            for i in range(400):
                assert not progress.recovered(113+i/10, 153+i/10*1.01, False)
            assert not progress.recovered(154.,195.)
            assert progress.recovered(155.,196.)
            assert diagnostic["decoded"] == 118 and diagnostic["presented"] == 88
            assert diagnostic["paint_gap_ms"] == 80 and diagnostic["duration_ms"] == 2000
            from unittest.mock import patch
            # A locked GUI keeps its local clock monitor and never visits the image fingerprint path.
            fake.canvas.isVisible = lambda: True
            fake.window = viewer.window
            fake.playback_rate = 1.
            fake.audio.clock_observation = None
            monitor.link.online = monitor.link.locked = True
            monitor.profile_due = float("inf")
            monitor.source = (decoder, history, 0)
            monitor.progress = package.LockedProgress()
            monitor.diagnose = lambda *_: None
            with patch.object(package, "frame_observation", side_effect=AssertionError("locked fingerprint")):
                package.WatchRoom.sample(monitor)
            viewer.running = True
            try:
                with patch.object(package, "RoomLink") as constructor:
                    account = AccountRow(window)
                    avatar = QPixmap(60, 60)
                    avatar.fill(QColor("#3760a8"))
                    account.set_account("岸边的猫", avatar, platform="bilibili", uid="123-test")
                    window.sidebar = SimpleNamespace(account_row=account)
                    dialog.create.click()
                    session = constructor.return_value
                    session.mode = "local"
                    assert constructor.call_args.kwargs["mode"] == "local"
                    assert dialog.link is session and dialog.timer.isActive() and dialog.isVisible()
                    session.online = False
                    assert not dialog.control_request({"command":"recheck"})["requested"]
                    session.commands.append.assert_not_called()
                    session.online, session.locked, session.code = True, True, "123456"
                    dialog.handle(session, {"type":"seat","code":session.code,"side":0})
                    assert {key: session.profile[key] for key in ("platform", "id", "nickname")} == {
                        "platform":"bilibili", "id":"123-test", "nickname":"岸边的猫"}
                    assert session.profile["avatar"]
                    members = [dict(side=0, profile=session.profile), dict(side=1, profile=None),
                               dict(side=2, profile={"platform":"youtube", "id":"<test>", "avatar":"bad="})]
                    dialog.handle(session, dict(type="state", both=False, members=members[:1]))
                    assert dialog.status.text() == "等好友加入"
                    if output:
                        capture("waiting.png")
                    dialog.handle(session, dict(type="state", both=True, members=members))
                    assert dialog.room_number.text() == session.code and "3/3" in dialog.online_count.text()
                    assert dialog.room_mode.text() == "本机房间" and not tabs.tabIcon(1).isNull()
                    rows = [dialog.roster.itemAt(index).widget() for index in range(dialog.roster.count())]
                    names = [row.findChild(QLabel, "WatchMemberName") for row in rows]
                    assert [name.accessibleName() for name in names] == ["岸边的猫", "访客", "<test>"]
                    assert all(name.textFormat() == Qt.PlainText for name in names)
                    portraits = [row.findChild(Avatar) for row in rows]
                    assert not portraits[0].pixmap().isNull()
                    assert portraits[1].text() == "访" and portraits[2].text() == "<"
                    assert not dialog.manual.isEnabled()
                    from unittest.mock import Mock
                    clipboard = Mock()
                    with patch.object(QApplication, "clipboard", return_value=clipboard):
                        dialog.copy.click()
                    clipboard.setText.assert_called_once_with(session.code)
                    assert dialog.copy_feedback.isVisible()
                    for width in (1050, 1666):
                        viewer.resize(width, 1131)
                        app.processEvents()
                        for control in (dialog.member_list, dialog.room_number, dialog.copy, dialog.manual, dialog.exit_room):
                            assert control.isVisible() and dialog.rect().contains(control.geometry()), control
                        height = console.height()
                        select(0)
                        select(1)
                        assert console.height() == height and "3/3" in dialog.online_count.text()
                        assert dialog.parentWidget().width() >= dialog.width()
                    dialog.source = object()
                    session.offset = 0
                    dialog.handle(session, dict(type="measurement", lag=900, spread=900, samples=3, p95=900,
                                                rtt=40, delays={"0":900,"1":880}, at=100000))
                    assert dialog.pending[2] == .9
                    dialog.handle(session, dict(type="members", members=members))
                    assert dialog.pending
                    dialog.handle(session, dict(type="state", both=True, members=members[:2]))
                    assert "2/3" in dialog.online_count.text() and dialog.pending is None
                    dialog.handle(session, dict(type="measurement", lag=16, leading=1, locked=True, p95=30, rtt=375))
                    assert dialog.status.text() == "画面已锁定" and "访客的画面领先 16 ms" in dialog.status_detail.text()
                    assert dialog.details_toggle.isVisible() and not dialog.details.isVisible()
                    footer = dialog.room_actions.geometry()
                    dialog.details_toggle.click()
                    assert dialog.details.isVisible() and [x.text() for x in dialog.metrics] == ["16 ms", "30 ms", "375 ms"]
                    app.processEvents()
                    assert dialog.room_actions.geometry() == footer
                    assert dialog.height() - footer.y() - footer.height() == dialog.layout().contentsMargins().bottom()
                    assert dialog.content_scroll.geometry().bottom() < footer.top()
                    original_height = dialog.height()
                    dialog.setFixedHeight(128)
                    app.processEvents()
                    bar = dialog.content_scroll.verticalScrollBar()
                    assert bar.maximum() > 0
                    footer = dialog.room_actions.geometry()
                    bar.setValue(bar.maximum())
                    app.processEvents()
                    assert dialog.room_actions.geometry() == footer and dialog.rect().contains(footer)
                    assert dialog.manual.isVisible() and dialog.exit_room.isVisible()
                    dialog.setMinimumHeight(0)
                    dialog.setMaximumHeight(16777215)
                    dialog.resize(dialog.width(), original_height)
                    bar.setValue(0)
                    app.processEvents()
                    if output:
                        capture("locked.png")
                    dialog.handle(session, dict(type="network", text="连接中断"))
                    assert dialog.status.text() == "正在重新连接" and not dialog.copy.isEnabled()
                    assert not dialog.details.isVisible() and not dialog.manual.isEnabled()
                    dialog.handle(session, dict(type="state", both=True, members=members[:2]))
                    assert dialog.copy.isEnabled()
                    window.sidebar.account_row.uid = ""
                    dialog.profile_due = 0
                    dialog.sample()
                    assert session.profile is None
                    select(0)
                    assert not dialog.isVisible() and dialog.timer.isActive()
                    if monitor_python:
                        with subprocess.Popen([str(monitor_python), str(repo/"tests/check_match_monitor.py"),
                                               "--host", str(host_root), "--pid", str(os.getpid()),
                                               "--check-watch-party"]) as child:
                            deadline = time.monotonic() + 30
                            while child.poll() is None and time.monotonic() < deadline:
                                app.processEvents()
                                time.sleep(.01)
                            if child.poll() is None:
                                child.kill()
                                raise AssertionError("MCP fixture timed out")
                            assert child.returncode == 0
                        session.commands.append.assert_called_once_with({"type":"manual"})
                    console.minimize.click()
                    assert console.collapsed and dialog.link is session
                    console.restore.click()
                    select(1)
                    assert dialog.isVisible() and dialog.link is session
                    dialog.handle(session, dict(type="measurement", lag=16, leading=1, locked=True, p95=30, rtt=375))
                    dialog.manual.click()
                    session.commands.append.assert_called_with({"type":"manual"})
                    viewer.showMinimized()
                    app.processEvents()
                    assert dialog.timer.isActive() and dialog.link is session
                    viewer.showNormal()
                    app.processEvents()
                    dialog.exit_room.click()
                    assert dialog.create.isVisible() and not dialog.room_number.isVisible()
                    assert not dialog.timer.isActive() and dialog.link is None
                    assert not dialog.local_control.isListening()
            finally:
                viewer.running = False
            if args.endpoint:
                from watch_party_group import check_group
                link_module = sys.modules[package.RoomLink.__base__.__module__]
                protocol(link_module, args.endpoint, diagnostic)
                check_group(link_module, args.endpoint)
        finally:
            viewer.close()
            app.processEvents()
            manager.unload()
    print("PASS: plugin entry, background session, embedded console tabs, clock fallback and per-device diagnostics")


if __name__ == "__main__":
    main()
