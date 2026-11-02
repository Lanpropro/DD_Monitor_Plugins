"""Locate the talker camera window inside a secondary stream frame.

The host already ships numpy and OpenCV (see the app's ``requirements.txt``), so
this module leans on OpenCV's Haar cascades for the one signal that is actually
reliable on a real broadcast: a face. A talker camera exists to show a person,
and a person's face is the only thing in the frame that a generic detector can
find without knowing the stream.

The pipeline is:

  1. run three Haar cascades on a few frames taken a fraction of a second apart
  2. keep only faces reported in two or more frames at the same spot, which
     throws away the single-frame false positives that make detector output
     jump around
  3. grow a 16:9 window around the winning face; the window is
     ``FACINGS`` (2.6 / 3.4 / 4.5) times the face height, because broadcast
     cameras frame a talking head at wildly different crops
  4. snap each candidate's edges onto the nearest strong picture step and keep
     the candidate whose border is the strongest -- that is the window frame
  5. fall back to ``None`` when no face shows up at all, so the caller can tell
     the viewer to draw the box by hand instead of silently cropping the whole
     picture

``snap`` is also useful on its own: it is what pulls a hand drawn crop onto the
real window border.
"""
from __future__ import annotations

import numpy as np

try:  # the host ships opencv-python-headless; keep the module importable anyway
    import cv2
except Exception:  # pragma: no cover - optional at import time
    cv2 = None

FULL = (0.0, 0.0, 1.0, 1.0)

MIN_WIDTH = 0.12
MAX_WIDTH = 0.62
MIN_HEIGHT = 0.10
MAX_HEIGHT = 0.78

ASPECT = 16.0 / 9.0
FACINGS = (3.0, 3.6, 4.2)
FACING_SLACK = 0.55
DETECT_WIDTH = 720.0
SNAP_TOLERANCE = 0.03
FINAL_SNAP = 0.02
GROW = 0.06

CASCADES = (
    "haarcascade_frontalface_default.xml",
    "haarcascade_frontalface_alt2.xml",
    "haarcascade_profileface.xml",
)

_saved_cascades = None


def available():
    """True when a face detector can actually run."""
    return bool(_cascades())


def _cascades():
    global _saved_cascades
    if _saved_cascades is None:
        found = []
        if cv2 is not None:
            for name in CASCADES:
                try:
                    classifier = cv2.CascadeClassifier(cv2.data.haarcascades + name)
                except Exception:  # pragma: no cover - broken install
                    continue
                if not classifier.empty():
                    found.append(classifier)
        _saved_cascades = found
    return _saved_cascades


def detect(frames, faces=True):
    """Return ``(rect, confidence)`` for the talker camera, or ``(None, 0.0)``.

    ``frames`` is a sequence of grayscale images of the same size. Two or three
    frames a fraction of a second apart are enough; a single frame is accepted
    too, just with less confidence.
    """
    prepared = _prepare(frames)
    if not prepared or not faces:
        return None, 0.0
    clusters = _face_clusters(prepared)
    if not clusters:
        return None, 0.0
    current = prepared[-1]
    height, width = current.shape[:2]
    # The first cluster (most frames, then largest face) is the talker; other
    # clusters are stray matches inside the game picture. A wrong box is worse
    # than no box, so the caller keeps the old crop when this one fails.
    cluster = clusters[0]
    candidates = []
    for facing in FACINGS:
        seed = _seed(cluster["box"], facing, width, height)
        if seed is None:
            continue
        candidates.append((_border_score(current, seed), seed, cluster))
    top = max((entry[0] for entry in candidates), default=0.0)
    if top <= 0.0:
        return None, 0.0
    # Cropping a little wide still shows the whole person; cropping narrow cuts
    # their head off. Among the plausible candidates keep the largest.
    near = [entry for entry in candidates if entry[0] >= top * FACING_SLACK]
    _score, rect, cluster = max(near, key=lambda entry: entry[1][2] * entry[1][3])
    rect = _widen(rect, GROW)
    rect = snap(current, rect, tolerance=FINAL_SNAP) or rect
    confidence = min(1.0, float(cluster["frames"]) / max(1.0, float(len(prepared))))
    return clamp(rect), confidence


def _widen(rect, grow):
    """Grow a crop about its centre, so a slightly short box never cuts a head."""
    x, y, w, h = rect
    width = min(MAX_WIDTH, w * (1.0 + grow))
    height = min(MAX_HEIGHT, h * (1.0 + grow))
    return clamp((x - (width - w) / 2.0, y - (height - h) / 2.0, width, height))


