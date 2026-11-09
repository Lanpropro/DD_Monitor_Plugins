"""GUI coordination for an opt-in room-link receiver and native add dialog."""
from pathlib import Path
import queue
import sys

from .link_server import LinkServer, room_link
from . import deep_link_windows


class DeepLinks:
    def __init__(self, owner, *, registry_path=deep_link_windows.REGISTRY_PATH):
        self.owner = owner
        self.endpoint = owner.hub.root/'link-endpoint.json'
        self.registry_path = registry_path
        self.server, self.dialog = None, None
        self.error = ''
        if owner.hub.preferences.get('room_links',False):
            try:
                self.set_enabled(True)
            except Exception as error:
                self.error = str(error)

    def set_enabled(self, value):
        if value and self.server is None:
            from ddm import config
            launch = Path(sys.executable) if getattr(sys,'frozen',False) else Path(config.REPO)/'run.cmd'
            server = LinkServer(self.endpoint)
            try:
                deep_link_windows.register(self.endpoint,launch,registry_path=self.registry_path)
            except Exception:
                server.close()
                raise
            self.server = server
        elif not value:
            deep_link_windows.unregister(self.endpoint,registry_path=self.registry_path)
            self.close()
        self.error = ''
        self.owner.hub.preferences['room_links'] = bool(value)
        self.owner.hub.save()

    def poll(self):
        if not self.server or self.dialog is not None:
            return
        try:
            link = self.server.requests.get_nowait()
        except queue.Empty:
            return
        self.open_dialog(link)

    def request(self, uri):
        self.open_dialog(room_link(uri))

    def open_dialog(self, link):
        if self.dialog is not None:
            raise RuntimeError('请先完成当前添加直播间对话框')
        from ddm.add_room_dialog import AddRoomDialog
        window = self.owner.context.window
        dialog = AddRoomDialog(window,room_id_resolver=window._normalize_room_input,
            folders=window.sidebar.folder_state(),folder_creator=window.sidebar.create_folder)
        self.dialog = dialog
        dialog.edit.setText(link)
        dialog.hint.setText('确认直播间和文件夹后添加。')
        def accepted():
            window._add_room_id(dialog.room_id,folder_id=dialog.folder_id)
        def finished(*_):
            self.dialog = None
            dialog.deleteLater()
        dialog.accepted.connect(accepted)
        dialog.finished.connect(finished)
        if window.isMinimized() or not window.isVisible():
            tray = getattr(window,'_tray',None)
            tray.restore() if tray else window.showNormal()
        dialog.open()

    def close(self):
        if self.dialog is not None:
            self.dialog.reject()
        if self.server is not None:
            self.server.close()
            self.server = None
