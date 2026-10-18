"""Gift browsing and quantities; HTTP and account state stay in controllers."""
from pathlib import Path
import time
from PySide6.QtCore import QEvent,QPoint,QRect,QSize,Qt,QTimer,Signal
from PySide6.QtGui import QCursor,QPainter,QPen
from PySide6.QtWidgets import (QApplication,QGridLayout,QHBoxLayout,QLabel,QLineEdit,
    QPushButton,QScrollArea,QSizePolicy,QTabBar,QVBoxLayout,QWidget)
from ddm import config,theme
from .emoticons import _style_button
from .gift_api import format_battery
from .gift_controller import GiftPanelState
from .gift_media import GiftMediaLoader,GiftImageLabel

def _clip(widget,anchor,*,prefer_above=True):
    screen=anchor.screen().availableGeometry()
    point=anchor.mapToGlobal(QPoint(anchor.width(),0))
    x=point.x()-widget.width()
    y=point.y()-widget.height()-6 if prefer_above else point.y()+anchor.height()+6
    if y<screen.top():y=anchor.mapToGlobal(QPoint(0,anchor.height())).y()+6
    x=max(screen.left(),min(x,screen.right()-widget.width()+1))
    y=max(screen.top(),min(y,screen.bottom()-widget.height()+1))
    widget.move(x,y)

def _quantity_valid(item,count,wallet):
    return (type(count) is int and 0<count<=item.max_count and
        (item.allow_custom or count in item.counts) and not item.disabled_reason and
        (item.source=='bag' and count<=item.stock and (not item.expires_at or item.expires_at>int(time.time())) or
         item.source=='gold' and type(wallet) is int and item.price*count<=wallet))

class GiftButton(QPushButton):
    def __init__(self,parent=None):
        super().__init__(parent);_style_button(self);self.setFixedSize(28,28)
        self.setAccessibleName('选择直播间礼物');self.setToolTip('赠送礼物与背包礼物')
    def paintEvent(self,event):
        super().paintEvent(event)
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width()/2,self.height()/2)
        painter.setPen(QPen(theme.qcolor(theme.TEXT2 if self.isEnabled() else theme.TEXT4),1.6))
        painter.drawRect(-7,-2,14,9);painter.drawRect(-8,-5,16,4)
        painter.drawLine(0,-5,0,7);painter.drawEllipse(-5,-9,5,4);painter.drawEllipse(0,-9,5,4)

class GiftCard(QPushButton):
    inspected=Signal(object)
    chosen=Signal(object)
    def __init__(self,item,parent=None):
        super().__init__(parent);self.item=item;self.setObjectName('GiftCard')
        self.setMinimumWidth(56);self.setFixedHeight(90);self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        self.setAccessibleName(item.name);self.setCursor(Qt.PointingHandCursor)
        row=QVBoxLayout(self);row.setContentsMargins(2,2,2,2);row.setSpacing(1)
        self.image=GiftImageLabel();self.image.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.image,0,Qt.AlignHCenter)
        self.name_label=QLabel(item.name);self.name_label.setMinimumWidth(0)
        self.name_label.setAlignment(Qt.AlignCenter);self.name_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.price_label=QLabel(f'库存 {item.stock}' if item.source=='bag' else f'{format_battery(item.price)} 电池')
        self.price_label.setAlignment(Qt.AlignCenter);self.price_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.name_label);row.addWidget(self.price_label)
        expiry=time.strftime('%Y-%m-%d %H:%M',time.localtime(item.expires_at)) if item.expires_at else '无到期时间'
        self.setToolTip(item.disabled_reason or (f'{item.name}：库存 {item.stock}，{expiry}' if item.source=='bag' else item.name))
        self.clicked.connect(lambda:self.chosen.emit(self))
    def enterEvent(self,event):
        if self.isEnabled() and (self.item.counts!=(1,) or self.item.allow_custom):self.inspected.emit(self)
        super().enterEvent(event)

