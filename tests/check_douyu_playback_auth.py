"""Offline Douyu playback login, native source quality and Cookie domain checks."""
import argparse
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ["DDM_NO_SAVE"] = "1"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.host.resolve()))
    from ddm import plugins
    from ddm.account_store import AccountStore
    manager = plugins.PluginManager(plugins_dir=str(ROOT / "plugins"), enabled=["domestic_live"])
    manager.load()
    platform = manager.platforms["douyu"]
    module = sys.modules[type(platform).__module__]
    saved = ["auth=fixture; Domain=.douyu.com; Path=/; Secure; HttpOnly",
             "expired=old; Domain=.douyu.com; Expires=Sat, 01 Jan 2000 00:00:00 GMT",
             "foreign=secret; Domain=.example.com; Path=/",
             "hostonly=secret; Path=/"]
    with patch.object(AccountStore, "load", return_value=saved) as load:
        assert not platform._playback_cookies()
        load.assert_not_called()
        with patch.dict(os.environ, {"DDM_NO_SAVE": "0"}):
            cookies = platform._playback_cookies()
        load.assert_called_once_with()
    assert cookies.get_dict() == {"auth": "fixture"}
    source = {"rtmp_url": "https://cdn.test", "rtmp_live": "source.flv", "rate": 4,
              "multirates": [{"rate": 0, "bit": 16000, "name": "原画1080P60"},
                             {"rate": 4, "bit": 4000, "name": "蓝光4M"}]}
    session = module.Streamlink()

    def resolve(_parser, _room, cdn="", *, rate=0):
        assert session.http.cookies.get("auth") == "fixture"
        return dict(source, rate=4 if request.call_count == 1 else rate)

    try:
        with patch.object(platform, "_playback_cookies", return_value=cookies), \
                patch.object(platform, "room_info", return_value=plugins.RoomInfo("douyu:123", live=True)), \
                patch.object(platform, "_request_source", side_effect=resolve) as request:
            stream = platform._streams(session, "douyu:123", 10000)["source"]
            assert request.call_count == 2 and request.call_args.kwargs["rate"] == 0
            assert stream.ddm_quality == 10000, "Highest quality must explicitly request native rate 0"
        with patch.object(platform, "room_info", return_value=plugins.RoomInfo("douyu:123", live=True)), \
                patch.object(platform, "_request_source", side_effect=[source, {}]):
            try:
                platform._streams(session, "douyu:123", 10000)
            except RuntimeError as error:
                assert "所选画质" in str(error)
            else:
                raise AssertionError("Failed source selection must not silently reuse default 4M")
        with patch.object(module, "Streamlink", return_value=session), \
                patch.object(platform, "_streams", return_value={"source": stream}):
            _url, quality, _profile, headers = platform.play_url("douyu:123", 10000)
            assert quality == 10000 and set(headers) == {"User-Agent", "Referer"}
            assert "fixture" not in str(headers), "Account cookies must stay in the official API session"
    finally:
        session.http.close()
        manager.unload()
    print("PASS: scoped saved Douyu login, original quality request, explicit failure and no CDN Cookie leak")


if __name__ == "__main__":
    main()
