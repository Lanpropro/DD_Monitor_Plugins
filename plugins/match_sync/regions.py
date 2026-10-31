"""Find common content geometry, then verify time with ordered dynamic scenes."""
from dataclasses import replace

from .engine import Match, Sample, match_scenes, refine_match, np
from .media import fingerprint, scene_feature

try:
    import cv2
except ImportError:
    cv2 = None


def _gray(image):
    return np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()].copy()


def locate_regions(reference, other):
    if cv2 is None or np is None:
        return None
    a = [(stamp, large) for stamp, _small, large in reference if large is not None]
    b = [(stamp, large) for stamp, _small, large in other if large is not None]
    if len(a) < 5 or len(b) < 5:
        return None
    a = a[-5::2]
    b = [b[index] for index in np.unique(np.linspace(0, len(b) - 1, min(24, len(b))).astype(int))]
    orb = cv2.ORB_create(nfeatures=600, edgeThreshold=8, patchSize=15, fastThreshold=8)
    first = [(stamp, orb.detectAndCompute(_gray(image), None)) for stamp, image in a]
    second = [(stamp, orb.detectAndCompute(_gray(image), None)) for stamp, image in b]
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    groups = []
    for ta, (ka, da) in first:
        if da is None:
            continue
        for tb, (kb, db) in second:
            if db is None or len(db) < 2:
                continue
            pairs = [pair[0] for pair in matcher.knnMatch(da, db, k=2)
                     if len(pair) == 2 and pair[0].distance < .7 * pair[1].distance]
            if len(pairs) < 14:
                continue
            pa = np.float32([ka[p.queryIdx].pt for p in pairs])
            pb = np.float32([kb[p.trainIdx].pt for p in pairs])
            matrix, mask = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=2)
            if matrix is None or mask.sum() < 12 or mask.mean() < .5:
                continue
            scale, turn, x, y = matrix[0, 0], matrix[0, 1], matrix[0, 2], matrix[1, 2]
            if not .3 <= scale <= 3 or abs(turn) > scale * .03:
                continue
            points = pa[mask.ravel().astype(bool)]
            lo, hi = points.min(axis=0), points.max(axis=0)
            if hi[0] - lo[0] < a[0][1].width() * .4 or hi[1] - lo[1] < a[0][1].height() * .35:
                continue  # A shared avatar, clock or logo is insufficient.
            group = next((g for g in groups if abs(g[0][0] - scale) < .06
                          and abs(g[0][1] - x) < 6 and abs(g[0][2] - y) < 6), None)
            if group is None:
                group = [(scale, x, y), []]
                groups.append(group)
            group[1].append((ta, tb, matrix, lo, hi))
    groups = [g for g in groups if len({p[0] for p in g[1]}) >= 2
              and len({p[1] for p in g[1]}) >= 3]
    if not groups:
        return None
    records = max(groups, key=lambda g: len(g[1]))[1]
    matrix = np.median([p[2] for p in records], axis=0)
    lo = np.median([p[3] for p in records], axis=0) - 4
    hi = np.median([p[4] for p in records], axis=0) + 4
    lo = np.maximum(lo, (0, 0))
    hi = np.minimum(hi, (a[0][1].width(), a[0][1].height()))
    # Keep both crops in bounds, so clipping cannot change their relative scale.
    scale = matrix[0, 0]
    shift = matrix[:, 2]
    lo = np.maximum(lo, -shift / scale)
    hi = np.minimum(hi, (np.array((b[0][1].width(), b[0][1].height())) - shift) / scale)
    if np.any(hi - lo < 24):
        return None
    def normalized(start, end, image):
        size = np.array((image.width(), image.height()))
        return tuple(start / size) + tuple((end - start) / size)
    return (normalized(lo, hi, a[0][1]), normalized(lo * scale + shift, hi * scale + shift, b[0][1]))


def region_samples(frames, crop):
    return [Sample(stamp, *fingerprint(image, crop), scene_feature(image, crop, (64, 24)))
            for stamp, image, _large in frames]


def match_regions(reference, other, regions=(), expected=None):
    missing = Match(None, 0, '未找到可靠的共同游戏区域；不同视角需有共同动态内容')
    if len(reference) < 40 or len(other) < 40:
        return missing
    options = [regions] if regions else []
    for attempt in range(2 if regions else 1):
        if attempt or not options:
            located = locate_regions(reference, other)
            if located is None:
                return missing
            options.append(located)
        crops = options[-1]
        a, b = region_samples(reference, crops[0]), region_samples(other, crops[1])
        candidate = (refine_match(Match(expected, .9, '复核共同区域'), a, b, focused=True)
                     if expected is not None else missing)
        if candidate.lag is None:
            candidate = match_scenes(a[::5], b[::5], focused=True)
            candidate = refine_match(candidate, a, b, focused=True)
        if candidate.lag is not None and candidate.confidence >= .65:
            return replace(candidate, reason='已自动识别共同游戏区域并验证连续画面', regions=crops)
    return missing
