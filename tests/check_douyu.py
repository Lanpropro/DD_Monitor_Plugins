"""离线回归：斗鱼靓号解析、实际房间取流和失败边界。"""
import argparse
import os
from pathlib import Path
import sys
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def response(data=None, text="", *, invalid_json=False):
    result = Mock(text=text)
    result.json.return_value = data
    if invalid_json:
        result.json.side_effect = ValueError("HTML instead of JSON")
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    os.environ["DDM_NO_SAVE"] = "1"
    from ddm.plugins import PluginManager, RoomInfo
    manager = PluginManager(plugins_dir=str(ROOT / "plugins"), enabled=["domestic_live"])
    manager.load()
    platform = manager.platforms["douyu"]
    module = sys.modules[type(platform).__module__]
    room = {"room_id": 6979222, "show_status": 1, "videoLoop": 0,
            "nickname": "Machine", "room_name": "6657",
            "owner_avatar": "//apic.douyucdn.cn/avatar.jpg",
            "room_pic": "https://rpic.douyucdn.cn/cover.jpg"}
    with patch.object(module.requests, "get", side_effect=[response(invalid_json=True),
            response(text="<script>window.room_id = 6979222;</script>"),
            response({"room": room})]) as get:
        info = platform.room_info("https://www.douyu.com/6657")
        assert info.room_id == "douyu:6979222" and info.live
        assert info.face and info.cover_url and info.viewers == ""
        assert [call.args[0] for call in get.call_args_list] == [
            "https://www.douyu.com/betard/6657", "https://www.douyu.com/6657",
            "https://www.douyu.com/betard/6979222"]
        assert all(call.kwargs["timeout"] == (4, 8) for call in get.call_args_list)
    with patch.object(module.requests, "get", return_value=response({"room": room})) as get:
        assert platform.room_info("douyu:6979222").room_id == "douyu:6979222"
        assert get.call_count == 1
    for page in ("captcha", "window.room_id = 6657;", "window.room_id = 0;",
                 "window.room_id = '6979222';"):
        with patch.object(module.requests, "get", side_effect=[response(invalid_json=True),
                response(text=page)]) as get:
            try:
                platform.room_info("douyu:6657")
            except RuntimeError:
                pass
            else:
                raise AssertionError("Invalid page cannot create a room")
            assert get.call_count == 2
    with patch.object(module.requests, "get", side_effect=module.requests.Timeout) as get:
        try:
            platform.room_info("douyu:6657")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Timeout cannot be treated as offline")
        assert get.call_count == 1
    session = module.Streamlink()
    try:
        with patch.object(platform, "room_info", return_value=RoomInfo("douyu:6979222", live=True)), \
                patch.object(module, "Douyu") as douyu, \
                patch.object(platform, "_request_source", return_value={
                    "rtmp_url": "https://cdn.test", "rtmp_live": "source.flv", "rate": 0}) as source:
            assert platform._streams(session, "douyu:6657")["source"].ddm_quality == 10000
            douyu.assert_called_once_with(session, "https://www.douyu.com/6979222")
            assert source.call_args.args[1] == "6979222"
    finally:
        session.http.close()
        manager.unload()
    print("PASS: Douyu vanity ID, actual room metadata/stream, bounded fallback and network errors")


if __name__ == "__main__":
    main()
