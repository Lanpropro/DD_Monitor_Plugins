"""Local order over the canonical account list; never writes to Bilibili."""
class WatchOrder:
    def __init__(self, context):
        self.context = context
        self.account = None
        self.items = []
        self.keys = []

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

    def snapshot(self):
        by_key = {item.bvid: item for item in self.items}
        return [by_key[key] for key in self.keys if key in by_key]

    def move(self, bvid, before=None):
        keys = [item.bvid for item in self.snapshot()]
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
