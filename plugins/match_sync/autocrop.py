"""Locate the talker camera window inside a secondary stream frame.

Pure numpy (OpenCV is optional and only used for faces).  The detector looks for
a rectangle built from four straight, high contrast borders whose interior is
calmer than the surrounding game picture, prefers a 16:9 shape and a corner
position, and falls back to a box around a detected face when no rectangle can
be found.  It is meant to run once per "自动框选" press, not every frame.
"""
from __future__ import annotations

import numpy as np

FULL = (0.0, 0.0, 1.0, 1.0)

MIN_WIDTH = 0.12
MAX_WIDTH = 0.62
MIN_HEIGHT = 0.10
MAX_HEIGHT = 0.72
MIN_ASPECT = 0.70
MAX_ASPECT = 2.80

EDGE = 12.0             # |difference| that counts as an edge sample
COVER = 0.55            # a border has to cover this much of the window side
SIDE_COVER = 0.45       # a side has to show an edge on this many samples
COLUMN_LIMIT = 18       # strongest vertical borders tried as candidates
ROW_LIMIT = 7           # strongest horizontal borders tried per width
SCORE_FLOOR = 42.0
MIN_BORDER = 18.0
NEST_RATIO = 0.55       # an enclosing box may score less, but not arbitrarily less
OUT_ASPECT = (1.10, 2.20)   # an enclosing camera window looks like 4:3 .. 16:9
OUT_GROWTH = 3.0        # never climb from a small inset to a huge frame

ASPECT_MEAN = 1.78      # 16:9 camera windows are by far the most common
ASPECT_SPREAD = 0.55
CORNER_RADIUS = 0.26    # cameras tend to sit in a corner
FACE_BONUS = 18.0
FACE_MIN = 0.02         # a face narrower than this is noise
FACE_MAX = 0.30


class Line:
    """One straight border: a column/row position plus the run that produced it."""

    __slots__ = ("pos", "start", "end", "strength")

    def __init__(self, pos: int, start: int, end: int, strength: float):
        self.pos = int(pos)
        self.start = int(start)
        self.end = int(end)
        self.strength = float(strength)

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def weight(self) -> float:
        return self.length * self.strength

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Line(pos={self.pos}, {self.start}-{self.end}, {self.strength:.0f})"


def _runs(strong: np.ndarray, magnitude: np.ndarray):
    """Longest run of True cells in every row of ``strong``.

    Returns ``(length, start, strength)`` arrays where ``start`` is the column
    index where the run begins and ``strength`` is the mean ``magnitude`` over
    the run.
    """
    count, width = strong.shape
    length = np.zeros(count, dtype=int)
    start = np.zeros(count, dtype=int)
    strength = np.zeros(count, dtype=float)
    rows = np.arange(count)
    current = np.zeros(count, dtype=int)
    begin = np.zeros(count, dtype=int)
    best = np.zeros(count, dtype=int)
    best_start = np.zeros(count, dtype=int)
    for step in range(width):
        inside = strong[:, step]
        current = np.where(inside, current + 1, 0)
        begin = np.where(current == 1, step, begin)
        better = current > best
        best = np.where(better, current, best)
        best_start = np.where(better, begin, best_start)
    length[:] = best
    start[:] = np.where(best > 0, best_start, 0)
    # prefix sums over magnitude let us average the winning run in one pass
    prefix = np.concatenate([np.zeros((count, 1)), np.cumsum(magnitude, axis=1)], axis=1)
    ends = np.clip(start + length, 0, width)
    totals = prefix[rows, ends] - prefix[rows, start]
    strength[:] = np.where(length > 0, totals / np.maximum(length, 1), 0.0)
    return length, start, strength


def _borders(length, start_at, strength, minimum: int):
    """Merge adjacent positions carrying the same run into single borders."""
    lines: list[Line] = []
    group: list[int] = []

    def flush() -> None:
        if not group:
            return
        best = max(group, key=lambda pos: (int(length[pos]), float(strength[pos])))
        first = min(int(start_at[pos]) for pos in group)
        last = max(int(start_at[pos]) + int(length[pos]) for pos in group)
        lines.append(Line(best, first, last, float(strength[best])))

    for pos in range(len(length)):
        if int(length[pos]) < minimum:
            flush()
            group = []
            continue
        if group:
            prev = group[-1]
            if (abs(int(start_at[pos]) - int(start_at[prev])) <= 4
                    and abs(int(length[pos]) - int(length[prev])) <= 8):
                group.append(pos)
                continue
            flush()
            group = []
        group.append(pos)
    flush()
    return lines


def _integral(values: np.ndarray) -> np.ndarray:
    return np.pad(values.cumsum(0).cumsum(1), ((1, 0), (1, 0)))


