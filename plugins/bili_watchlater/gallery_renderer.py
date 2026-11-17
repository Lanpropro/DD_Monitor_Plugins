"""Gallery materialization, paging and visible-image coordination; Qt widgets stay under Page."""
from __future__ import annotations
from typing import Any
from PySide6.QtCore import QObject, QCoreApplication, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QScrollArea
from .models import WatchLaterItem, FILTER_ALL, SORT_RECENT, filtered_items
from .preferences import CARD_WIDTHS
from .media_cards import GridCard, RowCard, multiselect_is_confirming
from .danmaku_count import DanmakuCountProvider
from .motion_trace import measure

COVER_REFETCH_DEBOUNCE_MS = 200
_VISIBLE_ROW_MARGIN = 2

# v1.0 首屏分页：642 行全量构建约 1.2 s GUI 线程开销、约 7000 个 QObject，
# 但首屏只看得到十几行。首次只物化 PAGE_INITIAL_ROWS 行，滚动接近底部时
# 再按 PAGE_GROW_ROWS 追加。数据层（搜索/筛选/排序/计数）仍是完整列表，
# 只是不把不可见的行做成 QWidget。改这两个常量即可调节首批/每批大小。
PAGE_INITIAL_ROWS = 50
PAGE_GROW_ROWS = 50
PAGE_PREFETCH_PX = 700

# v1.0 需求：：网格视图（默认 4 列，B 站客户端 4x4 观感），行随滚动分页追加。
# v1.0 需求 4：列数改为按可用宽度自适应 —— 目标单元格 240px，窗口越大列越多
# （1200px 出 5 列、1400px 出 6 列…），上下限 4/8 列。
GRID_COLUMNS = 3
GRID_MIN_COLUMNS = 2
GRID_MAX_COLUMNS = 6
GRID_TARGET_CELL_WIDTH = 300
# v1.0 需求 1：首屏只物化可视区能装下的卡片（980×660 默认窗口约 2~3 行，
# 每行 4 列 ≈ 8~12 张），从 32 降到 16，滚动接近底部再按 GRID_GROW_CARDS 补。
GRID_INITIAL_CARDS = 16
GRID_GROW_CARDS = 16
GRID_PREFETCH_PX = 640
GRID_KEEP_ROWS = 4
GRID_BUILD_BATCH = 4
GROW_SETTLE_MS = 60
GRID_VISIBLE_MARGIN = 1
GRID_COVER_BUDGET = 32

# v1.0 性能：冷启动全量构建时按批让出 GUI 线程。只在窗口可见时启用，
# 离屏测试/基准仍走完整同步路径，保证 _render() 返回时行列表完整。
ROW_BUILD_BATCH = 16
ROW_BUILD_YIELD_MIN = 64
# 网格冷首屏只有 32 张卡（低于 ROW_BUILD_YIELD_MIN），用更低的让出门槛
# 把首屏也分批，避免切到网格瞬间一次性建满 32 张卡卡顿。
GRID_BUILD_YIELD_MIN = 16


def grid_columns_for_width(
    viewport_width: int,
    *,
    target: int = GRID_TARGET_CELL_WIDTH,
    minimum: int = GRID_MIN_COLUMNS,
    maximum: int = GRID_MAX_COLUMNS,
) -> int:
    """v1.0 需求 4：按可用宽度算网格列数（纯函数，可单测）。

    以 ``target`` 为目标单元格宽度四舍五入，再夹到 ``[minimum, maximum]``：

    * 932px（980 默认窗口）→ 3 列；
    * 1218px（1280 窗口）→ 4 列；
    * 1373px（1440 窗口）→ 5 列。

    只用整数运算，避免 ``round(4.5) == 4`` 这种银行家舍入在临界点上跳变。
    """
    try:
        width = int(viewport_width)
    except (TypeError, ValueError):
        width = 0
    target = max(1, int(target))
    lowest = max(1, int(minimum))
    highest = max(lowest, int(maximum))
    if width <= 0:
        return lowest
    columns = (width + target // 2) // target
    return max(lowest, min(highest, columns))


def first_visible_index(views: Any, offset: int) -> int:
    """v1.0 需求：：视口顶部第一个可见条目的下标。

    ``views`` 按显示顺序排列，且 bottom 单调递增（列表单列、网格逐行），
    所以用二分而不是遍历。切换视图时把这一条目当作锚点，比按滚动比例
    换算更准：列表一行一个、网格一行四个，条目密度差太多，比例会漂。
    列表为空返回 0；全部在上面时返回最后一个下标。
    """
    items = list(views or ())
    if not items:
        return 0
    offset = max(0, int(offset))
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi) // 2
        # bottom == offset 时该条已经完整滚出视口，锚点要取它下面那条。
        if items[mid].geometry().bottom() <= offset:
            lo = mid + 1
        else:
            hi = mid
    return min(lo, len(items) - 1)



