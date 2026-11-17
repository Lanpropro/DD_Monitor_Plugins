"""Local order over the canonical account list; never writes to Bilibili."""
from .preferences import read_preferences


class WatchOrder:
    def __init__(self, context):
        self.context = context
        self.account = None
        self.items = []
        self.keys = []
        self.include_finished = read_preferences(context)['order_include_finished']

    def update(self, items, account):
        account = str(account or '')
        if account != self.account:
            self.account = account
            reader = getattr(self.context, 'setting', None)
            saved = reader('watch_order', {}) if callable(reader) else {}
            self.keys = list(dict.fromkeys(key for key in saved.get('keys', []) if isinstance(key, str))) if isinstance(saved, dict) and saved.get('account') == account else []
        self.items = list(items)
        available = {item.bvid for item in self.items}
        # Keep saved keys across transient empty/loading states, prune on a real list.
        if self.items:
            self.keys = [key for key in self.keys if key in available]
            self.keys.extend(item.bvid for item in self.items if item.bvid not in self.keys)

    def snapshot(self, *, include_finished=None):
        if include_finished is None:
            include_finished = self.include_finished
        by_key = {item.bvid: item for item in self.items}
        return [by_key[key] for key in self.keys if key in by_key
                and (include_finished or not by_key[key].is_finished)]

    def next_after(self, bvid):
        found = False
        for item in self.snapshot(include_finished=True):
            if found and (self.include_finished or not item.is_finished):
                return item
            if item.bvid == bvid:
                found = True
        return None

    def move(self, bvid, before=None):
        keys = [item.bvid for item in self.snapshot(include_finished=True)]
        if bvid not in keys or before == bvid:
            return False
        keys.remove(bvid)
        index = keys.index(before) if before in keys else len(keys)
        keys.insert(index, bvid)
        if keys == self.keys:
            return False
        self.keys = keys
        setter = getattr(self.context, 'set_setting', None)
        if callable(setter):
            setter('watch_order', dict(account=self.account, keys=list(keys)))
        return True

    def restore(self, keys):
        """Restore available entries to their order before the undone removal."""
        available = {item.bvid for item in self.items}
        ordered = list(dict.fromkeys(key for key in [*keys,*self.keys] if key in available))
        if ordered == self.keys:
            return False
        self.keys = ordered
        self.context.set_setting('watch_order', dict(account=self.account, keys=list(ordered)))
        return True
