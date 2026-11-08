"""Rotated selection geometry shared by video and still-image editors."""
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QTransform


def geometry(item, frame):
    width, height = item['w']*frame.width(), item['h']*frame.height()
    center = QPointF(frame.left()+(item['x']+item['w']/2)*frame.width(),
                     frame.top()+(item['y']+item['h']/2)*frame.height())
    transform = QTransform().translate(center.x(), center.y()).rotate(item.get('rotation', 0))
    corners = [transform.map(QPointF(x*width/2, y*height/2)) for x, y in [(-1, -1), (1, -1), (1, 1), (-1, 1)]]
    rotate = transform.map(QPointF(0, -height/2-24))
    return transform, QRectF(-width/2, -height/2, width, height), corners, rotate