def _prepare(frames):
    prepared = []
    for frame in frames or ():
        if frame is None or not getattr(frame, "size", 0):
            continue
        array = np.asarray(frame)
        if array.ndim == 3:
            array = array.mean(axis=2)
        prepared.append(array.astype(np.float32))
    if not prepared:
        return []
    if len({array.shape[:2] for array in prepared}) > 1:
        smallest = min(prepared, key=lambda array: array.shape[0] * array.shape[1]).shape[:2]
        prepared = [array[:smallest[0], :smallest[1]] for array in prepared]
    return prepared[-5:]


def _face_clusters(frames):
    """Faces seen in two or more frames at the same spot, best first."""
    groups = []
    for index, frame in enumerate(frames):
        for face in _faces(frame):
            for group in groups:
                if _overlap(group["box"], face) >= 0.30:
                    group["boxes"].append(face)
                    group["indices"].add(index)
                    group["box"] = _average(group["boxes"])
                    break
            else:
                groups.append({"box": face, "boxes": [face], "indices": {index}})
    confident = []
    needed = 1 if len(frames) == 1 else 2
    for group in groups:
        if len(group["indices"]) < needed:
            continue
        confident.append({"box": group["box"], "frames": len(group["indices"])})
    confident.sort(key=lambda item: (item["frames"], item["box"][2] * item["box"][3]),
                   reverse=True)
    return confident


def _faces(gray):
    cascades = _cascades()
    if not cascades:
        return []
    height, width = gray.shape[:2]
    scale = max(1.0, width / DETECT_WIDTH)
    source = np.clip(gray, 0, 255).astype(np.uint8)
    if scale > 1.0:
        source = cv2.resize(source, (int(width / scale), int(height / scale)),
                            interpolation=cv2.INTER_AREA)
    small_h, small_w = source.shape[:2]
    minimum = max(8, int(round(min(small_w, small_h) * 0.035)))
    found = []
    for cascade in cascades:
        for boost in (1.0, 2.0):
            big = source if boost == 1.0 else _boost(source, boost)
            factor = big.shape[1] / float(small_w)
            try:
                boxes = cascade.detectMultiScale(big, scaleFactor=1.06, minNeighbors=4,
                                                 minSize=(minimum, minimum))
            except Exception:  # pragma: no cover - defensive
                continue
            for x, y, w, h in boxes:
                found.append((x / factor * scale, y / factor * scale,
                              w / factor * scale, h / factor * scale))
    return _merge(found)


def _boost(source, factor):
    """Upscale so small broadcast faces clear the cascade's minimum size."""
    return cv2.resize(source, (int(source.shape[1] * factor), int(source.shape[0] * factor)),
                      interpolation=cv2.INTER_CUBIC)


def _merge(boxes):
    """Drop duplicates coming from multiple cascades or scales."""
    merged = []
    for box in sorted(boxes, key=lambda item: item[2] * item[3], reverse=True):
        for kept in merged:
            if _overlap(kept, box) >= 0.35:
                break
        else:
            merged.append(tuple(float(value) for value in box))
    return merged


def _seed(face, facing, width, height):
    """A 16:9 window around the face, ``facing`` times its height."""
    x, y, w, h = face
    if w <= 1 or h <= 1:
        return None
    window_h = max(8.0, h * float(facing))
    window_w = window_h * ASPECT
    if window_w > width * MAX_WIDTH:
        window_w = width * MAX_WIDTH
        window_h = window_w / ASPECT
    if window_h > height * MAX_HEIGHT:
        window_h = height * MAX_HEIGHT
        window_w = window_h * ASPECT
    centre_x = x + w / 2.0
    centre_y = y + h / 2.0
    # Shift the window to stay fully inside the frame instead of clipping it:
    # a clipped side has no border to measure, and a camera window never sits
    # half outside the picture anyway.
    left = min(max(centre_x - window_w / 2.0, 0.0), max(0.0, width - window_w))
    top = min(max(centre_y - window_h / 2.0, 0.0), max(0.0, height - window_h))
    return clamp((left / width, top / height, window_w / width, window_h / height))


