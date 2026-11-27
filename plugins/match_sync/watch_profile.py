"""Small public profile snapshots for the watch-party roster."""
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader, QPixmap


def sidebar_profile(viewer):
    row = getattr(getattr(viewer.context.window, "sidebar", None), "account_row", None)
    if not row or not row.platform or not row.uid:
        return None
    profile = {"platform": row.platform, "id": str(row.uid)}
    name = "".join(char for char in row.uname if char.isprintable()).strip()[:48]
    if name:
        profile["nickname"] = name
    avatar = row.avatar.pixmap()
    if avatar and not avatar.isNull():
        image = avatar.toImage().scaled(26, 26, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        buffer = QBuffer()
        if buffer.open(QIODevice.WriteOnly) and image.save(buffer, "JPEG", 80):
            payload = bytes(buffer.data().toBase64()).decode("ascii")
            if len(payload) <= 2048:
                profile["avatar"] = payload
    return profile


def display_name(profile):
    return (profile.get("nickname") or profile["id"]) if profile else "访客"


def avatar_pixmap(profile):
    data = (profile or {}).get("avatar")
    if not isinstance(data, str) or len(data) > 2048 or not data.isascii():
        return QPixmap()
    buffer = QBuffer()
    buffer.setData(QByteArray.fromBase64(data.encode("ascii")))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer, b"jpeg")
    size = reader.size()
    if not size.isValid() or size.boundedTo(QSize(26, 26)) != size:
        return QPixmap()
    return QPixmap.fromImage(reader.read())
