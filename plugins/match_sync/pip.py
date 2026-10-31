"""Geometry for the picture-in-picture inset drawn over the match picture.

The inset shows one room's crop rectangle (a commentator's face, for example)
on top of the main picture and can be dragged or resized with the mouse.
Everything here is plain arithmetic so placement and dragging can be checked
without Qt or a display; rects travel as (x, y, width, height) tuples.
"""
from __future__ import annotations

import math


CORNERS = ("right-bottom", "left-bottom", "right-top", "left-top")
LABELS = {
    "right-bottom": "右下",
    "left-bottom": "左下",
    "right-top": "右上",
    "left-top": "左上",
}
MIN_WIDTH = 0.10
MAX_WIDTH = 0.45
DEFAULT_WIDTH = 0.28
MIN_SIZE = 0.02
MIN_BOX = 0.06
MAX_BOX = 0.95
HANDLE = 18.0
FULL = (0.0, 0.0, 1.0, 1.0)


def _quad(rect):
    """Read a rect from a tuple, a list, or a Qt-like rect offering getRect().

    QRectF is not iterable, so a generator over it raises TypeError; that used
    to make every crop fall back to the full frame.
    """
    if rect is None:
        return None
    get_rect = getattr(rect, "getRect", None)
    if get_rect is not None:
        try:
            rect = get_rect()
        except TypeError:
            return None
    try:
        values = tuple(float(value) for value in rect)
    except (TypeError, ValueError):
        return None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    return values


def clamp_crop(rect, minimum=MIN_SIZE):
    """Return a usable normalized (x, y, width, height) crop rectangle."""
    values = _quad(rect)
    if values is None:
        return FULL
    x, y, width, height = values
    if width <= 0.0 or height <= 0.0:
        return FULL
    x = min(max(0.0, x), 1.0 - minimum)
    y = min(max(0.0, y), 1.0 - minimum)
    width = min(1.0 - x, max(minimum, width))
    height = min(1.0 - y, max(minimum, height))
    return (x, y, width, height)


def crop_pixels(rect, width, height):
    """Pixel (x, y, width, height) of a normalized crop inside a frame."""
    x, y, crop_width, crop_height = clamp_crop(rect)
    width = max(1, int(width))
    height = max(1, int(height))
    left = min(width - 1, int(round(x * width)))
    top = min(height - 1, int(round(y * height)))
    right = min(width, max(left + 1, int(round((x + crop_width) * width))))
    bottom = min(height, max(top + 1, int(round((y + crop_height) * height))))
    return (left, top, right - left, bottom - top)


def inset(area, aspect, width_ratio, corner="right-bottom", margin=0.0):
    """Place the inset inside ``area`` = (x, y, width, height).

    ``aspect`` is the crop's width / height and ``width_ratio`` the inset width
    as a fraction of the area width.  Returns (x, y, width, height) or None when
    the area is too small to hold anything.
    """
    try:
        area_x, area_y, area_width, area_height = (float(value) for value in area)
        aspect = float(aspect)
        width_ratio = float(width_ratio)
        margin = max(0.0, float(margin))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (area_x, area_y, area_width, area_height,
                                                  aspect, width_ratio, margin)):
        return None
    if area_width <= 0 or area_height <= 0 or aspect <= 0:
        return None
    free_width = area_width - 2 * margin
    free_height = area_height - 2 * margin
    if free_width <= 1 or free_height <= 1:
        return None
    width = free_width * min(MAX_WIDTH, max(MIN_WIDTH, width_ratio))
    height = width / aspect
    if height > free_height:
        height = free_height
        width = height * aspect
    if width > free_width:
        width = free_width
        height = width / aspect
    if corner not in CORNERS:
        corner = "right-bottom"
    left = area_x + margin + (free_width - width if corner.startswith("right") else 0.0)
    top = area_y + margin + (0.0 if corner.endswith("top") else free_height - height)
    return (left, top, width, height)


def to_normalized(rect, area):
    """Convert a pixel rect inside ``area`` to (0..1) coordinates."""
    values = _quad(rect)
    bounds = _quad(area)
    if values is None or bounds is None:
        return None
    area_x, area_y, area_width, area_height = bounds
    if area_width <= 0 or area_height <= 0:
        return None
    x, y, width, height = values
    return ((x - area_x) / area_width, (y - area_y) / area_height,
            width / area_width, height / area_height)


def from_normalized(rect, area):
    """Convert a (0..1) rect back to pixels inside ``area``."""
    values = _quad(rect)
    bounds = _quad(area)
    if values is None or bounds is None:
        return None
    area_x, area_y, area_width, area_height = bounds
    if area_width <= 0 or area_height <= 0:
        return None
    x, y, width, height = values
    return (area_x + x * area_width, area_y + y * area_height,
            width * area_width, height * area_height)


def clamp_box(rect, minimum=MIN_BOX):
    """Fit a saved normalized inset rect back into the picture, or None."""
    values = _quad(rect)
    if values is None:
        return None
    x, y, width, height = values
    if width <= 0.0 or height <= 0.0:
        return None
    width = min(MAX_BOX, max(minimum, width))
    height = min(MAX_BOX, max(minimum, height))
    x = min(max(0.0, x), 1.0 - width)
    y = min(max(0.0, y), 1.0 - height)
    return (x, y, width, height)


def fit(rect, area, minimum=MIN_BOX):
    """Clamp a pixel rect fully inside ``area`` and keep a usable size."""
    values = _quad(rect)
    bounds = _quad(area)
    if values is None or bounds is None:
        return None
    area_x, area_y, area_width, area_height = bounds
    if area_width <= 2 or area_height <= 2:
        return None
    x, y, width, height = values
    width = min(area_width, max(area_width * minimum, width))
    height = min(area_height, max(area_height * minimum, height))
    x = min(max(area_x, x), area_x + area_width - width)
    y = min(max(area_y, y), area_y + area_height - height)
    return (x, y, width, height)


def resize_keeping_aspect(rect, width, aspect, area, minimum=MIN_BOX):
    """Resize from the top-left corner, keeping width / height == aspect."""
    values = _quad(rect)
    bounds = _quad(area)
    try:
        aspect = float(aspect)
        width = float(width)
    except (TypeError, ValueError):
        return None
    if values is None or bounds is None or aspect <= 0 or not math.isfinite(aspect):
        return None
    area_x, area_y, area_width, area_height = bounds
    x, y, _old_width, _old_height = values
    room_width = max(0.0, area_x + area_width - x)
    room_height = max(0.0, area_y + area_height - y)
    biggest = min(room_width, room_height * aspect)
    if biggest <= 0.0:
        return None
    smallest = min(area_width * minimum, biggest)
    width = min(max(width, smallest), biggest)
    if width <= 0.0 or not math.isfinite(width):
        return None
    return (x, y, width, width / aspect)
