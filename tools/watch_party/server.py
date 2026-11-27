"""Run the standalone web demo and plugin room server."""
import argparse
from aiohttp import web
import sys
from pathlib import Path

plugin = Path(__file__).resolve().parents[2] / "plugins" / "match_sync"
sys.path.insert(0, str(plugin))
from watch_room_server import make_app as room_app, public_profile


def make_app(observations=None):
    return room_app(observations, page=Path(__file__).with_name("index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18764)
    parser.add_argument("--observations", type=argparse.FileType("a", encoding="utf-8"))
    args = parser.parse_args()
    web.run_app(make_app(args.observations), host="127.0.0.1", port=args.port, access_log=None)
