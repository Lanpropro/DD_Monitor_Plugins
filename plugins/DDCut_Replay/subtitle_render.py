"""Caption frames shared by project previews and final export."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QTextLayout, QTextOption
from ddm import theme
from .subtitles import default_style, placed_cues


class SubtitleGraphics:
    def __init__(self, project, width, height):
        self.cues = placed_cues(project)
        self.style = {**default_style(), **project.get('subtitle_style', {})}
        self.width, self.height = width, height

    def frame_at(self, stamp):
        image = QImage(self.width, self.height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        content = '\n'.join(cue['text'] for cue, start, end in self.cues if start <= stamp < end and cue['text'].strip())
        if not content:
            return image
        style = self.style
        font = QFont(style['font'] or theme.danmaku_font_family())
        font.setPixelSize(max(1, round(style['size']*self.height/1080)))
        font.setBold(bool(style['bold']))
        font.setItalic(bool(style['italic']))
        metrics = QFontMetricsF(font)
        lines = []
        for paragraph in content.splitlines():
            layout = QTextLayout(paragraph or ' ', font)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            layout.setTextOption(option)
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(self.width*.9)
                lines.append((paragraph[line.textStart():line.textStart()+line.textLength()], line.naturalTextWidth()))
            layout.endLayout()
        spacing = metrics.height()*1.12
        height = len(lines)*spacing
        margin = self.height*style['margin']/100
        y = margin if style['position'] == 'top' else (self.height-height)/2 if style['position'] == 'middle' else self.height-margin-height
        path = QPainterPath()
        for index, (text, width) in enumerate(lines):
            path.addText(QPointF((self.width-width)/2, y+metrics.ascent()+index*spacing), font, text)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        if style['background']:
            painter.fillRect(path.boundingRect().adjusted(-4, -2, 4, 3), QColor(0, 0, 0, 170))
        outline = style['outline']*self.height/1080
        painter.setPen(QPen(QColor(style['outline_color']), outline*2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin) if outline else Qt.NoPen)
        painter.setBrush(QColor(style['color']))
        painter.drawPath(path)
        painter.end()
        return image
