"""Read-only spatial snapshot of host cells; never capture a native video surface."""
from dataclasses import dataclass
from PySide6.QtCore import QRect, QRectF
from ddm import layouts, resize_layout


@dataclass
class PlacementCell:
    index: int
    rect: QRectF
    target: object
    title: str
    kind: str
    identity: str = ''
    image: object = None


@dataclass
class PlacementSnapshot:
    title: str
    aspect: float
    cells: list

    def signature(self):
        return tuple((cell.index, id(cell.target), cell.kind, cell.identity,
                      cell.rect.x(), cell.rect.y(), cell.rect.width(), cell.rect.height()) for cell in self.cells)


def snapshot_wall(wall, candidates, binding_for, cover_for):
    if wall is None:
        return PlacementSnapshot('当前布局不可用', 16 / 9, [])
    preset = layouts.BY_ID.get(wall.layout_id)
    if not preset or not preset.get('spec'):
        return PlacementSnapshot('当前布局不可用', 16 / 9, [])
    rows, columns, cells = preset['spec']
    panes = wall.pane_danmaku
    tiles = panes.cell_tiles()
    chats = panes.bindings()
    # Read saved divider ratios through the same geometry engine as the wall.
    rectangles = {}
    control = getattr(wall, 'layout_control', None)
    if control is not None and (wall.layout_id == 'corner' or wall.layout_id in wall._partitions):
        tree, weights, _widgets, minimum, defaults = control.border_model()
        width, height = max(1, wall.width()), max(1, wall.height())
        rects, _dividers = resize_layout.geometry(tree, QRect(0, 0, width, height), wall.grid.spacing(),
                                                minimum, weights, defaults)
        rectangles = {key: QRectF(rect.x() / width, rect.y() / height, rect.width() / width, rect.height() / height)
                      for key, rect in rects.items()}
        aspect = width / height
    else:
        aspect = 16 / 9
    result = []
    for index, (row, col, row_span, col_span) in enumerate(cells):
        rect = rectangles.get(index, QRectF(col / columns, row / rows, col_span / columns, row_span / rows))
        tile = tiles.get(index)
        if index in chats:
            source = chats[index]
            name = str((getattr(source, 'room', None) or {}).get('uname') or '')
            result.append(PlacementCell(index, rect, None, '弹幕' + (' · ' + name if name else ''), 'chat'))
            continue
        if tile is None or tile not in candidates:
            result.append(PlacementCell(index, rect, None, '不可用', 'unavailable'))
            continue
        binding = binding_for(tile)
        room = getattr(tile, 'room', None) or {}
        occupied = bool(room.get('room_id') or binding)
        title = str(binding.item.title if binding else room.get('uname') or room.get('title') or '空视频格')
        image = cover_for(binding.item.cover_url) if binding else None
        if image is None and occupied:
            cover = getattr(tile, 'cover', None)
            image = cover.pixmap() if cover is not None else None
        result.append(PlacementCell(index, rect, tile, title, 'video' if occupied else 'empty',
                                    str(binding.bvid if binding else room.get('room_id') or ''), image))
    return PlacementSnapshot(preset['name'], aspect, result)