class GiftQuantityPopup(QWidget):
    sendRequested=Signal(str,int)
    def __init__(self,parent=None):
        super().__init__(parent,Qt.Tool|Qt.FramelessWindowHint)
        self.item=None;self.wallet=None;self.sending=False
        self.setObjectName('GiftQuantityPopup');self.setAccessibleName('礼物数量')
        self.row=QVBoxLayout(self);self.row.setContentsMargins(10,10,10,10)
        self.title=QLabel();self.row.addWidget(self.title)
        self.options=QWidget();self.option_grid=QGridLayout(self.options);self.option_grid.setContentsMargins(0,0,0,0)
        self.row.addWidget(self.options)
        self.edit=QLineEdit('1');self.edit.setAccessibleName('礼物数量');self.edit.textChanged.connect(self._validate)
        self.row.addWidget(self.edit);self.total=QLabel();self.row.addWidget(self.total)
        self.send_button=QPushButton('赠送');self.send_button.clicked.connect(self._send);self.row.addWidget(self.send_button)
        self.setFixedWidth(220)
    def set_item(self,item,wallet,sending=False):
        self.item,self.wallet,self.sending=item,wallet,sending
        self.title.setText(item.name)
        while self.option_grid.count():
            child=self.option_grid.takeAt(0).widget()
            if child:child.deleteLater()
        for index,count in enumerate(item.counts):
            button=QPushButton(str(count));button.clicked.connect(lambda checked=False,n=count:self.edit.setText(str(n)))
            self.option_grid.addWidget(button,index//4,index%4)
        self.edit.setReadOnly(not item.allow_custom)
        self.edit.setText(str(item.counts[0] if item.counts else 1));self._validate();self.adjustSize()
    def _count(self):
        text=self.edit.text()
        return int(text) if text.isascii() and text.isdigit() and len(text)<10 else 0
    def _validate(self):
        if self.item is None:return
        count=self._count();valid=_quantity_valid(self.item,count,self.wallet)
        self.send_button.setEnabled(valid and not self.sending)
        self.total.setText(f'共 {format_battery(self.item.price*count)} 电池' if self.item.source=='gold' else f'库存 {self.item.stock}')
    def _send(self):
        if self.send_button.isEnabled():self.sendRequested.emit(self.item.key,self._count())

class GiftPopup(QWidget):
    sendRequested=Signal(str,int)
    refreshRequested=Signal()
    closed=Signal()
    def __init__(self,parent=None,*,cache_dir=None):
        super().__init__(parent,Qt.Tool|Qt.FramelessWindowHint)
        self.setObjectName('GiftPopup');self.setAccessibleName('直播间礼物')
        self.state=GiftPanelState();self._tabs=();self._cards=[];self._selected=''
        self._armed=False;self._closed=False;self._anchor=None;self._outside_since=None
        self._generation=0;self._media_urls={};self._loaded={};self.columns=5
        self.quantity_popup=GiftQuantityPopup(self)
        self.quantity_popup.sendRequested.connect(self.sendRequested)
        self.media=GiftMediaLoader(cache_dir or Path(config.REPO)/'cache/bili_live/gifts',self)
        self.media.loaded.connect(self._media_loaded);self.media.failed.connect(self._media_failed)
        row=QVBoxLayout(self);row.setContentsMargins(10,10,10,10);row.setSpacing(5)
        header=QHBoxLayout();self.wallet_label=QLabel('剩余电池：未知');header.addWidget(self.wallet_label,1)
        self.refresh_button=QPushButton('刷新');self.refresh_button.clicked.connect(self.refreshRequested);header.addWidget(self.refresh_button)
        row.addLayout(header);self.status=QLabel();self.status.setWordWrap(True);row.addWidget(self.status)
        self.tabs=QTabBar();self.tabs.setExpanding(False);self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(self._select);row.addWidget(self.tabs)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QScrollArea.NoFrame)
        row.addWidget(self.scroll,1)
        self.body=QWidget();self.grid=QGridLayout(self.body);self.grid.setContentsMargins(0,0,0,0)
        self.grid.setSpacing(3);self.grid.setAlignment(Qt.AlignTop);self.scroll.setWidget(self.body)
        self.scroll.verticalScrollBar().valueChanged.connect(self._visible_media)
        self._pointer=QTimer(self);self._pointer.setInterval(30);self._pointer.timeout.connect(self._check_pointer)
        self.resize(400,340);QApplication.instance().installEventFilter(self)
        self._theme()
    def _theme(self):
        self.setStyleSheet(f'QWidget#GiftPopup,QWidget#GiftQuantityPopup {{background:{theme.BG};color:{theme.TEXT1};border:1px solid {theme.BORDER};}} '
            f'QPushButton#GiftCard {{background:transparent;border:1px solid transparent;border-radius:{theme.RADIUS_SM}px;}} '
            f'QPushButton#GiftCard:hover {{border-color:{theme.ACCENT};background:{theme.CONTENT_HOVER};}}')
    def set_state(self,state):
        if self._closed:return
        self.state=state
        self.wallet_label.setText('剩余电池：'+('未知' if state.wallet is None else format_battery(state.wallet)))
        errors=[x for x in (state.catalog_error,state.wallet_error,state.bag_error) if x]
        status=[state.feedback,*errors]
        if state.loading:status.append('正在加载：'+ '、'.join({'catalog':'礼物','wallet':'余额','bag':'背包'}[x] for x in sorted(state.loading)))
        self.status.setText('；'.join(x for x in status if x) or '选择礼物')
        if state.tabs!=self._tabs:
            self._tabs=state.tabs;self.tabs.blockSignals(True)
            while self.tabs.count():self.tabs.removeTab(0)
            for tab in state.tabs:self.tabs.addTab(tab.title)
            selected=next((i for i,t in enumerate(state.tabs) if t.key==self._selected),0)
            self.tabs.setCurrentIndex(selected);self.tabs.blockSignals(False);self._select(selected)
        for card in self._cards:
            valid=not card.item.disabled_reason and card.item.source in ('gold','bag')
            card.setEnabled(valid and not state.sending and (card.item.source=='bag' or state.wallet is not None))
        q=self.quantity_popup
        if q.item:
            q.wallet,q.sending=state.wallet,state.sending;q._validate()
        self._check_pointer()
    def _select(self,index):
        self.quantity_popup.hide();self.media.cancel();self._generation+=1;self._media_urls.clear()
        while self.grid.count():
            card=self.grid.takeAt(0).widget()
            if card:
                card.image.set_file('');card.hide();card.deleteLater()
        self._cards=[]
        if 0<=index<len(self._tabs):
            tab=self._tabs[index];self._selected=tab.key
            for item in tab.items:
                card=GiftCard(item,self.body);card.chosen.connect(self._choose);card.inspected.connect(self._quantity)
                card.setEnabled(not item.disabled_reason and not self.state.sending and
                    item.source in ('gold','bag') and (item.source=='bag' or self.state.wallet is not None))
                self._cards.append(card)
        self._arrange()
    def _arrange(self):
        self.columns=max(1,min(5,(self.scroll.viewport().width()+3)//70))
        for index,card in enumerate(self._cards):
            self.grid.addWidget(card,index//self.columns,index%self.columns);card.show()
        QTimer.singleShot(0,self._visible_media)
    def _choose(self,card):
        if not card.isEnabled():return
        item=card.item
        if item.counts==(1,) and not item.allow_custom:
            if _quantity_valid(item,1,self.state.wallet):self.sendRequested.emit(item.key,1)
        else:self._quantity(card)
    def _quantity(self,card):
        if not self.isVisible() or not card.isEnabled():return
        if card.item.counts==(1,) and not card.item.allow_custom:return
        q=self.quantity_popup
        q.set_item(card.item,self.state.wallet,self.state.sending)
        screen=card.screen().availableGeometry();q.setMaximumSize(screen.size())
        _clip(q,card);q.show();q.raise_();self._outside_since=None
    def _visible_media(self,*args):
        if not self.isVisible() or self._closed:return
        urls={};viewport=self.scroll.viewport()
        for card in self._cards:
            rect=QRect(card.mapTo(viewport,QPoint()),card.size())
            visible=viewport.rect().intersects(rect)
            card.image.set_playing(visible)
            if visible:
                url=card.item.gif_url or card.item.image_url
                if url:
                    urls.setdefault(url,[]).append(card)
                    if url in self._loaded:card.image.set_file(self._loaded[url])
        if set(urls)!=set(self._media_urls):
            self._media_urls=urls
            self.media.request(tuple(u for u in urls if u not in self._loaded),self._generation)
    def _media_loaded(self,generation,url,path):
        if generation!=self._generation or not self.isVisible():return
        self._loaded[url]=path
        for card in self._media_urls.get(url,()):card.image.set_file(path)
    def _media_failed(self,generation,url):
        if generation!=self._generation or not self.isVisible():return
        fallbacks=[]
        for card in self._media_urls.get(url,()):
            fallback=card.item.image_url
            if fallback and fallback!=url:
                self._media_urls.setdefault(fallback,[]).append(card);fallbacks.append(fallback)
        if fallbacks:self.media.request(tuple(dict.fromkeys(fallbacks)),generation)
    def open_at(self,button):
        if self._closed:return
        if self.isVisible():self.hide();return
        self._anchor=button;self._armed=False;self._outside_since=None;self._theme()
        screen=button.screen().availableGeometry();self.resize(min(400,screen.width()),min(340,screen.height()))
        _clip(self,button);self.show();self.raise_();self._arrange();self._pointer.start()
    def mark_sent(self,result):
        if not self.isVisible() or self._closed:return
        self._armed=True;self._outside_since=None
        if not self.state.sending and not self._inside(QCursor.pos()):self.hide()
    def _inside(self,point):
        return (self.geometry().contains(point) or
            self.quantity_popup.isVisible() and self.quantity_popup.geometry().contains(point))
    def _check_pointer(self):
        if not self.isVisible() or not self._armed or self.state.sending:
            self._outside_since=None;return
        if self._inside(QCursor.pos()):self._outside_since=None;return
        now=time.monotonic()
        if self._outside_since is None:self._outside_since=now
        elif now-self._outside_since>=.15:self.hide()
    def eventFilter(self,watched,event):
        if not self.isVisible():return False
        if event.type()==QEvent.KeyPress and event.key()==Qt.Key_Escape:
            self.hide();return True
        if event.type()==QEvent.MouseButtonPress:
            point=event.globalPosition().toPoint()
            anchor=self._anchor
            on_anchor=anchor and anchor.rect().contains(anchor.mapFromGlobal(point))
            if not self._inside(point) and not on_anchor:self.hide()
        return False
    def hideEvent(self,event):
        self.quantity_popup.hide();self._pointer.stop();self.media.cancel()
        self._media_urls.clear()
        for card in self._cards:card.image.set_playing(False)
        self.closed.emit();super().hideEvent(event)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'scroll'):self._arrange()
    def interaction_active(self):return self.isVisible() or self.quantity_popup.isVisible()
    def shutdown(self):
        if self._closed:return
        self.hide();self._closed=True;self.media.shutdown()
        for card in self._cards:card.image.set_file('')
        QApplication.instance().removeEventFilter(self)
