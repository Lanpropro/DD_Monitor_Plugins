"""Find the webcam inset inside a secondary stream frame.

The detector never touches the decoder: it takes the small grayscale
thumbnail the plugin already keeps and returns a normalized crop that follows
the webcam window.  Pure ``numpy`` on purpose, so it ships with unit tests and
the optional OpenCV face check simply disappears when the host lacks OpenCV.

The window is found from its four straight borders: every column/row keeps its
longest run of edge samples, columns with the same run are collapsed into one
border, and two borders whose runs start and end together are the two sides of
a candidate box.  That pairing is what keeps a 1080p scoreboard or a timer
from being mistaken for a webcam window.
"""

from __future__ import annotations

import numpy as np

FULL = (0.0, 0.0, 1.0, 1.0)

MIN_WIDTH = 0.14
MAX_WIDTH = 0.62
MIN_HEIGHT = 0.12
MAX_HEIGHT = 0.72
MIN_ASPECT = 0.70
MAX_ASPECT = 2.80

EDGE = 12.0             # |difference| that counts as an edge sample
COVER = 0.55            # a border has to cover this much of the window side
SIDE_COVER = 0.50       # a side has to show an edge on this many samples
STABLE_BONUS = 1.0      # edges that survived from the previous frame count more
COLUMN_LIMIT = 18       # strongest vertical borders tried as candidates
ROW_LIMIT = 7           # strongest horizontal borders tried per width
SCORE_FLOOR = 42.0
MIN_BORDER = 18.0
NEST_RATIO = 0.55       # an enclosing box may score less, but not arbitrarily less
OUT_ASPECT = (1.15, 2.15)   # an enclosing camera window looks like 4:3 .. 16:9
OUT_GROWTH = 3.0        # never climb from a small inset to a huge frame
CONFIRM_COUNT = 2
MIN_INTERVAL = 1.5


class Line:
    """One straight border: where it sits and which span it covers."""

    __slots__ = ("pos", "start", "end", "strength", "weight")

    def __init__(self, pos: int, start: int, end: int, strength: float):
        self.pos = int(pos)
        self.start = int(start)
        self.end = int(end)
        self.strength = float(strength)
        self.weight = int(end - start)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Line({self.pos}, {self.start}-{self.end}, {self.strength:.0f})"


def _runs(strong: np.ndarray, magnitude: np.ndarray):
    """Longest consecutive run per line, as ``(length, start, strength)``."""
    samples, lines = strong.shape
    length = np.zeros(lines, dtype=np.int32)
    start_at = np.zeros(lines, dtype=np.int32)
    current = np.zeros(lines, dtype=np.int32)
    current_start = np.zeros(lines, dtype=np.int32)
    for index in range(samples):
        inside = strong[index]
        current = np.where(inside, current + 1, 0)
        current_start = np.where(inside & (current == 1), index, current_start)
        better = current > length
        length = np.where(better, current, length)
        start_at = np.where(better, current_start, start_at)
    prefix = np.cumsum(np.concatenate((np.zeros((1, lines)), magnitude), axis=0), axis=0)
    ends = start_at + length
    totals = prefix[ends, np.arange(lines)] - prefix[start_at, np.arange(lines)]
    strength = np.where(length > 0, totals / np.maximum(1, length), 0.0)
    return length, start_at, strength


def _borders(length, start_at, strength, minimum: int):
    """Collapse neighbouring lines that share a run into single borders."""
    borders: list[Line] = []
    index = 0
    lines = length.size
    while index < lines:
        if length[index] < minimum:
            index += 1
            continue
        end = index
        while end + 1 < lines and length[end + 1] >= minimum and abs(
                int(start_at[end + 1]) - int(start_at[index])) <= 3 and abs(
                int(length[end + 1]) - int(length[index])) <= 3:
            end += 1
        span = slice(index, end + 1)
        best = int(np.argmax(strength[span])) + index
        borders.append(Line((index + end) // 2, start_at[best],
                            start_at[best] + length[best], strength[best]))
        index = end + 1
    return borders


