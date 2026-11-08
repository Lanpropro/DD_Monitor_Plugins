"""Text paths shared by cover pixels and styled project frames."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainterPath, QPainterPathStroker
from ddm import theme


def draw_text(painter, rect, item, scale=1):
    outline = item['outline'] * scale
    painter.setClipRect(rect.adjusted(-outline, -outline, outline, outline))
    font = QFont(item['font'] or theme.danmaku_font_family())
    font.setPixelSize(max(1, round(item['font_size'] * scale)))
    font.setBold(item['bold'])
    font.setItalic(item['italic'])
    metrics = QFontMetricsF(font)
    path = QPainterPath()
    for index, line in enumerate(item['text'].splitlines() or ['']):
        measured = metrics.horizontalAdvance(line)
        x = rect.left() if item['align'] == 'left' else rect.right()-measured if item['align'] == 'right' else rect.center().x()-measured/2
        path.addText(QPointF(x, rect.top()+metrics.ascent()+index*metrics.lineSpacing()*1.15), font, line)
    if item['background']:
        painter.fillRect(path.boundingRect().adjusted(-12*scale, -8*scale, 12*scale, 8*scale), QColor(item['background']))
    if item['shadow']:
        painter.save()
        painter.translate(5*scale, 5*scale)
        painter.fillPath(path, QColor(0, 0, 0, 170))
        painter.restore()
    if outline and item['stroke']:
        stroker = QPainterPathStroker()
        stroker.setWidth(outline*2)
        stroker.setJoinStyle(Qt.RoundJoin)
        painter.fillPath(stroker.createStroke(path), QColor(item['stroke']))
    painter.fillPath(path, QColor(item['color']))