def _border_score(gray, rect):
    """How strong the step is on the sides of ``rect`` that sit inside the frame.

    A composited camera window has a step on every side; a box drawn inside the
    window (around the head, say) mostly does not. Sides that are clipped by the
    frame edge have no step to measure and are skipped, otherwise a box pushed
    into the corner would score zero just for touching the edge.
    """
    height, width = gray.shape[:2]
    x, y, w, h = rect
    x0, y0 = int(round(x * width)), int(round(y * height))
    x1, y1 = int(round((x + w) * width)), int(round((y + h) * height))
    x0, y0 = max(0, min(x0, width - 4)), max(0, min(y0, height - 4))
    x1, y1 = max(x0 + 4, min(x1, width)), max(y0 + 4, min(y1, height))
    band = max(3, int(round(min(x1 - x0, y1 - y0) * 0.03)))
    rows = slice(y0 + band, max(y0 + band + 1, y1 - band))
    cols = slice(x0 + band, max(x0 + band + 1, x1 - band))
    steps = []
    # A window hugging the frame edge has no outside strip to compare against;
    # score the sides that exist, and refuse when fewer than three of them do.
    if x0 - band >= 0:
        steps.append(abs(float(gray[rows, x0:x0 + band].mean())
                         - float(gray[rows, x0 - band:x0].mean())))
    if x1 + band <= width:
        steps.append(abs(float(gray[rows, x1 - band:x1].mean())
                         - float(gray[rows, x1:x1 + band].mean())))
    if y0 - band >= 0:
        steps.append(abs(float(gray[y0:y0 + band, cols].mean())
                         - float(gray[y0 - band:y0, cols].mean())))
    if y1 + band <= height:
        steps.append(abs(float(gray[y1 - band:y1, cols].mean())
                         - float(gray[y1:y1 + band, cols].mean())))
    if len(steps) < 3:
        return 0.0
    return min(steps) + 0.25 * (sum(steps) / len(steps))


def _edges(gray):
    dx = np.zeros_like(gray, dtype=np.float32)
    dy = np.zeros_like(gray, dtype=np.float32)
    dx[:, 1:-1] = np.abs(gray[:, 2:] - gray[:, :-2]) * 0.5
    dy[1:-1, :] = np.abs(gray[2:, :] - gray[:-2, :]) * 0.5
    return dx + dy


def clamp(rect):
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
    w = max(w, min(MIN_WIDTH, 1.0 - x))
    h = max(h, min(MIN_HEIGHT, 1.0 - y))
    return x, y, min(w, 1.0 - x), min(h, 1.0 - y)


def snap(gray, rect, tolerance=SNAP_TOLERANCE):
    """Nudge a crop onto the nearest strong content step on each side."""
    if rect is None:
        return None
    height, width = np.asarray(gray).shape[:2]
    x, y, w, h = clamp(rect)
    x0, y0 = int(round(x * width)), int(round(y * height))
    x1, y1 = int(round((x + w) * width)), int(round((y + h) * height))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return (x, y, w, h)
    span = max(2, int(round(tolerance * max(width, height))))
    band = max(2, min(4, (y1 - y0) // 12, (x1 - x0) // 12))
    rows = slice(max(0, y0 + band), min(height, y1 - band))
    cols = slice(max(0, x0 + band), min(width, x1 - band))
    if rows.stop <= rows.start or cols.stop <= cols.start:
        return (x, y, w, h)

    reach = 2 * span + 2
    left_start = max(0, x0 - reach)
    right_stop = min(width, x1 + reach)
    top_start = max(0, y0 - reach)
    bottom_stop = min(height, y1 + reach)
    left = _snap_edge(gray[rows, left_start:min(width, x0 + reach)],
                      x0 - left_start, band, vertical=True)
    right_patch = gray[rows, max(0, x1 - reach):right_stop]
    right = _snap_edge(right_patch, x1 - max(0, x1 - reach), band, vertical=True)
    top = _snap_edge(gray[top_start:min(height, y0 + reach), cols],
                     y0 - top_start, band, vertical=False)
    bottom_patch = gray[max(0, y1 - reach):bottom_stop, cols]
    bottom = _snap_edge(bottom_patch, y1 - max(0, y1 - reach), band, vertical=False)

    if left is not None:
        x0 = left_start + left
    if right is not None:
        x1 = max(0, x1 - reach) + right
    if top is not None:
        y0 = top_start + top
    if bottom is not None:
        y1 = max(0, y1 - reach) + bottom
    x0 = min(max(x0, 0), width - 8)
    y0 = min(max(y0, 0), height - 8)
    x1 = min(max(x1, x0 + 8), width)
    y1 = min(max(y1, y0 + 8), height)
    return clamp((x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height))


def _snap_edge(patch, centre, band, vertical):
    """Index of the strongest mean step near ``centre`` inside ``patch``."""
    patch = np.asarray(patch)
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
    nearest = min((entry for entry in scores if entry[0] >= peak * 0.6),
                  key=lambda item: abs(item[1] - centre))
    return nearest[1]


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    return float(inter / (aw * ah + bw * bh - inter)) if inter > 0 else 0.0


def _average(boxes):
    return tuple(float(np.mean([box[i] for box in boxes])) for i in range(4))
