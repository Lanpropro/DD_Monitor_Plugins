"""离线回归：官方网页关注分页、离线主播、去重及取消。"""
import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def response(data):
    result = Mock()
    result.json.return_value = data
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    os.environ["DDM_NO_SAVE"] = "1"
    from ddm.plugins import PluginManager
    manager = PluginManager(plugins_dir=str(ROOT / "plugins"), enabled=["domestic_live"])
    manager.load()
    platform = manager.platforms["douyu"]
    assert platform.follow_cookie_domain == "douyu.com"
    assert platform.follow_login_url.startswith("https://www.douyu.com/")
    assert not manager.platforms["huya"].follow_login_url and not manager.platforms["douyin"].follow_login_url
    live = {"room_id": 6979222, "nickname": "Machine", "room_name": "live", "show_status": "1",
            "avatar_small": "//apic.douyucdn.cn/avatar.jpg", "room_src": "https://rpic.douyucdn.cn/cover.jpg",
            "online": 99999}
    offline = dict(live, room_id=123, nickname="offline", show_status=2)
    session = Mock()
    session.get.side_effect = [response({"error": 0, "data": json.dumps({"nowPage": 1,
        "pageCount": 2, "list": [live, {"video_id": "recommendation"}]})}),
        response({"error": "0", "data": {"nowPage": 2, "pageCount": 2, "list": [live, offline]}})]
    rooms = platform.follow_rooms(session, lambda: False)
    assert len(rooms) == 2 and rooms[0]["room_id"] == "douyu:6979222"
    assert rooms[0]["live"] and not rooms[1]["live"] and rooms[0]["face"] and rooms[0]["cover_url"]
    assert all(room["viewers"] == "" and "online" not in room for room in rooms)
    assert [call.kwargs["params"]["page"] for call in session.get.call_args_list] == [1, 2]
    assert all(call.kwargs["timeout"] == (4, 8) for call in session.get.call_args_list)
    assert all(call.args[0] == "https://www.douyu.com/wgapi/livenc/liveweb/follow/list"
               for call in session.get.call_args_list)
    for data in ({"error": -1}, {"error": 0, "data": "invalid JSON"},
                 {"error": 0, "data": {"list": []}},
                 {"error": 0, "data": {"list": [], "pageCount": 101}}):
        session = Mock(get=Mock(return_value=response(data)))
        try:
            platform.follow_rooms(session, lambda: False)
        except (RuntimeError, ValueError):
            pass
        else:
            raise AssertionError("Authentication or malformed response must not succeed")
        assert session.get.call_count == 1
    session = Mock(get=Mock(return_value=response({"error": 0, "data": {"list": [], "pageCount": 0}})))
    assert platform.follow_rooms(session, lambda: False) == []
    session.get.reset_mock()
    assert platform.follow_rooms(session, lambda: True) == [] and not session.get.called
    session.get.return_value = response({"error": 0, "data": {"list": [live], "pageCount": 2}})
    assert platform.follow_rooms(session, lambda: session.get.call_count > 0) == []
    assert session.get.call_count == 1
    manager.unload()
    print("PASS: paginated follows, offline rooms, canonical IDs, dedup, images, no counts, errors/cancellation")


if __name__ == "__main__":
    main()
