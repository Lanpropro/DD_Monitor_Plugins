"""Remember browsing within this run, by item identity and local offset."""
from PySide6.QtCore import QObject


class BrowseState(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        reader = getattr(page.context, 'setting', None)
        legacy = reader('browse_state', {}) if callable(reader) else {}
        if legacy:
            setter = getattr(page.context, 'set_setting', None)
            if callable(setter):
                setter('browse_state', {})  # Discard positions persisted by older builds.
        self.saved = {}
        self.pending = None

    def clear(self):
        self.saved = {}
        self.pending = None

    def capture(self):
        page = self.page
        if not page.preferences['remember_state']:
            self.clear()
            return
        if page.stack.currentWidget() not in (page._active_scroll(), page.noresult_view, page.empty_view):
            return
        key = page._visible_anchor_key()
        views = page._cards_by_key if page._view_mode == 'grid' else page._rows_by_key
        anchor = views.get(key)
        value = page._active_scroll().verticalScrollBar().value()
        self.saved = dict(key=key, offset=value - anchor.y() if anchor is not None else 0,
            value=value, view=page._view_mode, query=page.search.text(),
            filter=page.filter_combo.currentData(), sort=page.sort_combo.currentData(),
            account=str(page._cookies.get('DedeUserID') or ''))

    def prepare(self):
        page = self.page
        if not page.preferences['remember_state']:
            self.pending = None
            self._apply_controls(dict(view=page.preferences['default_view'], query='', filter='all', sort='recent'))
            return
        if not self.saved:
            self.pending = None
            return
        self.pending = dict(self.saved)
        self._apply_controls(self.pending)

    def _apply_controls(self, state):
        page = self.page
        page._view_mode = state.get('view') if state.get('view') in ('grid', 'list') else page._view_mode
        page.view_switch.set_mode(page._view_mode)
        page.search.blockSignals(True)
        query = state.get('query')
        page.search.setText(query if isinstance(query, str) else '')
        page.search.blockSignals(False)
        for name in ('filter', 'sort'):
            combo = getattr(page, name + '_combo')
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(state.get(name))))
            combo.blockSignals(False)
        page.filter_tabs.setValue(page.filter_combo.currentData())

    def validate_account(self):
        if self.pending is None:
            return
        account = str(self.page._cookies.get('DedeUserID') or '')
        if self.pending.get('account') and account != self.pending['account']:
            self.clear()
            self._apply_controls(dict(view=self.page.preferences['default_view'], query='', filter='all', sort='recent'))

    def materialize(self):
        if self.pending is None:
            return
        key = self.pending.get('key')
        if isinstance(key, str):
            self.page._ensure_anchor_materialized(key)

    def restore(self):
        if self.pending is None:
            return
        page = self.page
        state, self.pending = self.pending, None
        key = state.get('key')
        bar = page._active_scroll().verticalScrollBar()
        if isinstance(key, str) and page._restore_anchor(key):
            offset = state.get('offset', 0)
            bar.setValue(bar.value() + (offset if type(offset) is int else 0))
        else:
            value = state.get('value', 0)
            bar.setValue(max(0, value) if type(value) is int else 0)