def _integral(values: np.ndarray) -> np.ndarray:
    padded = np.zeros((values.shape[0] + 1, values.shape[1] + 1), dtype=np.float64)
    padded[1:, 1:] = np.cumsum(np.cumsum(values, axis=0), axis=1)
    return padded


def _area_mean(integral: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> float:
    area = max(1, (x2 - x1) * (y2 - y1))
    return float(integral[y2, x2] - integral[y1, x2] - integral[y2, x1] + integral[y1, x1]) / area


def _iou(a, b) -> float:
    if a is None or b is None:
        return 0.0
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0.0
    overlap = (right - left) * (bottom - top)
    return overlap / (aw * ah + bw * bh - overlap)


def _face_boxes(gray: np.ndarray):
    """Largest OpenCV face boxes, in pixels; empty without OpenCV."""
    try:
        import cv2
    except Exception:
        return []
    try:
        cascade = _cascade()
        if cascade is None:
            return []
        height, width = gray.shape
        scale = 160.0 / max(height, width)
        small = gray if scale >= 0.99 else cv2.resize(
            gray, (max(24, int(width * scale)), max(24, int(height * scale))),
            interpolation=cv2.INTER_AREA)
        found = cascade.detectMultiScale(small, scaleFactor=1.2, minNeighbors=5, minSize=(24, 24))
        boxes = [(int(x / scale), int(y / scale), int(w / scale), int(h / scale))
                 for x, y, w, h in found]
        boxes.sort(key=lambda box: box[2] * box[3], reverse=True)
        return boxes[:3]
    except Exception:
        return []


_CASCADE = None


def _cascade():
    global _CASCADE
    if _CASCADE is None:
        try:
            import cv2
            _CASCADE = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            if _CASCADE.empty():
                _CASCADE = False
        except Exception:
            _CASCADE = False
    return None if _CASCADE is False else _CASCADE


def _inside(box, face) -> bool:
    centre_x = face[0] + face[2] / 2.0
    centre_y = face[1] + face[3] / 2.0
    return box[0] <= centre_x <= box[0] + box[2] and box[1] <= centre_y <= box[1] + box[3]


def _face_crop(gray: np.ndarray, face, previous=None):
    """A 16:9 box around a face, used when the camera window has no border."""
    height, width = gray.shape
    face_x, face_y, face_w, face_h = face
    centre_x, centre_y = face_x + face_w / 2.0, face_y + face_h / 2.0
    ratio = 4.2 if previous is None else max(1.2, previous[2] * width / max(1.0, face_w))
    box_w = min(width * MAX_WIDTH, max(width * MIN_WIDTH, face_w * ratio))
    box_h = box_w * 9.0 / 16.0
    if box_h > height * MAX_HEIGHT:
        box_h = height * MAX_HEIGHT
        box_w = box_h * 16.0 / 9.0
    x = min(max(centre_x - box_w / 2.0, 0.0), width - box_w)
    y = min(max(centre_y - box_h * 0.45, 0.0), height - box_h)
    return (x / width, y / height, box_w / width, box_h / height)


def analyse(gray: np.ndarray):
    array = np.asarray(gray, dtype=np.float32)
    if array.ndim == 3:
        array = array.mean(axis=2)
    if array.ndim != 2 or min(array.shape) < 24:
        return None
    height, width = array.shape
    dx = np.zeros_like(array)
    dy = np.zeros_like(array)
    dx[:, 1:] = np.abs(np.diff(array, axis=1))
    dy[1:, :] = np.abs(np.diff(array, axis=0))
    minimum = max(4, int(min(height, width) * 0.10))
    strong_x = dx > EDGE
    strong_y = dy > EDGE
    return {
        "shape": (height, width),
        "columns": _borders(*_runs(strong_x, dx), minimum),
        "rows": _borders(*_runs(strong_y.T, dy.T), minimum),
        "strong_x": strong_x,
        "strong_y": strong_y,
        "integral": _integral(0.5 * (dx + dy)),
        "array": array,
    }


def _span_mean(prefix: np.ndarray, low: float, high: float) -> float:
    a = int(np.clip(low, 0, prefix.size - 1))
    b = int(np.clip(round(high), 0, prefix.size))
    if b <= a + 1:
        return 0.0
    return float(prefix[b] - prefix[a]) / float(b - a)


def _peaks(cover: np.ndarray, strength: np.ndarray, limit: int):
    """Row indices that look like horizontal borders, strongest first."""
    limit = max(2, int(limit))
    candidates = np.where(cover >= SIDE_COVER * 0.7)[0]
    if candidates.size < 2:
        return []
    order = candidates[np.argsort(strength[candidates])[::-1]]
    chosen = []
    for index in order:
        if all(abs(int(index) - other) > 2 for other in chosen):
            chosen.append(int(index))
        if len(chosen) >= limit * 2:
            break
    return sorted(chosen)


def detect(gray: np.ndarray, previous=None, faces: bool = True, prior=None, frame=None):
    """Return ``(rect, score)`` for the best webcam window, or ``(None, 0.0)``.

    ``prior`` is the edge mask pair kept from the last accepted frame: the camera
    window keeps its border while gameplay and lower thirds move, so intersecting
    the two masks is what separates a webcam inset from game UI.
    """
    if frame is None:
        frame = analyse(gray)
    if frame is None:
        return None, 0.0
    height, width = frame["shape"]
    dx = np.zeros_like(frame["array"])
    dy = np.zeros_like(frame["array"])
    dx[:, 1:] = np.abs(np.diff(frame["array"], axis=1))
    dy[1:, :] = np.abs(np.diff(frame["array"], axis=0))
    strong_x, strong_y = frame["strong_x"], frame["strong_y"]
    if prior is not None:
        prior_x, prior_y = prior
        if prior_x.shape == strong_x.shape:
            stable_x = strong_x & prior_x
            stable_y = strong_y & prior_y
            strong_x = stable_x if stable_x.sum() > 40 else strong_x
            strong_y = stable_y if stable_y.sum() > 40 else strong_y
    cover_x = np.cumsum(strong_x, axis=0)
    cover_y = np.cumsum(strong_y, axis=1)
    strength_x = np.cumsum(dx, axis=0)
    strength_y = np.cumsum(dy, axis=1)
    integral = frame["integral"]
    minimum = max(4, int(min(height, width) * 0.10))
    found = _face_boxes(frame["array"]) if faces else []

    sides = frame["columns"]
    sides.sort(key=lambda line: line.strength * line.weight, reverse=True)
    sides = sides[:COLUMN_LIMIT]

    best = (None, 0.0)
    boxes = []
    for index, left in enumerate(sides):
        for right in sides[index + 1:]:
            wid = right.pos - left.pos
            if wid < width * MIN_WIDTH or wid > width * MAX_WIDTH:
                continue
            x1, x2 = left.pos, right.pos
            row_cover = (cover_y[:, x2] - cover_y[:, x1]) / float(x2 - x1)
            row_strength = (strength_y[:, x2] - strength_y[:, x1]) / float(x2 - x1)
            picks = _peaks(row_cover, row_strength, ROW_LIMIT)
            for y1 in picks:
                for y2 in picks:
                    if y2 - y1 < height * MIN_HEIGHT or y2 - y1 > height * MAX_HEIGHT:
                        continue
                    box_aspect = (x2 - x1) / float(y2 - y1)
                    if not MIN_ASPECT <= box_aspect <= MAX_ASPECT:
                        continue
                    cov_l = (cover_x[y2, x1] - cover_x[y1, x1]) / float(y2 - y1)
                    cov_r = (cover_x[y2, x2] - cover_x[y1, x2]) / float(y2 - y1)
                    if min(cov_l, cov_r) < SIDE_COVER:
                        continue
                    str_l = (strength_x[y2, x1] - strength_x[y1, x1]) / float(y2 - y1)
                    str_r = (strength_x[y2, x2] - strength_x[y1, x2]) / float(y2 - y1)
                    str_t = float(row_strength[y1])
                    str_b = float(row_strength[y2])
                    border = (str_l + str_r + str_t + str_b) / 4.0
                    if border < MIN_BORDER:
                        continue
                    interior = _area_mean(integral, x1, y1, x2, y2)
                    aspect_bonus = 10.0 * float(np.exp(-((box_aspect - 1.55) / 0.6) ** 2))
                    corner = min(x1, width - x2) / width + min(y1, height - y2) / height
                    corner_bonus = 7.0 * float(np.exp(-(corner / 0.22) ** 2))
                    rect = (x1 / width, y1 / height, wid / width, (y2 - y1) / height)
                    score = border - 1.8 * interior + aspect_bonus + corner_bonus
                    if previous is not None:
                        score += 16.0 * _iou(rect, previous)
                    if found:
                        score += 18.0 if any(_inside((x1, y1, wid, y2 - y1), box)
                                             for box in found) else 0.0
                    boxes.append((rect, score))
                    if score > best[1]:
                        best = (rect, score)
    if best[0] is None or best[1] < SCORE_FLOOR:
        if found:
            return _face_crop(frame["array"], found[0], previous), 30.0
        return None, float(best[1])
    return _outermost(best, boxes)


def _contains(outer, inner, margin: float = 0.02) -> bool:
    """True when ``inner`` sits inside ``outer`` (allowing a small margin)."""
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return (ix >= ox - margin and iy >= oy - margin
            and ix + iw <= ox + ow + margin and iy + ih <= oy + oh + margin)


def _outermost(best, boxes, rounds: int = 3):
    """Climb out of nested UI rectangles to the enclosing camera window.

    A webcam window usually contains a monitor, a desk or an overlay, and each of
    those is itself a clean rectangle.  The outer window is the one that keeps
    enclosing the winner while still explaining its own borders.
    """
    rect, score = best
    for _ in range(rounds):
        outer = None
        for other, other_score in boxes:
            if other_score < score * NEST_RATIO or _iou(other, rect) > 0.95:
                continue
            if not _contains(other, rect):
                continue
            aspect = other[2] * 1000.0 / max(1.0, other[3])
            if not OUT_ASPECT[0] <= aspect <= OUT_ASPECT[1]:
                continue
            if _area(other) > _area(rect) * OUT_GROWTH:
                continue
            if outer is None or _area(other) > _area(outer[0]):
                outer = (other, other_score)
        if outer is None:
            break
        rect, score = outer
    return rect, score


def _area(rect) -> float:
    return rect[2] * rect[3]


def _bounds(length: int, parts: int) -> np.ndarray:
    edges = np.linspace(0, length, parts + 1).astype(int)
    edges = np.maximum.accumulate(edges + np.arange(parts + 1) * 0)  # keep increasing
    for index in range(1, parts + 1):
        if edges[index] <= edges[index - 1]:
            edges[index] = edges[index - 1] + 1
    return np.clip(edges, 0, length)


def _gray(array: np.ndarray, size=(64, 36)) -> np.ndarray:
    """Downscale a grayscale patch to a fixed template size (numpy only)."""
    height, width = array.shape
    rows = _bounds(height, size[1])
    cols = _bounds(width, size[0])
    rows[-1] = min(rows[-1], height)
    cols[-1] = min(cols[-1], width)
    blocks = np.add.reduceat(np.add.reduceat(np.asarray(array, dtype=np.float64),
                                             rows[:-1], axis=0),
                             cols[:-1], axis=1)
    counts = np.outer(np.diff(rows), np.diff(cols)).astype(np.float64)
    return (blocks / np.maximum(counts, 1)).astype(np.float32)


def _crop_gray(gray: np.ndarray, rect, size=(64, 36)) -> np.ndarray:
    height, width = gray.shape
    x = int(np.clip(rect[0] * width, 0, width - 2))
    y = int(np.clip(rect[1] * height, 0, height - 2))
    w = int(np.clip(rect[2] * width, 2, width - x))
    h = int(np.clip(rect[3] * height, 2, height - y))
    return _gray(gray[y:y + h, x:x + w], size)


def match(previous: np.ndarray, current: np.ndarray, rect, search: float = 0.5):
    """Track the crop by matching its own pixels in the next frame.

    ``search`` is how far the window may have moved, as a fraction of its own
    size.  A coarse pass keeps the cost low, then a fine pass snaps to the pixel.
    Returns ``(rect, correlation)``; the size of the crop is preserved.
    """
    template = _crop_gray(previous, rect)
    if template.std() < 1.0:
        return None, 0.0
    template = template - template.mean()
    norm = float(np.sqrt((template ** 2).sum())) or 1.0
    height, width = current.shape
    x0 = int(rect[0] * width)
    y0 = int(rect[1] * height)
    wid = max(2, int(rect[2] * width))
    hgt = max(2, int(rect[3] * height))
    step_x = max(4, int(wid * search))
    step_y = max(4, int(hgt * search))

    def _best_search(span_x: int, span_y: int, stride: int, centre_x: int, centre_y: int):
        best = (0.0, centre_x, centre_y)
        for dy in range(-span_y, span_y + 1, stride):
            top = centre_y + dy
            if top < 0 or top + hgt > height:
                continue
            for dx in range(-span_x, span_x + 1, stride):
                left = centre_x + dx
                if left < 0 or left + wid > width:
                    continue
                sample = _gray(current[top:top + hgt, left:left + wid], (64, 36))
                sample = sample - sample.mean()
                denominator = float(np.sqrt((sample ** 2).sum())) or 1.0
                score = float((template * sample).sum()) / (norm * denominator)
                if score > best[0]:
                    best = (score, left, top)
        return best

    score, left, top = _best_search(step_x, step_y, 4, x0, y0)
    score, left, top = _best_search(4, 4, 1, left, top)
    if score < 0.45:
        return None, score
    return (left / width, top / height, rect[2], rect[3]), score


def clamp(rect) -> tuple[float, float, float, float]:
    try:
        x, y, w, h = (float(value) for value in rect)
    except (TypeError, ValueError):
        return FULL
    if not all(np.isfinite(value) for value in (x, y, w, h)) or w <= 0 or h <= 0:
        return FULL
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.02), 1.0 - x)
    h = min(max(h, 0.02), 1.0 - y)
    return (x, y, w, h)


