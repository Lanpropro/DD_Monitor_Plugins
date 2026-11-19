"""Read-only Python item projection; no QListWidgetItem ownership or destruction."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class OrderModel(QAbstractListModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.items = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        if role == Qt.UserRole:
            return item
        if role == Qt.DisplayRole:
            return item.title
        if role == Qt.AccessibleTextRole:
            return (item.title + ' · ' + item.owner_name
                    + (' · 已看完' if item.is_finished else '')
                    + (' · 已入墙' if item.bvid in self.parent().wall_bvids else ''))
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled

    def replace(self, items):
        items = list(items)
        if len(items) == len(self.items) and all(a is b for a,b in zip(items,self.items)):
            if items:
                self.dataChanged.emit(self.index(0,0),self.index(len(items)-1,0),[])
            return
        self.beginResetModel()
        self.items = items
        self.endResetModel()
