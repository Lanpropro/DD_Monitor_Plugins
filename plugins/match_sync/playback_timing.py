"""Playback deadlines and retained-history status descriptions."""
from __future__ import annotations

from math import ceil


def picture_poll_interval(target, frame_stamp, fps, rate=1., elapsed=0.):
    fps = max(1., fps)
    normal = max(4, int(500 / fps))
    if frame_stamp is None:
        return normal
    remaining = (frame_stamp + 1 / fps - target) / rate - elapsed
    return max(1, min(normal, ceil(remaining * 1000)))


def cache_gap(history, target):
    """Describe missing target content, separately from scene-match confidence."""
    bounds = history.bounds()
    if bounds is None:
        return "尚未收到播放缓存"
    previous = getattr(history, 'previous', None)
    previous_bounds = previous.bounds() if previous is not None else None
    earliest = min(bounds[0], previous_bounds[0]) if previous_bounds else bounds[0]
    latest = max(bounds[1], previous_bounds[1]) if previous_bounds else bounds[1]
    if target < earliest:
        return f"目标早于已保留缓存约 {earliest - target:.1f} 秒"
    if target > latest:
        return f"目标内容尚未收到，缺少约 {target - latest:.1f} 秒"
    continuous = history.bounds_at(target)
    if continuous is None or not continuous[0] <= target <= continuous[1]:
        return "目标位置存在断流缓存缺口"
    return ""