def _area_mean(integral: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> float:
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = max(x1, x2), max(y1, y2)
    total = integral[y2, x2] - integral[y1, x2] - integral[y2, x1] + integral[y1, x1]
    return float(total) / max(1, (x2 - x1) * (y2 - y1))


def _iou(a, b) -> float:
    if a is None or b is None:
        return 0.0
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    width = min(ax2, bx2) - max(ax1, bx1)
    height = min(ay2, by2) - max(ay1, by1)
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    union = aw * ah + bw * bh - inter
    return float(inter / union) if union > 0 else 0.0


def _face_boxes(gray: np.ndarray):
    """Face rectangles from OpenCV when it happens to be installed."""
    cascade = _cascade()
    if cascade is None:
        return []
    try:
        import cv2
    except ImportError:  # pragma: no cover - optional dependency
        return []
    height, width = gray.shape[:2]
    scale = 2
    source = np.clip(np.asarray(gray), 0, 255).astype(np.uint8)
    big = cv2.resize(source, (width * scale, height * scale), interpolation=cv2.INTER_CUBIC)
    found = cascade.detectMultiScale(big, scaleFactor=1.08, minNeighbors=3,
                                     minSize=(24 * scale, 24 * scale))
    return [(int(x / scale), int(y / scale), int(w / scale), int(h / scale)) for x, y, w, h in found]


_CASCADE = None


def _cascade():
    global _CASCADE
    if _CASCADE is None:
        try:
            import cv2
            path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            _CASCADE = cv2.CascadeClassifier(path) or False
        except Exception:  # pragma: no cover - optional dependency
            _CASCADE = False
    return _CASCADE or None


def _face_boxes(gray: np.ndarray):
    """Confident face boxes, or an empty list.

    A face only counts when the same spot is reported at two or more scales --
    a single hit from the Haar cascade is far too easy to get from game art.
    """
    cascade = _cascade()
    if cascade is None:
        return []
    try:
        import cv2
    except ImportError:  # pragma: no cover - optional dependency
        return []
    height, width = gray.shape[:2]
    source = np.clip(np.asarray(gray), 0, 255).astype(np.uint8)
    groups: list[list] = []
    for scale in (2, 3, 4):
        big = cv2.resize(source, (width * scale, height * scale), interpolation=cv2.INTER_CUBIC)
        found = cascade.detectMultiScale(big, scaleFactor=1.05, minNeighbors=4,
                                         minSize=(24 * scale, 24 * scale))
        for x, y, w, h in found:
            box = (x / scale, y / scale, w / scale, h / scale)
            for group in groups:
                if _iou(group[0], box) >= 0.3:
                    group[1].append(box)
                    break
            else:
                groups.append([box, [box]])
    confident = []
    for _first, boxes in groups:
        if len(boxes) < 2:
            continue
        average = tuple(float(np.mean([box[i] for box in boxes])) for i in range(4))
        confident.append((len(boxes), average))
    confident.sort(key=lambda item: (item[0], item[1][2] * item[1][3]), reverse=True)
    return [box for _hits, box in confident]


def _inside(box, face) -> bool:
    x, y, w, h = box
    fx, fy, fw, fh = face
    centre_x, centre_y = fx + fw / 2, fy + fh / 2
    return x <= centre_x <= x + w and y <= centre_y <= y + h


def _face_crop(gray: np.ndarray, face, previous=None):
    """A 16:9 box around a face: head room above, shoulders below."""
    height, width = gray.shape[:2]
    fx, fy, fw, fh = face
    aspect = 16 / 9
    if previous is not None and previous[3] > 0:
        aspect = (previous[2] * width) / max(1.0, previous[3] * height)
    box_w = min(max(fw * 4.2, width * MIN_WIDTH), width * MAX_WIDTH)
    box_h = box_w / aspect
    if box_h < height * MIN_HEIGHT:
        box_h = height * MIN_HEIGHT
        box_w = box_h * aspect
    centre_x = fx + fw / 2
    centre_y = fy + fh * 0.62
    x = min(max(centre_x - box_w / 2, 0), width - box_w)
    y = min(max(centre_y - box_h / 2, 0), height - box_h)
    return (x / width, y / height, box_w / width, box_h / height)


def analyse(gray: np.ndarray):
    """Column/row border candidates for one grayscale frame."""
    gray = np.asarray(gray, dtype=np.float32)
    if gray.ndim == 3:
        gray = gray.mean(axis=2)
    height, width = gray.shape[:2]
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    strong_x = dx > EDGE
    strong_y = dy > EDGE
    minimum = max(6, min(height, width) // 6)
    columns = _borders(*_runs(strong_x.T, dx.T), minimum)
    rows = _borders(*_runs(strong_y, dy), max(6, min(height, width) // 8))
    return {
        "width": width,
        "height": height,
        "columns": columns,
        "rows": rows,
        "strong_rows": strong_y,
        "magnitude": dx,
    }


def _covers(strong_rows: np.ndarray, pos: int, x1: int, x2: int) -> float:
    if pos < 0 or pos >= strong_rows.shape[0] or x2 <= x1:
        return 0.0
    return float(strong_rows[pos, x1:x2].mean())


def detect(gray: np.ndarray, previous=None, faces: bool = True):
    """Best camera window in ``gray`` as ``(rect, score)``; ``rect`` may be None."""
    frame = analyse(gray)
    width, height = frame["width"], frame["height"]
    columns = sorted(frame["columns"], key=lambda line: line.weight, reverse=True)[:COLUMN_LIMIT]
    rows = sorted({line.pos for line in frame["rows"]})
    strong_rows = frame["strong_rows"]
    magnitude = frame["magnitude"]
    interior_integral = _integral(magnitude)
    slack = max(4, int(0.10 * min(width, height)))

    best_rect = None
    best_score = 0.0
    boxes = []
    for left in columns:
        for right in columns:
            span = right.pos - left.pos
            if span < width * MIN_WIDTH or span > width * MAX_WIDTH:
                continue
            if abs(left.start - right.start) > slack or abs(left.end - right.end) > slack:
                continue
            top = (left.start + right.start) / 2
            bottom = (left.end + right.end) / 2
            box_h = bottom - top
            if box_h < height * MIN_HEIGHT or box_h > height * MAX_HEIGHT:
                continue
            aspect = span / max(1.0, box_h)
            if not MIN_ASPECT <= aspect <= MAX_ASPECT:
                continue
            if not any(abs(row - top) <= 2 for row in rows):
                continue
            if not any(abs(row - bottom) <= 2 for row in rows):
                continue
            cover = max(_covers(strong_rows, pos, left.pos, right.pos)
                        for pos in range(int(top) - 3, int(top) + 4))
            cover = max(cover, max(_covers(strong_rows, pos, left.pos, right.pos)
                                   for pos in range(int(bottom) - 3, int(bottom) + 4)))
            if cover < COVER:
                continue
            border = left.strength + right.strength
            if border < MIN_BORDER:
                continue
            interior = _area_mean(interior_integral, left.pos + 2, int(top) + 2,
                                  right.pos - 1, int(bottom) - 1)
            score = border - 1.8 * interior
            score += 10 * np.exp(-(((aspect - ASPECT_MEAN) / ASPECT_SPREAD) ** 2))
            centre_x = (left.pos + right.pos) / 2 / width
            centre_y = (top + bottom) / 2 / height
            corner = min(np.hypot(x - centre_x, y - centre_y)
                         for x in (0.0, 1.0) for y in (0.0, 1.0))
            score += 7 * np.exp(-((corner / CORNER_RADIUS) ** 2))
            rect = (left.pos / width, top / height, span / width, box_h / height)
            if previous is not None:
                score += 16 * _iou(rect, previous)
            boxes.append((score, left.pos, right.pos, top, bottom))
            if score > best_score:
                best_score = score
                best_rect = rect

    if faces:
        found = _face_boxes(np.asarray(gray, dtype=np.float32))
        faces = [face for face in found
                 if FACE_MIN * width <= face[2] <= FACE_MAX * width]
        if faces and best_rect is not None and any(_inside(best_rect, face) for face in faces):
            best_score += FACE_BONUS
        elif faces and best_rect is None:
            best_rect = _face_crop(np.asarray(gray, dtype=np.float32), faces[0], previous)
            best_score = 30.0 + FACE_BONUS

    if best_rect is not None:
        best_rect = _outermost((best_score, best_rect), boxes, width, height)
    if best_rect is None or best_score < SCORE_FLOOR:
        return None, float(best_score)
    return tuple(float(value) for value in best_rect), float(best_score)


def _contains(outer, inner, margin: float = 0.02) -> bool:
    x1, y1, w, h = outer
    ix, iy, iw, ih = inner
    return (ix - x1 >= -margin and iy - y1 >= -margin
            and x1 + w - (ix + iw) >= -margin and y1 + h - (iy + ih) >= -margin)


def _outermost(best, boxes, width: int, height: int):
    """Climb to an enclosing border when it wraps the detected inset."""
    score, rect = best
    for _ in range(3):
        step = None
        for candidate in boxes:
            candidate_score, x1, x2, top, bottom = candidate
            box = (x1 / width, top / height, (x2 - x1) / width, (bottom - top) / height)
            if not _contains(box, rect):
                continue
            aspect = (x2 - x1) / max(1.0, bottom - top)
            if not OUT_ASPECT[0] <= aspect <= OUT_ASPECT[1]:
                continue
            if _area(box) > OUT_GROWTH * _area(rect):
                continue
            if candidate_score < NEST_RATIO * score:
                continue
            step = box
            break
        if step is None:
            break
        rect = step
    return rect


def _area(rect) -> float:
    return max(0.0, rect[2]) * max(0.0, rect[3])


def clamp(rect) -> tuple[float, float, float, float]:
    """Keep a crop inside the frame and above the minimum useful size."""
    if rect is None:
        return FULL
    try:
        x, y, w, h = (float(value) for value in rect)
    except (TypeError, ValueError):
        return FULL
    if any(value != value for value in (x, y, w, h)):
        return FULL
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(w, 1.0 - x)
    h = min(h, 1.0 - y)
    if w <= 0.0 or h <= 0.0:
        return FULL
    # Shrink the box when the source overlaps an edge, then enforce the minimum
    # useful size only where the frame leaves room for it.
    w = max(w, min(MIN_WIDTH, 1.0 - x))
    h = max(h, min(MIN_HEIGHT, 1.0 - y))
    return x, y, min(w, 1.0 - x), min(h, 1.0 - y)


def snap(gray, rect, tolerance: float = 0.045):
    """Nudge a hand-drawn crop onto the nearest strong content step.

    Each edge searches ``tolerance`` of the frame for the biggest change in the
    strip mean along that edge, so a slightly tight or loose manual box lands on
    the real boundary.  Edges without a clear step keep their original place.
    """
    if rect is None:
        return None
    height, width = gray.shape[:2]
    x, y, w, h = clamp(rect)
    x0, y0 = int(round(x * width)), int(round(y * height))
    x1, y1 = int(round((x + w) * width)), int(round((y + h) * height))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return (x, y, w, h)
    span = max(2, int(round(tolerance * max(width, height))))
    band = max(2, min(4, (y1 - y0) // 12, (x1 - x0) // 12))
    rows = slice(max(0, y0 + band), min(height, y1 - band))
    cols = slice(max(0, x0 + band), min(width, x1 - band))

    left = _edge(gray[rows, max(0, x0 - 2 * span - 2):min(width, x0 + 2 * span + 2)], span + 2,
                 band, vertical=True)
    right_patch = gray[rows, max(0, x1 - 2 * span - 2):min(width, x1 + 2 * span + 2)]
    right = _edge(right_patch, right_patch.shape[1] - span - 2, band, vertical=True)
    top = _edge(gray[max(0, y0 - 2 * span - 2):min(height, y0 + 2 * span + 2), cols], span + 2,
                band, vertical=False)
    bottom_patch = gray[max(0, y1 - 2 * span - 2):min(height, y1 + 2 * span + 2), cols]
    bottom = _edge(bottom_patch, bottom_patch.shape[0] - span - 2, band, vertical=False)

    if left is not None:
        x0 = max(0, x0 - 2 * span - 2) + left
    if right is not None:
        x1 = max(0, x1 - 2 * span - 2) + right
    if top is not None:
        y0 = max(0, y0 - 2 * span - 2) + top
    if bottom is not None:
        y1 = max(0, y1 - 2 * span - 2) + bottom
    x0 = min(max(x0, 0), width - 8)
    y0 = min(max(y0, 0), height - 8)
    x1 = min(max(x1, x0 + 8), width)
    y1 = min(max(y1, y0 + 8), height)
    return clamp((x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height))


def _edge(patch, centre, band, vertical):
    """Index of the strongest mean step near ``centre`` in a patch."""
    if patch.size == 0:
        return None
    profile = patch.mean(axis=0 if vertical else 1).astype(np.float32)
    size = profile.size
    scores = []
    for index in range(2, size - 2):
        before = profile[max(0, index - band):index].mean()
        after = profile[index:min(size, index + band)].mean()
        scores.append((abs(float(after - before)), index))
    if not scores:
        return None
    peak = max(value for value, _ in scores)
    if peak < 4.0:
        return None
    nearest = min((entry for entry in scores if entry[0] >= peak * 0.6), key=lambda item: abs(item[1] - centre))
    return nearest[1]


def detect_best(frames, previous=None, faces: bool = True):
    """Run :func:`detect` over several resolutions and keep the best answer.

    Bigger frames carry more detail, so a tie (scores within 6 %) goes to the
    earliest frame in ``frames`` -- callers pass them largest first.
    """
    best_rect, best_score = None, 0.0
    for gray in frames:
        if gray is None or getattr(gray, "size", 0) == 0:
            continue
        rect, score = detect(gray, previous=previous, faces=faces)
        if rect is None:
            continue
        if score > best_score * 1.06 or best_rect is None:
            best_rect, best_score = rect, score
    return best_rect, best_score
