"""Overview shortcuts backed by the advanced page's existing path editors."""
from pathlib import Path
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QFileDialog
from .panel_controls import StableSelect
from .model_catalog import discover_models, model_name


class ModelPicker(StableSelect):
    def __init__(self, editor, title, *, directory=False, parent=None):
        super().__init__(parent)
        self.editor, self.title, self.directory = editor, title, directory
        self.setAccessibleName(title)
        self.activated.connect(self._choose)
        editor.textChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, _text=None):
        blocker = QSignalBlocker(self)
        path = self.editor.text().strip()
        self.clear()
        paths = discover_models(path, directory=self.directory)
        if path and not any(Path(value).resolve() == Path(path).resolve() for value in paths):
            self.addItem(model_name(path) + '（未就绪）', path)
        elif not path:
            self.addItem('选择模型…', '')
        for value in paths:
            self.addItem(model_name(value), value)
        self.addItem('选择其他模型目录…' if self.directory else '选择其他模型文件…', None)
        selected = next((index for index in range(self.count()) if path and self.itemData(index)
                         and Path(self.itemData(index)).resolve() == Path(path).resolve()), 0)
        self.setCurrentIndex(selected)
        self.setToolTip(self.title + ('\n' + path if path else '') + '\n列出当前模型同级目录及插件内置目录中的兼容文件')
        del blocker

    def _choose(self, index):
        if self.itemData(index) is None:
            path = (QFileDialog.getExistingDirectory(self, self.title, self.editor.text()) if self.directory else
                    QFileDialog.getOpenFileName(self, self.title, self.editor.text(), 'GGUF (*.gguf)')[0])
            if path:
                self.editor.setText(path)
        elif self.itemData(index):
            self.editor.setText(self.itemData(index))
        self.refresh()

    def showPopup(self):
        self.refresh()
        super().showPopup()
