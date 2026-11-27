"""Expose the shared room matcher to the standalone checks."""
import sys
from pathlib import Path

plugin = Path(__file__).resolve().parents[2] / "plugins" / "match_sync"
sys.path.insert(0, str(plugin))
from watch_room_match import LiveMatch, picture_delta
