"""Shared rounded, non-grabbing pickers anchored to a host widget."""
from PySide6.QtCore import QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget
from ddm import theme


class AnchoredPopup(QWidget):
    def __init__(self, parent=None):
        # Native tool windows belong to a top-level owner, never a tile's
        # native child surface. This also avoids promoting transient ancestors.
        super().__init__(parent.window() if parent else None,
                         Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._anchor = None
        self._owner = None
        self._owner_dragging = False
        QApplication.instance().installEventFilter(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(theme.qcolor(theme.ELEVATED))
        painter.setPen(QPen(theme.qcolor(theme.BORDER), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),
                                theme.RADIUS_MD, theme.RADIUS_MD)

    def place_at(self, anchor):
        self._anchor = anchor
        self._owner = anchor.window()
        if self.parentWidget() is not self._owner:
            visible = self.isVisible()
            self.setParent(self._owner, self.windowFlags())
            if visible:self.show()
        screen = anchor.screen().availableGeometry()
        point = anchor.mapToGlobal(QPoint(anchor.width() // 2, 0))
        x = point.x() - self.width() // 2
        y = point.y() - self.height() - 6
        if y < screen.top():
            y = anchor.mapToGlobal(QPoint(0, anchor.height())).y() + 6
        self.move(max(screen.left(), min(x, screen.right()-self.width()+1)),
                  max(screen.top(), min(y, screen.bottom()-self.height()+1)))

    def open_at(self, button):
        self._owner_dragging = False
        self.place_at(button)
        self.show()
        self.raise_()

    def _inside(self, point):
        return self.geometry().contains(point)

    def shutdown(self):
        self.hide()
        QApplication.instance().removeEventFilter(self)
        self.deleteLater()

    def eventFilter(self, watched, event):
        if not self.isVisible(): return False
        kind = event.type()
        owner_event = watched is self._owner or self._owner and watched is self._owner.windowHandle()
        if owner_event and kind == QEvent.NonClientAreaMouseButtonPress and event.button() == Qt.LeftButton:
            self._owner_dragging = True
        elif kind in (QEvent.NonClientAreaMouseButtonRelease,QEvent.MouseButtonRelease):
            self._owner_dragging = False
        elif kind in (QEvent.Move, QEvent.Resize) and self._anchor:
            widget = self._anchor
            while widget is not None:
                if watched is widget:
                    self.place_at(self._anchor)
                    break
                widget = widget.parentWidget()
        elif kind == QEvent.MouseButtonPress:
            point = event.globalPosition().toPoint()
            on_anchor = self._anchor and self._anchor.rect().contains(self._anchor.mapFromGlobal(point))
            if not self._inside(point) and not on_anchor:
                self.hide()
        elif kind == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.hide()
            return True
        elif kind == QEvent.ApplicationDeactivate or (watched is self._owner and kind in (QEvent.Hide,QEvent.Close)):
            self.hide()
        # Outside clicks continue to the host, including its native title bar.
        return False


def picker_style():
    return f'''
        QToolButton#EmoticonButton, QPushButton#GiftCard {{
            background:transparent; color:{theme.TEXT1}; border:1px solid transparent;
            border-radius:{theme.RADIUS_SM}px; padding:1px; font-size:11px;
        }}
        QToolButton#EmoticonButton:hover, QToolButton#EmoticonButton:focus,
        QPushButton#GiftCard:hover, QPushButton#GiftCard:focus {{
            background:{theme.CONTENT_HOVER}; border-color:{theme.ACCENT};
        }}
        QToolButton#EmoticonButton:disabled, QPushButton#GiftCard:disabled {{color:{theme.TEXT3};}}
        QPushButton#GiftCard QLabel {{color:{theme.TEXT1};font-size:11px;}}
        QPushButton#GiftCard:disabled QLabel {{color:{theme.TEXT3};}}
        QTabBar::tab {{color:{theme.TEXT2}; background:transparent; padding:4px 8px;
            margin:2px; border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS_SM}px;}}
        QTabBar::tab:selected {{color:{theme.TEXT1};background:{theme.CONTENT_HOVER};border-color:{theme.ACCENT};}}
    '''
