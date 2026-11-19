"""Binding data and value conversion helpers, without Qt object ownership."""
def _as_int(value) -> int:
    from .models import safe_int

    return safe_int(value)

def _video_page_url(item) -> str:
    # Public https webpage URL, used ONLY for the explicit browser fallback.
    # It is never handed to the player (host EMBEDDED_OPTIONS has ytdl=False).
    from .models import video_url

    return video_url(item)

class VodBinding:
    """One VOD playback bound to a synthetic room (keyed by bvid)."""

    def __init__(self, item, cell, tile) -> None:
        self.item = item
        self.bvid = str(getattr(item, "bvid", "") or "")
        self.cell = cell
        self.tile = tile
        self.prev_room = None
        self.prev_chrome = {
            name: getattr(tile, name) for name in (
                "_recording_available", "quality_options", "quality",
                "actual_quality", "quality_locked"
            ) if hasattr(tile, name)
        }
        self.resume_paused = False
        self.cid = _as_int(getattr(item, "cid", 0))
        self.state = "resolving"  # resolving/playing/paused/error/ended
        self.stream = None
        self.warm_lease = None
        self.pending_warm_lease = None
        self.progress = 0.0
        self.duration = 0.0
        self.pending_seek = 0.0
        self.seek_issued = False
        self.pending_audio = ""
        self.audio_attempts = 0
        self.strip = None
        self.card = None
        self.danmaku = None       # [(time, mode, color, text)] sorted
        self.dm_cursor = 0
        self.dm_last_pos = 0.0
        self.dm_pending = False
        self.request_id = 0
        # --- v1.0：清晰度 / 全屏进度 / 预览 / 状态槽 ---
        self.quality = 0          # 当前档位 qn（0=接口默认最高）
        self.qualities = []       # [(qn, label)] 来自 accept_quality/description
        self.line = None          # 全屏视频底边 3px 蓝色进度线
        self.preview = None       # VodPreviewProvider（雪碧图预览）
        # --- v1.0 需求：：B 站字幕（SRT 临时文件）与开关 ---
        self.subtitles: list = []   # [(label, srt_path)]
        self.subtitle_on = False
        self.subtitle_loaded = False
        self._sub_pending = False
        #: v1.0b：复用宿主药丸时的状态（原文案/绘制钩子的存档）。
        self.order_pill = None
        #: v1.0 需求：：多 P 视频当前播放的分 P（0 基）。
        self.part = 0
        # --- v1.0 任务 3/8：左上角「N/总数」位置徽章 ---
        self.badge = None
        #: v1.0 任务 6：EOF 死锁存（一旦 ended 就不再接受续播/重连路径）。
        self.ended = False
        self._state_slot = None   # stateChanged 连接句柄（重注入先断旧连接）
        self._state_player = None  # 连接时的播放器实例（重建后不对旧对象断连）
