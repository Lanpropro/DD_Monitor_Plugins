"""Compatibility exports for tile VOD controls and media helpers.

Implementation is organized by protocol, native surface, progress and status.
"""
from .vod_headers import DEFAULT_USER_AGENT, DEFAULT_HEADERS, _WEBPAGE_MARKERS, is_direct_url, merge_headers
from .vod_surface import POSITION_BADGE_HEIGHT, POSITION_BADGE_MIN_WIDTH, POSITION_BADGE_LEFT, POSITION_BADGE_TOP, POSITION_BADGE_TEXT_LEFT, POSITION_BADGE_TEXT_RIGHT, _SCREEN_CHANGE, video_geometry, _push_layered_alpha, VodPositionBadge
from .vod_progress import STRIP_HEIGHT, BAR_HEIGHT, LINE_HEIGHT, _CLOCK_SAMPLE, format_clock, VodProgressStrip, VodBarProgress, VodProgressLine
from .vod_status import _ACCENT, _PILL_BG, _PILL_TEXT, _PILL_QSS, _status_action_icon, VodStatusCard, VodSubtitleButton
