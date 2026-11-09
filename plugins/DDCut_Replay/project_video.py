"""Bounded-resolution libmpv software frames for independently timed video layers."""
import ctypes
import time
from PySide6.QtGui import QImage
from .hls import preview_options


class VideoFrames:
    def __init__(self, path, source_url, *, contain=False):
        from ddm.player import EMBEDDED_OPTIONS
        import mpv
        self.mpv = mpv
        self.started = time.monotonic()
        self.core = mpv.MPV(**EMBEDDED_OPTIONS, vo='libmpv', audio=False, hwdec='auto-copy', pause=True,
                            keep_open=True, keepaspect=contain, **preview_options(path))
        self.context = None
        try:
            self.context = mpv.MpvRenderContext(self.core, 'sw')
            self.context.update_cb = lambda: None
            self.core.play(source_url(path))
        except Exception:
            self.close()
            raise
        self.image, self.buffer, self.size = None, None, None
        self.target = None
        self.has_frame = False
        self.initialized = False

    def frame(self, stamp, playing, speed, width, height):
        core = self.core
        changed = self.context.update()
        size = (max(2, width), max(2, height))
        if changed or (size != self.size and core.video_params):
            info = self.mpv.MpvRenderFrameInfo()
            parameter = self.mpv.MpvRenderParam('invalid')
            parameter.type_id, parameter.data = 11, ctypes.addressof(info)
            self.mpv._mpv_render_context_get_info(self.context.handle, parameter)
            flags = info.flags
            # Initial clear/redraw requests can precede the first decoded frame.
            # PRESENT without REDRAW identifies a queued video frame, even if black.
            queued_frame = bool(flags & 1 and (self.initialized or not flags & 2))
            if self.has_frame or queued_frame:
                self.render_frame(size)
            if queued_frame:
                self.has_frame = self.initialized = True
        current = core.time_pos
        if current is None or not self.has_frame:
            if time.monotonic()-self.started > 15:
                raise RuntimeError('视频帧读取超时，请检查来源文件')
            if current is None:
                return None, False
        if not core.seeking:
            drift = stamp-float(current)
            tolerance = max(.025, 1/(core.container_fps or 30))
            if abs(drift) > (1 if playing else tolerance):
                core.seek(max(0, stamp), 'absolute+exact')
                self.target = stamp
                self.has_frame = False
                self.started = time.monotonic()
            elif self.target is not None:
                self.target = None
        core.pause = not playing
        if playing:
            core.speed = speed*max(.8, min(1.2, 1+(stamp-float(current))*.2))
        ready = self.has_frame and not bool(core.seeking) and self.target is None
        return self.image, ready and self.image is not None

    def render_frame(self, size):
        self.size = size
        stride = (size[0]*4+63)//64*64
        self.buffer = ctypes.create_string_buffer(stride*size[1]+64)
        address = (ctypes.addressof(self.buffer)+63)//64*64
        dimensions = (ctypes.c_int*2)(*size)
        fmt = ctypes.create_string_buffer(b'bgr0')
        pitch = ctypes.c_size_t(stride)
        params = (self.mpv.MpvRenderParam*5)()
        for index, (kind, pointer) in enumerate([(17, ctypes.addressof(dimensions)), (18, ctypes.addressof(fmt)),
                                                (19, ctypes.addressof(pitch)), (20, address)]):
            params[index].type_id, params[index].data = kind, pointer
        self.mpv._mpv_render_context_render(self.context.handle, params)
        content = bytearray(ctypes.string_at(address, stride*size[1]))
        # libmpv's bgr0 padding byte is undefined; Qt RGB32 requires opaque alpha.
        content[3::4] = b'\xff'*(len(content)//4)
        self.image = QImage(content, *size, stride, QImage.Format_RGB32).copy()

    def close(self):
        if self.context:
            self.context.free()
            self.context = None
        if self.core:
            self.core.terminate()
            self.core = None
        self.image, self.buffer = None, None
