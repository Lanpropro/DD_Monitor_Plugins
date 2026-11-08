"""Cover image rendering and file output; canvas and exports use the same pixels."""
import os
from pathlib import Path
import tempfile
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPainterPathStroker
from ddm import theme
from .cover_model import SIZES, validate
from .media import check_cancel


def crop_rect(image, size, frame):
    width = min(image.width(), image.height()*size[0]/size[1])/frame['zoom']
    height = width*size[1]/size[0]
    x = max(width/2, min(image.width()-width/2, frame['x']*image.width()))
    y = max(height/2, min(image.height()-height/2, frame['y']*image.height()))
    return QRectF(x-width/2, y-height/2, width, height)


def render_cover(data, ratio, *, cache=None):
    validate(data)
    cache = {} if cache is None else cache
    def load(path):
        if path not in cache:
            cache[path] = QImage(path)
        if cache[path].isNull():
            raise FileNotFoundError('封面图片无法读取，请重新选择：'+path)
        return cache[path]
    size = SIZES[ratio]
    image = QImage(*size, QImage.Format_RGB32)
    image.fill(QColor('#16191f'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    try:
        if data['background']:
            picture = load(data['background'])
            painter.drawImage(QRectF(0, 0, *size), picture, crop_rect(picture, size, data['views'][ratio]['frame']))
        painter.translate(-(1920-size[0])/2, 0)
        for item in data['views'][ratio]['elements']:
            painter.save()
            painter.setOpacity(item['opacity'])
            width, height = item['w']*1920, item['h']*1080
            painter.translate((item['x']+item['w']/2)*1920, (item['y']+item['h']/2)*1080)
            painter.rotate(item['rotation'])
            rect = QRectF(-width/2, -height/2, width, height)
            if item['kind'] == 'image':
                if item['flip']:
                    painter.scale(-1, 1)
                painter.drawImage(rect, load(item['path']))
            else:
                painter.setClipRect(rect.adjusted(-item['outline'], -item['outline'], item['outline'], item['outline']))
                font = QFont(item['font'] or theme.danmaku_font_family())
                font.setPixelSize(round(item['font_size']))
                font.setBold(item['bold'])
                font.setItalic(item['italic'])
                metrics = QFontMetricsF(font)
                path = QPainterPath()
                for index, line in enumerate(item['text'].splitlines() or ['']):
                    measured = metrics.horizontalAdvance(line)
                    x = rect.left() if item['align'] == 'left' else rect.right()-measured if item['align'] == 'right' else -measured/2
                    path.addText(QPointF(x, rect.top()+metrics.ascent()+index*metrics.lineSpacing()*1.15), font, line)
                if item['background']:
                    painter.fillRect(path.boundingRect().adjusted(-12, -8, 12, 8), QColor(item['background']))
                if item['shadow']:
                    painter.save()
                    painter.translate(5, 5)
                    painter.fillPath(path, QColor(0, 0, 0, 170))
                    painter.restore()
                if item['outline'] and item['stroke']:
                    stroker = QPainterPathStroker()
                    stroker.setWidth(item['outline']*2)
                    stroker.setJoinStyle(Qt.RoundJoin)
                    painter.fillPath(stroker.createStroke(path), QColor(item['stroke']))
                painter.fillPath(path, QColor(item['color']))
            painter.restore()
    finally:
        painter.end()
    return image


def export_covers(job, data, targets, image_format='png'):
    if image_format not in ('png', 'jpg') or set(targets) != set(SIZES):
        raise ValueError('请选择PNG或JPEG，并提供两种比例的目标')
    paths = {key: Path(value) for key, value in targets.items()}
    if len(set(paths.values())) != 2 or any(path.exists() for path in paths.values()):
        raise FileExistsError('封面文件已存在或名称重复，请使用新文件名')
    published = []
    try:
        for ratio, target in paths.items():
            check_cancel(job)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.cover-', dir=target.parent) as temporary:
                output = Path(temporary)/('cover.'+image_format)
                image = render_cover(data, ratio)
                if not image.save(str(output), image_format.upper(), 95):
                    raise RuntimeError('封面编码失败：'+str(target))
                check_cancel(job)
                if target.exists():
                    raise FileExistsError('封面文件已存在：'+str(target))
                if os.name == 'nt':
                    output.rename(target)
                else:
                    os.link(output, target)
                published.append(target)
        return {'images': {key: str(path) for key, path in paths.items()}}
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        raise
