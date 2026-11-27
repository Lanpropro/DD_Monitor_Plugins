"""Exercise membership changes and group calibration using independent plugin connections."""
import queue
import time
from random import Random


def check_group(link_module, endpoint):
    from live_match import LiveMatch
    from server import public_profile

    for invalid in ({"platform": "test", "id": "42", "extra": "x"}, {"platform": "test", "id": "\nx"}):
        try:
            public_profile(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid public profile accepted")

    match = LiveMatch(active=(1, 2))
    for i in range(3):
        plan = match.decide((0, -800), i * 2000)
    assert plan["delays"] == {"2": 800}
    match.active = (0, 1, 2)
    match.reset(0)
    for i in range(3):
        plan = match.decide((0, 900, 940), i * 2000)
    assert plan["delays"] == {"0": 940}

    endpoint = link_module.ENDPOINT if endpoint == "packaged" else endpoint
    queues, events, links = [], [], []
    signatures = [Random(i).getrandbits(512) for i in range(1800)]
    start = time.monotonic()
    delays = [0, 0, 10]
    pending = {}
    apply_receipts = {}
    common = False

    def connect(code, profile=None, token=None):
        inbox = queue.SimpleQueue()
        link = link_module.RoomLink(code, inbox.put, endpoint)
        queues.append(inbox)
        events.append([])
        links.append(link)
        link.profile, link.token = profile, token
        link.worker.start()
        return link

    def drive(predicate, label, timeout=45):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            now = time.monotonic()
            for i, (link, inbox) in enumerate(zip(links, queues)):
                while not inbox.empty():
                    msg = inbox.get()
                    events[i].append(msg)
                    if i < 3 and "delays" in msg and str(i) in msg["delays"]:
                        pending[i] = ((msg["at"] - link.offset) / 1000, msg["delays"][str(i)], msg["at"])
                if i >= 3:
                    continue
                if i in pending and now >= pending[i][0]:
                    _, delay, at = pending.pop(i)
                    delays[i] += round(delay / 90)
                    apply_receipts[i] = at
                    link.commands.append(dict(type="applied", at=at, ok=1))
                tick = int((now - start) / .09) + 20 - delays[i]
                signature = signatures[tick % len(signatures)]
                if i == 2 and not common:
                    signature ^= (1 << 512) - 1
                link.points.append((now, signature, "screen"))
            if predicate():
                return
            time.sleep(.045)
        raise AssertionError((label, [[(m["type"], m.get("lag"), m.get("reason")) for m in log[-4:]]
                                     for log in events]))

    def roster(index):
        return next((m["members"] for m in reversed(events[index]) if "members" in m), [])

    try:
        host = connect(None, {"platform":"bilibili","id":"test-123"})
        drive(lambda: len(roster(0)) == 1, "host alone")
        assert roster(0)[0]["profile"] == host.profile
        peer = connect(host.code)
        drive(lambda: host.locked and peer.locked, "first two locked")
        assert roster(0)[1]["profile"] is None
        peer.profile = {"platform":"youtube","id":"@test-id"}
        drive(lambda: roster(0)[1]["profile"] == peer.profile, "profile update")
        assert host.locked and peer.locked
        third = connect(host.code)
        drive(lambda: all(len(roster(i)) == 3 for i in range(3)), "third joined")
        assert all(not link.locked for link in links)
        no_match_since = time.monotonic()
        drive(lambda: time.monotonic() - no_match_since > 6, "different pictures")
        assert all(not link.locked for link in links)
        common = True
        drive(lambda: all(link.locked for link in links), "three verified after correction", 85)
        assert set(apply_receipts) == {0, 1} and len(set(apply_receipts.values())) == 1
        for log in events:
            plan, = [m for m in log if "delays" in m]
            assert all(abs(delay - 900) <= 200 for delay in plan["delays"].values())
        fourth = connect(host.code)
        drive(lambda: any(m.get("reason") == "room_full" for m in events[3]), "fourth refused")
        assert all(link.locked for link in links[:3])
        third.commands.append({"type":"manual"})
        drive(lambda: all(any(m.get("manual") for m in log) for log in events[:3]), "manual group recheck")
        third.stopped.set()
        third.worker.join(6)
        drive(lambda: len(roster(0)) == len(roster(1)) == 2, "leave")
        resumed = connect(host.code, token=third.token)
        drive(lambda: len(roster(0)) == 3 and len(roster(4)) == 3, "resume")
        assert any(m.get("type") == "seat" and m["side"] == 2 for m in events[4])
        host.stopped.set()
        host.worker.join(6)
        drive(lambda: [m["side"] for m in roster(1)] == [1, 2] and resumed.online, "host offline")
    finally:
        for link in links:
            link.stopped.set()
        for link in links:
            link.worker.join(10)
            assert not link.worker.is_alive()
    print("PASS: three clients, public profiles, shared correction, fresh lock, full room, leave and resume")