class Follower:
    """Follow the camera inset: track small moves, re-lock after losing it.

    Tracking costs a cheap local match on the crop's own pixels, so a streamer
    dragging the window around is followed every tick; the geometric detector
    only has to run when tracking fails or nothing is locked yet.
    """

    def __init__(self, interval: float = MIN_INTERVAL):
        self.interval = interval
        self.rect = None
        self.score = 0.0
        self.pending = None
        self.pending_count = 0
        self.last_run = 0.0
        self.misses = 0
        self.prior = None
        self.gray = None

    def due(self, now: float) -> bool:
        return now - self.last_run >= self.interval

    def reset(self):
        self.rect = None
        self.score = 0.0
        self.pending = None
        self.pending_count = 0
        self.misses = 0
        self.prior = None
        self.gray = None

    def update(self, gray, now: float):
        """Feed one frame; return the crop to display, or ``None`` for no change."""
        self.last_run = now
        current = np.asarray(gray, dtype=np.float32)
        previous, self.gray = self.gray, current
        if self.rect is not None and previous is not None and previous.shape == current.shape:
            moved, quality = match(previous, current, self.rect)
            if moved is not None:
                shifted = _iou(moved, self.rect) < 0.995
                self.rect, self.score = moved, max(quality * 120.0, self.score * 0.8)
                self.misses = 0
                return moved if shifted else None
            self.misses += 1
            if self.misses < 2:
                return None
        frame = analyse(current)
        rect, score = detect(current, previous=self.rect, prior=self.prior, frame=frame)
        if frame is not None:
            self.prior = (frame["strong_x"], frame["strong_y"])
        if rect is None or score < SCORE_FLOOR:
            self.misses += 1
            if self.misses >= 4:
                self.rect = None
                self.score = 0.0
            return None
        self.misses = 0
        self.rect, self.score = rect, score
        self.pending = None
        self.pending_count = 0
        return rect