class GalleryRenderer(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self._rows: list[RowCard] = []
        self._rows_by_key: dict[str, RowCard] = {}
        self._rows_by_cover: dict[str, list[RowCard]] = {}
        self._cards: list[GridCard] = []
        self._cards_by_key: dict[str, GridCard] = {}
        self._cards_by_cover: dict[str, list[GridCard]] = {}
        self._cover_views: set[RowCard | GridCard] = set()
        self._grid_columns = GRID_COLUMNS
        self._grid_cell_size: tuple[int, int] | None = None
        self._filter_cache: tuple[tuple, list[WatchLaterItem]] | None = None
        self._scroll_refetch_connected = False
        self._building_rows = False
        self._render_pending = False
        self._render_limit = self.initial_limit()
        self._render_total = 0
        self._grow_pending = False
        self._grow_busy = False
        self._avatars_by_url = {}
        self._danmaku: DanmakuCountProvider | None = None

    def active_scroll(self) -> QScrollArea:
        return self.page.grid_scroll if self.page._view_mode == "grid" else self.page.list_scroll

    def render_after_remove(self) -> None:
        """删除成功后原地重排：保留分页与滚动位置，且只重绘一次。

        旧实现直接 ``_render()``，``_items_version`` 一变就把 ``_render_limit``
        打回首屏（列表 50 行 / 网格 16 张）：滚动很深时几百个控件被
        deleteLater、滚动条 maximum 骤降，整页跳一下并逐行闪。现在：
        * ``keep_position=True`` → 不回退分页、不清隐藏视图的封面；
        * 重建期间 ``setUpdatesEnabled(False)`` → Qt 只在结束时统一重绘；
        * 最后把滚动值夹回新的 maximum，保持原来的视觉锚点。
        多选删除串行推进时每条都走这里，不会越删越回到顶部。
        """
        if self.page.list_controller.closing:
            return
        try:
            scroll = self.active_scroll()
            bar = scroll.verticalScrollBar()
            viewport = scroll.viewport()
        except (AttributeError, RuntimeError):
            self.render(keep_position=True)
            return
        saved = bar.value()
        viewport.setUpdatesEnabled(False)
        try:
            self.render(keep_position=True)
            bar.setValue(max(bar.minimum(), min(saved, bar.maximum())))
        finally:
            viewport.setUpdatesEnabled(True)

    def initial_limit(self) -> int:
        return GRID_INITIAL_CARDS if self.page._view_mode == "grid" else PAGE_INITIAL_ROWS

    def visible_anchor_key(self) -> str:
        """v1.0 需求：：当前视口顶部那条视频的 key（切换视图的定位锚点）。

        选「首个可见条目的 key」而不是「滚动比例」：列表一行一条、网格一行
        四条，同一比例在两套布局里落在完全不同的条目上；按 key 定位能把
        同一条视频摆回视口顶部，列表↔网格肉眼对得上。
        """
        bar = self.active_scroll().verticalScrollBar()
        grid = self.page._view_mode == "grid"
        views = self._cards if grid else self._rows
        if not views:
            return ""
        index = first_visible_index(views, bar.value())
        return str(getattr(views[index].item, "key", "") or "")

    def ensure_anchor_materialized(self, key: str) -> None:
        """把分页窗口对到锚点条目，保证切换后它真的存在（否则无从定位）。

        窗口不够就扩到锚点；远远超出（列表滚到第 300 行再切网格）就收敛到
        锚点 + 一屏 —— 网格卡片比行重得多，没必要为了一个锚点重建 300 张。
        """
        if not key:
            return
        cached = self._filter_cache
        selected = cached[1] if cached is not None else self.page.list_controller.items
        for index, item in enumerate(selected):
            if item.key == key:
                need = index + 1 + self.initial_limit()
                if self._render_limit < need or self._render_limit > 2 * need:
                    self._render_limit = need
                return

    def restore_anchor(self, key: str) -> bool:
        """把 key 对应的控件滚到视口顶部；找不到时保持当前滚动值。"""
        if not key:
            return False
        grid = self.page._view_mode == "grid"
        index = self._cards_by_key if grid else self._rows_by_key
        view = index.get(key)
        if view is None:
            return False
        scroll = self.active_scroll()
        # 内容高度要立刻落定：QScrollArea 的 widgetResizable 是延后一轮布局
        # 才更新滚动范围的，否则这里的 setValue 会被旧上限截断（实测切回
        # 列表时会少滚 56px，锚点掉到上一条）。
        try:
            layout = scroll.widget().layout()
            if layout is not None:
                layout.activate()
                scroll.widget().adjustSize()
        except RuntimeError:
            pass
        top = view.geometry().top()
        views = self._cards if grid else self._rows
        if top <= 0 and views and view is not views[0]:
            # 布局还没生效（首帧）时所有 geometry 都是 0；宁可不滚，也不要
            # 在用户在列表中间时把他弹回顶部。
            return False
        scroll.verticalScrollBar().setValue(max(0, top))
        return True

    @measure('grid_layout')
    def fit_grid(self) -> None:
        """v1.0 需求 4：按 viewport 宽度重算列数 + 单元格尺寸（去抖后调用）。

        列数变了只**重排**已有的卡片（不复用重建：卡片本身、封面缓存、悬停
        状态都保留），并用「首个可见条目 key」把滚动位置钉回去；列数没变且
        单元格尺寸也没变时原地返回，滚动/拖动窗口不会做无谓的布局工作。
        """
        if self.page.list_controller.closing or self._building_rows or not self._cards or self.page._view_mode != "grid":
            return
        viewport = self.page.grid_scroll.viewport()
        gap = self.page.grid_layout.spacing()
        columns = self.page.preferences['grid_columns'] or grid_columns_for_width(
            viewport.width(), target=CARD_WIDTHS[self.page.preferences['card_size']])
        columns_changed = columns != self._grid_columns
        # 先记锚点再重排：重排会改所有卡片的行列，滚动值就没意义了。
        anchor = self.visible_anchor_key() if columns_changed else ""
        if columns_changed:
            self.apply_grid_columns(columns)
        width = max(
            80, (viewport.width() - 4 - (columns - 1) * gap) // columns
        )
        # Card height follows its 16:9 cover, independent of viewport height.
        height = round(width * 9 / 16) + 140
        size = (width, height)
        if size != self._grid_cell_size or columns_changed:
            self._grid_cell_size = size
            for card in self._cards:
                card.fit_cell(width, height)
            self.page.grid_layout.activate()
        if columns_changed:
            # 卡片新尺寸生效后再把锚点那条视频钉回视口顶部。
            self.restore_anchor(anchor)
        self.page._update_fabs()
        self.schedule_visible_covers()

    def apply_grid_columns(self, columns: int) -> None:
        """把 ``self._cards`` 按新列数重新落位（只 removeWidget + addWidget）。

        全程不 destroy 任何卡片，也不重设样式表；QGridLayout 对已经 parent 在
        同一容器里的控件重新 addWidget 只是改位置，代价远低于重建。
        """
        columns = max(1, int(columns))
        self._grid_columns = columns
        for col in range(max(columns, GRID_MAX_COLUMNS)):
            self.page.grid_layout.setColumnStretch(col, 1 if col < columns else 0)
        for index, card in enumerate(self._cards):
            row, col = divmod(index, columns)
            slot = self.page.grid_layout.itemAtPosition(row, col)
            if slot is not None and slot.widget() is card:
                continue
            self.page.grid_layout.addWidget(card, row, col)

    @measure('render')
    def render(self, reset_scroll: bool = False, *,
                keep_position: bool = False) -> None:
        """重排当前视图。

        ``keep_position`` 用于「删掉一条」这类原地小改：数据版本变了但
        用户的滚动位置、已展开的分页量、隐藏视图里的封面都应保留，否则
        几百个控件会被销毁重建、滚动条范围骤降，整页跳一下并逐行闪。
        """
        if self.page.list_controller.closing:
            return
        if self._building_rows:
            # 分批让出期间被定时器触发的重入：延后到构建完成后统一重排，
            # 避免两个 _rebuild_rows 同时改写 _rows / _rows_by_key。
            self._render_pending = True
            return
        self.page.browse_state.validate_account()
        total = len(self.page.list_controller.items)
        if not total:
            self.clear_rows()
            if self.page.list_controller.loaded_once:
                self.page.empty_view.set_state(
                    "稍后再看是空的",
                    "在 B 站点击“稍后再看”后，视频会出现在这里。",
                    primary="打开 B 站",
                )
                self.page.stack.setCurrentWidget(self.page.empty_view)
                self.page._update_header(0, 0)
            else:
                self.page.stack.setCurrentWidget(self.page.loading_view)
            self.page._update_destructive_controls()
            return

        query = self.page.search.text()
        filter_mode = self.page.filter_combo.currentData() or FILTER_ALL
        sort_mode = self.page.sort_combo.currentData() or SORT_RECENT
        # Cache the filtered/sorted view: repeated renders with unchanged inputs
        # (cover deliveries, status flashes, destructive-control refreshes) used
        # to re-run filter+sort over the whole list every time.
        signature = (query, filter_mode, sort_mode, self.page.list_controller.items_version)
        cached = self._filter_cache
        prev_signature = cached[0] if cached is not None else None
        if cached is not None and cached[0] == signature:
            selected = cached[1]
        else:
            selected = filtered_items(
                self.page.list_controller.items, query, filter_mode, sort_mode
            )
            self._filter_cache = (signature, selected)
        # v1.0：新查询（搜索/筛选/排序/数据变化）回到首屏；滚动追加时
        # signature 不变，_render_limit 保持已展开的行数，不重复回退。
        # 删除一条只让筛选结果少一项，不该把用户已经展开的分页打回首屏。
        if reset_scroll or (prev_signature != signature and not keep_position):
            self._render_limit = self.initial_limit()
        shown = len(selected)
        self._render_total = shown
        filtered = bool(query.strip()) or filter_mode != FILTER_ALL
        self.page._update_header(shown, total, filtered)
        if not shown:
            self.clear_rows()
            self.page.noresult_view.set_state(
                "没有匹配的视频",
                "试试更换关键词，或清除当前筛选条件。",
                primary="清除搜索/筛选",
            )
            self.page.stack.setCurrentWidget(self.page.noresult_view)
            self.page._update_destructive_controls()
            return

        # 隐藏视图的清理会清掉里面所有卡片的封面并 deleteLater 首批之外的
        # 控件；删除一条时跳过它，避免下次切换视图看到一片重新加载。
        if not keep_position:
            self.prune_inactive_view(selected)
        self.page.browse_state.materialize()
        if self.page._view_mode == "grid":
            self.rebuild_cards(selected)
        else:
            self.rebuild_rows(selected)
        scroll = self.active_scroll()
        self.page.stack.setCurrentWidget(scroll)
        scroll.widget().layout().activate()
        if self.page._view_mode == "grid":
            self.fit_grid()
        self.page._update_destructive_controls()
        if reset_scroll:
            scroll.verticalScrollBar().setValue(0)
        self.page.browse_state.restore()
        self.page._update_fabs()
        # 分批构建期间被跳过的可见区补取，在整页落定后再排一次。
        if self.page._visible:
            self.schedule_visible_covers()

    def release_covers(self) -> None:
        self.page.card_preview.stop()
        for view in self._cover_views:
            view.cover.set_pixmap(None)
            if isinstance(view, GridCard):
                view.avatar.set_pixmap(None)
        self._cover_views.clear()
        self._avatars_by_url.clear()

    def drop_view(self, view, layout) -> None:
        if self.page.card_preview.target is view.cover:
            self.page.card_preview.stop()
        self._cover_views.discard(view)
        layout.removeWidget(view)
        view.hide()
        view.cover.set_pixmap(None)
        if isinstance(view, GridCard):
            view.avatar.set_pixmap(None)
        view.deleteLater()

    def prune_inactive_view(self, items: list[WatchLaterItem]) -> None:
        """隐藏视图只保留首批可复用控件；不创建，也不持有历史封面。"""
        grid = self.page._view_mode != "grid"
        views = self._cards if grid else self._rows
        index = self._cards_by_key if grid else self._rows_by_key
        layout = self.page.grid_layout if grid else self.page.list_layout
        limit = GRID_INITIAL_CARDS if grid else PAGE_INITIAL_ROWS
        wanted = {item.key: item for item in items[:limit]}
        for key in list(index):
            view = index[key]
            if key not in wanted:
                self.drop_view(index.pop(key), layout)
            else:
                view.update_item(wanted[key])
                view.cover.set_pixmap(None)
                if isinstance(view, GridCard):
                    view.avatar.set_pixmap(None)
                self._cover_views.discard(view)
        views[:] = [view for view in views if view.item.key in index]
        covers = self._cards_by_cover if grid else self._rows_by_cover
        covers.clear()
        for view in views:
            if view.item.cover_url:
                covers.setdefault(view.item.cover_url, []).append(view)

    def clear_rows(self) -> None:
        self.release_covers()
        while self.page.grid_layout.count():
            entry = self.page.grid_layout.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._cards.clear()
        self._cards_by_key.clear()
        self._cards_by_cover.clear()
        self._grid_cell_size = None
        self._grid_resize_timer.stop()
        self._cover_scroll_timer.stop()
        self._grow_settle_timer.stop()
        self._filter_cache = None
        while self.page.list_layout.count():
            item = self.page.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._rows.clear()
        self._rows_by_key.clear()
        self._rows_by_cover.clear()
        self.page._destructive_ready = None
        # v1.0：清空列表时回到首屏分页，避免下一批数据沿用旧的展开量。
        self._render_limit = self.initial_limit()
        self._render_total = 0
        self._grow_pending = False
        self.page.list_layout.addStretch(1)

    def make_row(self, item: WatchLaterItem) -> RowCard:
        row = RowCard(
            item,
            self.page.covers.cached,
            self.page._remove_reason,
            self.page.list_widget,
            lazy_cover=True,
        )
        row.open_requested.connect(self.page._open_item)
        row.copy_requested.connect(self.page._copy_item)
        row.remove_requested.connect(self.page._confirm_remove)
        row.play_requested.connect(self.page._play_item)
        self.page.card_preview.watch(row.cover)
        row.selection_requested.connect(self.page._select_item)
        self.page._sync_card_action(row)
        row.double_play_requested.connect(self.page._on_double_play)
        row.selection_toggled.connect(self.page._on_multi_toggle)
        row.set_multi_mode(
            multiselect_is_confirming(self.page._multiselect_state),
            item.key in self.page._multi_selected_keys,
        )
        return row

    @measure('grid_build')
    def rebuild_cards(self, items: list[WatchLaterItem]) -> None:
        wanted = {item.key for item in items[:self._render_limit] if item.key}
        existing = self._cards_by_key
        for key in list(existing):
            if key not in wanted:
                self.drop_view(existing.pop(key), self.page.grid_layout)
        ordered = []
        visible_items = items[:self._render_limit]
        allow_yield = (
            self.page._visible
            and not self.page.list_controller.closing
            and len(visible_items) >= GRID_BUILD_YIELD_MIN
            and QApplication.instance() is not None
        )
        created = 0
        for item in visible_items:
            if not item.key:
                continue
            card = existing.get(item.key)
            if card is None:
                card = GridCard(item, self.page.covers.cached, self.page._remove_reason,
                                self.page.grid_widget, lazy_cover=True)
                card.open_requested.connect(self.page._open_item)
                card.copy_requested.connect(self.page._copy_item)
                card.remove_requested.connect(self.page._confirm_remove)
                card.play_requested.connect(self.page._play_item)
                self.page.card_preview.watch(card.cover)
                card.selection_requested.connect(self.page._select_item)
                self.page._sync_card_action(card)
                card.double_play_requested.connect(self.page._on_double_play)
                card.selection_toggled.connect(self.page._on_multi_toggle)
                existing[item.key] = card
                if self._grid_cell_size is not None:
                    card.fit_cell(*self._grid_cell_size)
                created += 1
                if allow_yield and created % GRID_BUILD_BATCH == 0:
                    self.yield_during_build()
            else:
                card.update_item(item)
            position = len(ordered)
            row, col = divmod(position, self._grid_columns)
            slot = self.page.grid_layout.itemAtPosition(row, col)
            if slot is None or slot.widget() is not card:
                self.page.grid_layout.addWidget(card, row, col)
            card.show()
            ordered.append(card)
        self._cards = ordered
        self._cards_by_cover = {}
        multi_active = multiselect_is_confirming(self.page._multiselect_state)
        for card in ordered:
            card.set_multi_mode(
                multi_active, card.item.key in self.page._multi_selected_keys
            )
            if card.item.cover_url:
                self._cards_by_cover.setdefault(card.item.cover_url, []).append(card)
            if self._danmaku is not None:
                if card.item.danmaku_count is None:
                    count = self._danmaku.cached(card.item.bvid)
                    if count is not None:
                        card.set_danmaku_count(count)
                if card.item.view_count is None:
                    views = self._danmaku.cached_view(card.item.bvid)
                    if views is not None:
                        card.set_view_count(views)
        self.page._destructive_ready = None

    @measure('list_build')
    def rebuild_rows(self, items: list[WatchLaterItem]) -> None:
        """Reuse row widgets keyed by item key; only build what is new.

        v1 destroyed and rebuilt every RowCard on each search keystroke.  That
        cost ~0.5 ms per card, so a 673-item list paid ~350 ms per keystroke.
        Reusing by key keeps the cost proportional to the change, not the list.

        v1.0: a cold first paint still creates every RowCard in one go
        (~0.8 s with 641 rows).  While the window is visible we yield to the
        event loop every ROW_BUILD_BATCH new rows, so Qt can paint and respond;
        the call stays synchronous, so _rows is complete on return.

        v1.0: yielding only spreads the freeze out, it does not remove the
        work.  We now materialize just the first _render_limit entries and
        append more batches as the user approaches the bottom of the list.
        _rows therefore contains only the rows that actually exist as widgets.
        """
        # 数据层 items 仍是完整筛选结果；只有这段窗口会被做成 QWidget。
        visible_items = items[: min(len(items), self._render_limit)]
        wanted = {item.key: item for item in visible_items if item.key}
        existing = self._rows_by_key

        # Reuse rows whose item is still present; drop the rest.
        stale = [key for key in existing if key not in wanted]
        for key in stale:
            self.drop_view(existing.pop(key), self.page.list_layout)

        allow_yield = (
            self.page._visible
            and not self.page.list_controller.closing
            and len(visible_items) >= ROW_BUILD_YIELD_MIN
            and QApplication.instance() is not None
        )
        ordered: list[RowCard] = []
        created = 0
        for item in visible_items:
            key = item.key
            row = existing.get(key) if key else None
            if row is None:
                if not key:
                    continue
                row = self.make_row(item)
                existing[key] = row
                created += 1
                if allow_yield and created % ROW_BUILD_BATCH == 0:
                    self.yield_during_build()
            else:
                row.update_item(item)
            ordered.append(row)

        # Re-seat in display order; Qt keeps already-parented widgets cheap.
        for position, row in enumerate(ordered):
            current = self.page.list_layout.itemAt(position)
            if current is not None and current.widget() is row:
                continue
            self.page.list_layout.insertWidget(position, row)

        # Drop the trailing stretch that _clear_rows left behind, then re-add it.
        last = self.page.list_layout.count() - 1
        if last >= 0:
            tail = self.page.list_layout.itemAt(last)
            if tail is not None and tail.widget() is None:
                self.page.list_layout.takeAt(last)
        self.page.list_layout.addStretch(1)

        self._rows = ordered
        self._rows_by_cover = {}
        for row in ordered:
            cover = row.item.cover_url
            if cover:
                self._rows_by_cover.setdefault(cover, []).append(row)
        # v1.0 需求：：重建/复用行时，已缓存的弹幕数直接显示，不再等待信号。
        provider = self._danmaku
        if provider is not None:
            for row in ordered:
                if row.item.bvid and row.item.danmaku_count is None:
                    count = provider.cached(row.item.bvid)
                    if count is not None:
                        row.set_danmaku_count(count)
                # v1.0 需求：：播放量同样走缓存补齐。
                if row.item.bvid and row.item.view_count is None:
                    views = provider.cached_view(row.item.bvid)
                    if views is not None:
                        row.set_view_count(views)
        self.page._destructive_ready = None
        if self._render_pending:
            self._render_pending = False
            QTimer.singleShot(0, self.render)

    def yield_during_build(self) -> None:
        """Let Qt paint/respond between row batches.

        The _render() call stays synchronous: the complete row list is
        published when it returns.  Re-entrant renders are deferred via
        _render_pending instead of mutating half-built state.
        """
        self._building_rows = True
        try:
            try:
                QCoreApplication.processEvents(
                    QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 1
                )
            except (TypeError, RuntimeError):
                QCoreApplication.processEvents()
        finally:
            self._building_rows = False

    @measure('cover_ready')
    def cover_ready(self, url: str) -> None:
        if self.page.list_controller.closing or self._building_rows:
            return
        # v1 scanned every row per delivered cover (O(n) each).  Index by cover
        # URL instead, so a delivery touches only the rows that show it.
        index = self._cards_by_cover if self.page._view_mode == "grid" else self._rows_by_cover
        for row in index.get(url, ()):
            if row in self._cover_views and self.page._visible:
                row.refresh_cover()
        for row in self._avatars_by_url.get(url, ()):
            if row in self._cover_views and self.page._visible:
                row.refresh_avatar(self.page.covers.cached)
        # A delivery drains the pending queue by one.  Re-arm the debounced
        # visible-row check so rows that were dropped while the queue was full
        # get their get() retried once slots free up (no extra scroll needed).
        self.schedule_visible_covers()

    def danmaku_provider(self) -> DanmakuCountProvider | None:
        """v1.0 需求：：弹幕数提供器懒创建；shutdown 后不再重建。"""
        if self.page.list_controller.closing:
            return None
        if self._danmaku is None:
            try:
                self._danmaku = DanmakuCountProvider(self.page)
                self._danmaku.count_ready.connect(self.danmaku_ready)
                # v1.0 需求：：播放量与弹幕数同一次请求回来。
                self._danmaku.view_ready.connect(self.view_count_ready)
                if self.page._visible:
                    self._danmaku.resume()
            except Exception:
                self._danmaku = None
        return self._danmaku

    def danmaku_ready(self, bvid: str, count: int) -> None:
        if self.page.list_controller.closing:
            return
        # 两种视图可能共享同一 item；每个 meta 都要更新，不能首个命中就返回。
        for index in (self._rows_by_key, self._cards_by_key):
            view = index.get(bvid)
            if view is not None:
                view.set_danmaku_count(count)
        # 行已滚出/被筛选掉：写回模型供下次物化。
        for item in self.page.list_controller.items:
            if item.bvid == bvid:
                item.danmaku_count = count
                break

    def view_count_ready(self, bvid: str, count: object) -> None:
        """v1.0 需求：：播放量到位，只改 meta 行，不重建行/卡。"""
        if self.page.list_controller.closing or count is None:
            return
        try:
            value = max(0, int(count))
        except (TypeError, ValueError):
            return
        for index in (self._rows_by_key, self._cards_by_key):
            view = index.get(bvid)
            if view is not None:
                view.set_view_count(value)
        for item in self.page.list_controller.items:
            if item.bvid == bvid:
                item.view_count = value
                break

    def connect_scroll_refetch(self) -> None:
        if self._scroll_refetch_connected:
            return
        for scroll in (self.page.list_scroll, self.page.grid_scroll):
            scroll.verticalScrollBar().valueChanged.connect(self.schedule_visible_covers)
            scroll.verticalScrollBar().valueChanged.connect(self.page._update_fabs)
            scroll.verticalScrollBar().rangeChanged.connect(self.schedule_visible_covers)
        self._scroll_refetch_connected = True

    def disconnect_scroll_refetch(self) -> None:
        if not self._scroll_refetch_connected:
            return
        for scroll in (self.page.list_scroll, self.page.grid_scroll):
            bar = scroll.verticalScrollBar()
            for signal, slot in ((bar.valueChanged, self.schedule_visible_covers),
                                 (bar.valueChanged, self.page._update_fabs),
                                 (bar.rangeChanged, self.schedule_visible_covers)):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._scroll_refetch_connected = False

    def schedule_visible_covers(self, *_args) -> None:
        if self.page.list_controller.closing or not self.page._visible or self._building_rows or self.page._inline_panel is not None:
            return
        # Deliveries and scrollbar signals must not indefinitely reset refill.
        if not self._cover_scroll_timer.isActive():
            self._cover_scroll_timer.start(COVER_REFETCH_DEBOUNCE_MS)
        self.maybe_grow_page()

    def maybe_grow_page(self) -> None:
        """接近底部时排一次追加；**不重启**排队中的定时器（节流，不是去抖）。

        旧实现在每个 bar.valueChanged 里 ``timer.start()``：`start()` 会重置
        计时，于是只要还在滚，settle 就永远不会到期 —— 表现就是「滚到底就没了，
        必须停一下才长出下一批」，一边滚一边等永远等不到。现在改成节流：
        已经在排队就直接返回，让它到期后追加一批；持续滚动时每
        GROW_SETTLE_MS 追加一批，用户不必停手。

        成本可控的前提是 `_rebuild_cards` 已经按 GRID_BUILD_BATCH（4 张）分批
        让出事件循环，一批 16 张是 4 段 ~5ms，不会出现 20ms 的整块冻结。
        """
        if (
            self.page.list_controller.closing
            or not self.page._visible
            or self._building_rows
            or self._grow_busy
            or self._render_limit >= self._render_total
            or self.page.stack.currentWidget() is not self.active_scroll()
        ):
            return
        if self._grow_settle_timer.isActive():
            # 已有一批在排队：不要 restart（去抖会让"一直滚"永远等不到追加）。
            return
        bar = self.active_scroll().verticalScrollBar()
        threshold = GRID_PREFETCH_PX if self.page._view_mode == "grid" else PAGE_PREFETCH_PX
        if bar.maximum() - bar.value() > threshold:
            return
        self._grow_pending = True
        self._grow_settle_timer.start(GROW_SETTLE_MS)

    @measure('grow')
    def grow_page(self) -> None:
        """追加一批 RowCard；只有当前分页窗口内的条目会被物化。"""
        self._grow_pending = False
        if (
            self.page.list_controller.closing
            or not self.page._visible
            or self.page._inline_panel is not None
            or self._building_rows
            or self.page.stack.currentWidget() is not self.active_scroll()
            or self._render_limit >= self._render_total
        ):
            return
        bar = self.active_scroll().verticalScrollBar()
        threshold = GRID_PREFETCH_PX if self.page._view_mode == "grid" else PAGE_PREFETCH_PX
        if bar.maximum() - bar.value() > threshold:
            return
        self._grow_busy = True
        try:
            grow = GRID_GROW_CARDS if self.page._view_mode == "grid" else PAGE_GROW_ROWS
            self._render_limit = min(self._render_total, self._render_limit + grow)
            # keep_position=True：追加是"往下接一段"，不该顺手把隐藏视图里的
            # 卡片全部 prune 一遍（那是给删除一条准备的路径）。滚动中每 60ms
            # 追加一批，prune 会把隐藏视图的封面反复扔掉又装回来。
            self.render(keep_position=True)
        finally:
            self._grow_busy = False
        # 内容仍不足以产生滚动条时（超长窗口）继续补，避免用户永远无法
        # 触发追加；_render_limit 到达总数后自然停止，不会无限排队。
        # 需求 3：此刻并没有滚动发生，不必等 settle，直接 0ms 续摊
        # （改成直调 _grow_page，绕开 _maybe_grow_page 的 120ms 停稳闸门）。
        if (
            self._render_limit < self._render_total
            and bar.maximum() == 0
        ):
            QTimer.singleShot(0, self.grow_page)

    @measure('visible_covers')
    def refetch_visible_covers(self) -> None:
        """Re-run get() for the rows currently in (or just outside) the viewport.

        CoverLoader.get() drops a URL when its pending queue is full, so a
        quiet jump to a far region otherwise leaves permanent placeholders.
        Only a handful of visible rows is touched - never the whole list - so
        the MAX_PENDING memory guard is never re-filled in one pass.

        v1.0 滚动性能：「拉取带」与「保留带」分开。拉取带只有视口 ±1 行（真的
        要显示的），保留带是 ±GRID_KEEP_ROWS 行。旧实现用同一套 ±1 行同时
        `set_pixmap(None)`，于是滚过半屏就把刚加载好的封面全部释放，滚回来再
        逐张重建预合成位图（每张 ~0.35ms，一屏 24 张 ≈ 8ms），肉眼就是
        「之前加载好的封面又要重新加载一次，很卡」。现在只有离视口超过保留带
        的卡片才释放，正常来回滚不会再触发重建。
        """
        if self.page.list_controller.closing or not self.page._visible or self._building_rows or self.page._inline_panel is not None:
            return
        grid = self.page._view_mode == "grid"
        rows = self._cards if grid else self._rows
        scroll = self.active_scroll()
        if not rows or self.page.stack.currentWidget() is not scroll:
            self.release_covers()
            return
        viewport_rect = scroll.viewport().rect()
        if viewport_rect.isEmpty():
            return
        spacing = self.page.grid_layout.spacing() if grid else self.page.list_layout.spacing()
        stride = max(60, rows[0].geometry().height() + spacing)
        margin_rows = GRID_VISIBLE_MARGIN if grid else _VISIBLE_ROW_MARGIN
        keep_rows = GRID_KEEP_ROWS if grid else _VISIBLE_ROW_MARGIN + 2
        margin = stride * margin_rows
        offset = scroll.verticalScrollBar().value()
        band_top = offset - margin
        band_bottom = offset + viewport_rect.height() + margin

        def _first_row_at(top: int) -> int:
            """Rows are stacked top-to-bottom, so their bottoms are monotonic."""
            lo, hi = 0, len(rows)
            while lo < hi:
                mid = (lo + hi) // 2
                if rows[mid].geometry().bottom() < top:
                    lo = mid + 1
                else:
                    hi = mid
            return lo

        index = _first_row_at(band_top)
        # Hard cap also bounds the degenerate "layout not finished" case.
        budget = viewport_rect.height() // stride + 2 * margin_rows + 2
        if grid:
            budget = min(GRID_COVER_BUDGET, budget * self._grid_columns)
        visible = []
        while index < len(rows) and len(visible) < budget:
            row = rows[index]
            if row.geometry().top() > band_bottom:
                break
            visible.append(row)
            index += 1

        # 保留带：视口上下各 keep_rows 行。只有落在带外的才释放封面。
        keep_margin = stride * keep_rows
        retain_top = offset - keep_margin
        retain_bottom = offset + viewport_rect.height() + keep_margin
        start = _first_row_at(retain_top)
        retain = set()
        walk = start
        while walk < len(rows):
            row = rows[walk]
            if row.geometry().top() > retain_bottom:
                break
            retain.add(row)
            walk += 1
        for row in self._cover_views - retain:
            row.cover.set_pixmap(None)
            if isinstance(row, GridCard):
                row.avatar.set_pixmap(None)
        self._cover_views = retain
        self._avatars_by_url = {}
        for row in retain:
            if isinstance(row, GridCard) and row.avatar_url:
                self._avatars_by_url.setdefault(row.avatar_url, []).append(row)
        visible.sort(key=lambda row: not (row.geometry().bottom() >= offset
            and row.geometry().top() <= offset + viewport_rect.height()))
        self.page.covers.prioritize(self.page.removal_undo.notice.image_urls() + self.page.external_cover_urls() + [row.item.cover_url for row in visible] +
                               [row.avatar_url for row in visible if isinstance(row, GridCard) and row.avatar_url])
        provider = self.danmaku_provider()
        for row in visible:
            row.refresh_cover(self.page.covers.get)
            if isinstance(row, GridCard):
                row.refresh_avatar(self.page.covers.get)
            if provider is not None and row.item.bvid:
                count = provider.get(row.item.bvid)
                if count is not None:
                    row.set_danmaku_count(count)
                # 播放量与弹幕数同批到达，缓存命中时一并刷新。
                views = provider.cached_view(row.item.bvid)
                if views is not None and row.item.view_count != views:
                    row.set_view_count(views)
