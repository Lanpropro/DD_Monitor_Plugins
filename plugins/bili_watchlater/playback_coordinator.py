"""Playback binding, media requests, tick and release; UI stays with the plugin."""
from __future__ import annotations
import bisect
import os
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from .motion_trace import measure
from .playback_binding import VodBinding as _VodBinding, _as_int, _video_page_url

class PlaybackCoordinator:
    def __init__(self, owner):
        self.owner = owner
        self.bindings: dict[str, _VodBinding] = {}
        self.vod_requests: set = set()
        self.vod_context: dict[int, dict] = {}
        self.cell_vod: dict[int, int] = {}
        self.vod_seq = 0
        self.next_prefetch = None
        self.account_generation = 0
        self.dm_requests: set = set()
        self.dm_context: dict[int, tuple] = {}
        self.dm_seq = 0
        self.sub_requests: set = set()
        self.sub_context: dict[int, tuple] = {}
        self.sub_seq = 0
        self.tick_timer: QTimer | None = None

    @property
    def page(self):
        return self.owner.page  # Preserve the existing motion_trace measurement target.

    def advance_generation(self):
        self.account_generation += 1

    def clear_prefetch(self):
        if self.next_prefetch is not None:
            self.next_prefetch.clear()

    def refresh_items(self, items):
        """Refresh display metadata without restarting or seeking a player."""
        tiles = self.owner._host_tiles()
        for item in items:
            binding = self.bindings.get(item.bvid)
            if binding is None or item.owner_name in ('', '未知 UP 主'):
                continue
            binding.item = item
            tile = binding.tile
            if tile not in tiles:
                continue
            room = getattr(tile, 'room', None) or {}
            if room.get('watchlater_bvid') != binding.bvid:
                continue  # Resolving bindings will use the item on injection.
            room['face'] = item.owner_face
            title = self.room_title(binding)
            update = getattr(tile, 'set_uname_title', None)
            if callable(update):
                update(item.owner_name, title)

    def on_item_metadata(self, request_id, generation, item):
        if self.owner._shutting_down or generation != self.account_generation:
            return
        binding = self.bindings.get(item.bvid)
        if (binding is None or binding.request_id != request_id
                or binding.item.owner_name not in ('', '未知 UP 主')):
            return
        self.refresh_items([item])

    def dispose_prefetch(self):
        if self.next_prefetch is not None:
            self.next_prefetch.clear()
            self.next_prefetch.deleteLater()
            self.next_prefetch = None

    def stop_tick_timer(self):
        if self.tick_timer is not None:
            self.tick_timer.stop()
            try:
                self.tick_timer.deleteLater()
            except RuntimeError:
                pass
            self.tick_timer = None

    def release_all(self, *, restart):
        for binding in list(self.bindings.values()):
            self.release_binding(binding,restart=restart)
        if not restart:
            self.bindings.clear()

    def start_subtitle_fetch(self, binding, cookies: dict) -> None:
        if binding.subtitles or binding._sub_pending or self.owner._shutting_down:
            return
        from .workers import SubtitleRequest

        self.sub_seq += 1
        request_id = self.sub_seq
        binding._sub_pending = True
        self.sub_context[request_id] = (binding, self.account_generation)
        worker = SubtitleRequest(cookies, request_id, binding.bvid, binding.cid)
        self.sub_requests.add(worker)
        worker.completed.connect(self.on_subtitle_completed)
        worker.failed.connect(self.on_subtitle_failed)
        worker.finished.connect(
            lambda rid=request_id: self.retire_subtitle(rid), Qt.QueuedConnection
        )
        worker.finished.connect(worker.deleteLater)
        self.owner._sync_subtitle_control(binding.tile)
        try:
            worker.start()
        except Exception as error:
            self.sub_requests.discard(worker)
            self.on_subtitle_failed(request_id, error)
            worker.deleteLater()

    def retire_subtitle(self, request_id: int) -> None:
        self.sub_context.pop(request_id, None)
        for worker in tuple(self.sub_requests):
            if getattr(worker, "request_id", None) == request_id:
                self.sub_requests.discard(worker)
                break

    def current_subtitle_binding(self, request_id):
        ctx = self.sub_context.pop(request_id, None)
        if ctx is None:
            return None
        binding, generation = ctx
        if (self.owner._shutting_down or generation != self.account_generation
                or self.bindings.get(binding.bvid) is not binding):
            return None
        binding._sub_pending = False
        return binding

    def on_subtitle_completed(self, request_id, results) -> None:
        binding = self.current_subtitle_binding(request_id)
        if binding is None:
            self.unlink_paths([path for _label, path in (results or [])])
            return
        binding.subtitles = list(results or [])
        if not binding.subtitles:
            binding.subtitle_on = False
            self.owner._notify_warning("该视频没有可用字幕，或当前账号暂不能获取字幕。")
        self.owner._sync_subtitle_control(binding.tile)

    def on_subtitle_failed(self, request_id, error) -> None:
        binding = self.current_subtitle_binding(request_id)
        if binding is None:
            return
        binding.subtitle_on = False
        self.owner._sync_subtitle_control(binding.tile)
        self.owner._notify_warning("字幕加载失败，播放不受影响。")
        self.owner._log("subtitle fetch failed type=" + type(error).__name__)

    def apply_subtitle(self, binding, player) -> None:
        if not binding.subtitle_on or not binding.subtitles or binding.subtitle_loaded:
            return
        label, path = binding.subtitles[0]
        if self.mpv_command(player, "sub-add", path, "select", str(label)):
            binding.subtitle_loaded = True
            try:
                player._set("sub-visibility", True)
            except Exception:
                pass
        else:
            binding.subtitle_on = False
            self.owner._sync_subtitle_control(binding.tile)
            self.owner._notify_warning("当前播放器无法加载字幕。")

    @staticmethod
    def unlink_paths(paths) -> None:
        import os

        for path in paths:
            try:
                os.unlink(path)
            except Exception:
                pass

    def toggle_subtitle(self, tile) -> None:
        binding = self.owner._binding_for_tile(tile)
        if (binding is None or binding.state not in ("playing", "paused")
                or binding._sub_pending):
            return
        binding.subtitle_on = not binding.subtitle_on
        if binding.subtitle_on and not binding.subtitles:
            self.start_subtitle_fetch(binding, self.cookie_snapshot())
        player = self.owner._host_player(tile)
        if player is not None and binding.subtitle_loaded:
            try:
                player._set("sub-visibility", binding.subtitle_on)
            except Exception:
                pass
        self.owner._sync_subtitle_control(tile)

    def start_vod_for_tile(self, tile, item, *, prepared=None, resume=None) -> None:
        if self.owner._shutting_down or tile is None or item is None:
            return
        bvid = str(getattr(item, "bvid", "") or "")
        if not bvid:
            return
        if not self.owner.host.supports_playback():
            self.owner._notify_warning(
                "当前宿主版本不支持内嵌播放，请用浏览器打开。"
            )
            return
        cell = self.owner._resolve_cell_from_tile(tile)
        if cell is None:
            self.owner._notify_warning("这个格子没有注册稍后再看控件，无法内嵌播放。")
            return
        # 同一视频只在一个格子播：换格起播 = 把旧格子还原。
        old = self.bindings.get(bvid)
        if old is not None:
            if old.tile is tile:
                # 同格重复拖入同一视频 = 重播：直接重新取流，不先把
                # 旧直播起起来再停（避免无谓的直播取流与画面闪烁）。
                old.item = item
                self.replay_binding(old)
                return
            # 同一个正在播放的视频再次拖到另一个格子 = 移动画面，保留解码器。
            if old.state in ("playing", "paused"):
                from .tile_swap import swap_tiles_preserving_players
                if swap_tiles_preserving_players(window, old.tile, tile):
                    self.owner._sync_tile_widgets(old.tile)
                    self.owner._sync_tile_widgets(tile)
                    return
                self.owner._notify_warning("当前布局暂不能无中断换格，视频保持原位。")
                return
            self.release_binding(old, restart=True)
        # 目标格上已有的别的点播：先静默替换（不中间起直播）。
        for existing in list(self.bindings.values()):
            if existing.tile is tile:
                self.release_binding(existing, restart=False)
        room = getattr(tile, "room", None) or {}
        prev_room = None
        if str(room.get("room_id") or "") and not room.get("watchlater_bvid"):
            prev_room = dict(room)
        binding = _VodBinding(item, cell, tile)
        from .preferences import read_preferences
        binding.resume_paused = not read_preferences(self.owner.context)['auto_play']
        binding.part = 0          # v1.0 需求：：新片从第一 P 开始
        start = self.resume_seconds(item)
        if resume is not None:
            binding.part = resume['part']
            binding.quality = resume['quality']
            binding.resume_paused = binding.resume_paused or resume['paused']
            start = resume['progress']
            binding.progress = start
        binding.prev_room = prev_room
        self.bindings[bvid] = binding
        if self.owner.wall is not None:
            try:
                self.owner.wall.bind(cell, item)
            except Exception:
                pass
        if resume is not None:
            # Reserve the slot now. An empty host room ID hides live composers
            # and makes startup skip this slot until VOD injects its real ID.
            self.owner._stop_host_tile(tile)
            try:
                tile.set_room(dict(room_id='', live=False, live_known=True,
                                   watchlater_bvid=bvid, watchlater_part=binding.part))
            except Exception as error:
                self.owner._log('reserve restored slot failed type=' + type(error).__name__)
                self.release_binding(binding, restart=True)
                return
            binding.duration = float(item.duration)
        try:
            tile.set_status("正在取流…")
        except Exception:
            pass
        if resume is not None:
            # Restore presentation before the first paint, independently of HTTP.
            self.owner.presentation.apply_tile_chrome(binding, pending=True)
            self.owner._sync_subtitle_control(tile)
            entry = self.owner._bar_progress.get(id(tile))
            if entry is not None:
                entry['widget'].set_duration(item.duration)
                entry['widget'].set_position(start)
            self.owner._sync_tile_widgets(tile)
        if prepared is not None:
            self.start_resolution(binding, start=start, prepared=prepared)
        else:
            self.start_resolution(binding, start=start)
        self.ensure_tick_timer()

    @staticmethod
    def room_title(binding) -> str:
        """合成房间标题；多 P 时追加「P{n}」，画中画/标题浮标才分得清第几 P。"""
        title = str(getattr(binding.item, "title", "") or "")
        parts = int(getattr(binding.item, "parts", 1) or 1)
        if parts > 1:
            return "%s  P%d" % (title, int(binding.part) + 1)
        return title

    @staticmethod
    def resume_seconds(item) -> float:
        # 单 P 才续播；多 P 不猜分P（与 v1.0 同一规则）。
        if bool(getattr(item, "is_multi_part", False)):
            return 0.0
        try:
            return max(0.0, float(getattr(item, "progress", 0) or 0))
        except (TypeError, ValueError):
            return 0.0

    def cookie_snapshot(self) -> dict[str, str]:
        snapshot = None
        try:
            snapshot = self.owner.context.credentials("bilibili")
        except Exception:
            snapshot = None
        values = getattr(snapshot, "values", None)
        if values is None and isinstance(snapshot, dict):
            values = snapshot
        cookies: dict[str, str] = {}
        try:
            if values is not None:
                cookies = {
                    str(key): str(value)
                    for key, value in dict(values).items()
                    if value is not None
                }
        except (TypeError, ValueError):
            cookies = {}
        if self.account_generation <= 0:
            cred_generation = _as_int(getattr(snapshot, "generation", 0))
            self.account_generation = cred_generation if cred_generation > 0 else 1
        return cookies

    def start_resolution(self, binding, *, start: float,
                          qn: int | None = None, prepared=None) -> None:
        """起一次取流（首播/重试/重播/秒切回退续播/清晰度切换共用）。"""
        cookies = self.cookie_snapshot()
        generation = self.account_generation
        cell = binding.cell
        self.cancel_cell_requests(cell)
        from .workers import VodResolveRequest

        if qn is None:
            qn = int(binding.quality or 0)
        self.vod_seq += 1
        request_id = self.vod_seq
        binding.request_id = request_id
        previous_state = binding.state
        binding.resume_paused = binding.resume_paused or previous_state == "paused"
        # v1.0 任务 6：重播/重试/换片都是显式重启，解除 ended 锁存。
        binding.ended = False
        binding.state = "resolving"
        room = getattr(binding.tile, "room", None) or {}
        self.vod_context[request_id] = {
            "cell": cell,
            "tile": binding.tile,
            "item": binding.item,
            "bvid": binding.bvid,
            "generation": generation,
            "start": float(start),
            "qn": int(qn),
            "previous_state": previous_state,
            "expected_room": str(room.get("room_id") or ""),
            "expected_video": room.get("watchlater_bvid"),
        }
        self.cell_vod[id(cell)] = request_id
        if prepared is not None:
            try:
                stream, lease = prepared.activate()
                binding.pending_warm_lease = lease
                self.on_vod_completed(request_id, generation, stream)
                self.retire_vod(request_id)
                return
            except Exception as error:
                lease = getattr(binding, 'pending_warm_lease', None)
                if lease is not None:
                    lease.close()
                    binding.pending_warm_lease = None
                self.owner._log('prefetch handoff fallback type=' + type(error).__name__)
        worker = VodResolveRequest(
            cookies, generation, request_id, binding.bvid,
            # 多 P：第 2 页起不再用列表里的 cid，交给 resolve 按 page 取。
            _as_int(getattr(binding.item, "cid", 0)) if binding.part <= 0 else 0,
            int(qn), hq=True, page=binding.part,
        )
        self.vod_requests.add(worker)
        worker.load_metadata = binding.item.owner_name in ('', '未知 UP 主')
        worker.completed.connect(self.on_vod_completed)
        worker.failed.connect(self.on_vod_failed)
        worker.metadata_ready.connect(self.on_item_metadata)
        try:
            worker.finished.connect(worker.deleteLater)
        except Exception:
            pass
        worker.finished.connect(
            lambda rid=request_id: self.retire_vod(rid), Qt.QueuedConnection
        )
        try:
            worker.start()
        except Exception as error:
            self.vod_requests.discard(worker)
            self.vod_context.pop(request_id, None)
            self.cell_vod.pop(id(cell), None)
            self.owner._log("vod worker start failed type=" + type(error).__name__)
            self.show_card_error(binding, "无法启动取流，请用浏览器打开")

    def start_danmaku_fetch(self, binding, cookies: dict) -> None:
        from .workers import DanmakuRequest

        self.dm_seq += 1
        request_id = self.dm_seq
        binding.dm_pending = True
        self.dm_context[request_id] = (binding, self.account_generation, binding.part)
        worker = DanmakuRequest(
            cookies, request_id, binding.bvid,
            binding.cid,
        )
        self.dm_requests.add(worker)
        worker.completed.connect(self.on_danmaku_completed)
        worker.failed.connect(self.on_danmaku_failed)
        try:
            worker.finished.connect(worker.deleteLater)
        except Exception:
            pass
        worker.finished.connect(
            lambda rid=request_id: self.retire_danmaku(rid), Qt.QueuedConnection
        )
        try:
            worker.start()
        except Exception:
            self.dm_requests.discard(worker)
            self.dm_context.pop(request_id, None)
            binding.dm_pending = False

    def on_danmaku_completed(self, request_id, comments) -> None:
        ctx = self.dm_context.get(request_id)
        if ctx is None or self.owner._shutting_down:
            return
        binding, generation, part = ctx
        if (self.bindings.get(binding.bvid) is not binding
                or generation != self.account_generation or part != binding.part):
            return
        binding.danmaku = list(comments or [])
        binding.dm_cursor = bisect.bisect_left(
            [entry[0] for entry in binding.danmaku], binding.progress
        )
        binding.dm_last_pos = binding.progress
        binding.dm_pending = False

    def on_danmaku_failed(self, request_id, error) -> None:
        ctx = self.dm_context.get(request_id)
        if ctx is None or self.owner._shutting_down:
            return
        binding, generation, part = ctx
        if (self.bindings.get(binding.bvid) is not binding
                or generation != self.account_generation or part != binding.part):
            return
        binding.dm_pending = False
        message = getattr(error, "message", None)
        self.owner._log("danmaku fetch failed: " + str(message or type(error).__name__)[:160])

    def retire_danmaku(self, request_id) -> None:
        self.dm_context.pop(request_id, None)
        for worker in tuple(self.dm_requests):
            if getattr(worker, "request_id", None) == request_id:
                self.dm_requests.discard(worker)
                break

    def is_current_vod(self, request_id, generation, ctx) -> bool:
        if generation != self.account_generation:
            return False
        if ctx.get("generation") != generation:
            return False
        return self.cell_vod.get(id(ctx["cell"])) == request_id

    def on_vod_completed(self, request_id, generation, stream) -> None:
        if self.owner._shutting_down:
            return
        ctx = self.vod_context.get(request_id)
        if ctx is None or not self.is_current_vod(request_id, generation, ctx):
            return
        tile = ctx["tile"]
        if tile not in self.owner._host_tiles():
            return  # 格子已被布局拆掉
        room = getattr(tile, "room", None) or {}
        if str(room.get("room_id") or "") != ctx.get("expected_room", ""):
            return  # 取流期间用户换了台/拖了直播进来：结果作废
        if ctx.get('expected_video') and room.get('watchlater_bvid') != ctx['expected_video']:
            return  # Closing a reserved (empty-ID) slot must also invalidate it.
        binding = self.bindings.get(ctx["bvid"])
        if binding is None or binding.request_id != request_id:
            return
        self.inject_playback(binding, stream, float(ctx.get("start", 0.0)))

    def on_vod_failed(self, request_id, generation, error) -> None:
        if self.owner._shutting_down:
            return
        ctx = self.vod_context.get(request_id)
        if ctx is None or not self.is_current_vod(request_id, generation, ctx):
            return
        binding = self.bindings.get(ctx["bvid"])
        if binding is None or binding.request_id != request_id:
            return
        message = getattr(error, "message", None)
        if not isinstance(message, str) or not message.strip():
            message = "取流失败，请用浏览器打开该视频"
        if ctx.get("previous_state") in ("playing", "paused") and self.owner._host_player(binding.tile):
            binding.state = ctx["previous_state"]
            self.owner._apply_tile_chrome(binding)
            self.owner._sync_fullscreen_state(binding.tile)
            self.owner._notify_warning("画质切换失败，保留原画质继续播放。")
        else:
            self.show_card_error(binding, message)

    def retire_vod(self, request_id) -> None:
        self.vod_context.pop(request_id, None)
        for worker in tuple(self.vod_requests):
            if getattr(worker, "request_id", None) == request_id:
                self.vod_requests.discard(worker)
                break

    def cancel_cell_requests(self, cell) -> None:
        request_id = self.cell_vod.pop(id(cell), None)
        if request_id is None or request_id not in self.vod_context:
            return
        for worker in tuple(self.vod_requests):
            if getattr(worker, "request_id", None) == request_id:
                try:
                    worker.cancel()
                except Exception:
                    pass

    def cancel_all_requests(self) -> None:
        for worker in tuple(self.vod_requests) + tuple(self.dm_requests) \
                + tuple(self.sub_requests):
            try:
                worker.cancel()
            except Exception:
                pass

    def drain_requests(self) -> None:
        self.cancel_all_requests()
        for worker in tuple(self.vod_requests) + tuple(self.dm_requests) \
                + tuple(self.sub_requests):
            try:
                if worker.isRunning():
                    worker.wait()
            except Exception:
                pass
            try:
                worker.deleteLater()
            except Exception:
                pass
        # 字幕 worker 交接后的临时文件：completed 信号可能在宿主退出前未被
        # 处理（_sub_context 已清），这里按 worker 记录的 srt_path 兜底删除。
        # 与 binding 释放时的清理幂等（重复 unlink 异常被吞）。
        sub_paths = [getattr(w, "srt_path", None) for w in tuple(self.sub_requests)]
        self.unlink_paths([p for p in sub_paths if p])
        self.vod_requests.clear()
        self.vod_context.clear()
        self.cell_vod.clear()
        self.dm_requests.clear()
        self.dm_context.clear()
        self.sub_requests.clear()
        self.sub_context.clear()

    def inject_playback(self, binding, stream, start: float) -> None:
        from .vod_headers import is_direct_url, merge_headers

        tile = binding.tile
        url = getattr(stream, "url", "")
        if not is_direct_url(url):
            # 红线：网页 URL 绝不喂宿主播放器（ytdl=False 必错）。
            self.show_card_error(binding, "取流结果异常，请用浏览器打开")
            return
        old_lease = getattr(binding, 'warm_lease', None)
        if old_lease is not None:
            old_lease.close()
        binding.warm_lease = getattr(binding, 'pending_warm_lease', None)
        binding.pending_warm_lease = None
        self.owner._stop_host_tile(tile)
        current = getattr(tile, "room", None) or {}
        danmaku_enabled = bool(current.get("video_danmaku_enabled", True)) \
            if current.get("watchlater_bvid") == binding.bvid else True
        synthetic = {
            "room_id": binding.bvid,      # 无冒号：start_tile 不会当平台房
            "live": False,                # 离线分支：宿主绝不联网取直播
            "live_known": True,
            "platform": "bilibili",
            "uname": str(getattr(binding.item, "owner_name", "") or ""),
            # v1.0 需求：/17：画中画/头像位读 room["face"]，缺了就只剩空白。
            "face": str(getattr(binding.item, "owner_face", "") or ""),
            "title": self.room_title(binding),
            "video_danmaku_enabled": danmaku_enabled,
            "watchlater_bvid": binding.bvid,  # 扫描标记（dict 拷贝会保留）
            "watchlater_part": int(binding.part),  # 多 P：当前第几 P（0 基）
        }
        try:
            tile.set_room(synthetic)
        except Exception as error:
            self.owner._log("synthetic room failed type=" + type(error).__name__)
            self.release_binding(binding, restart=True)
            return
        # 需求：：左上角「未开播」药丸由 room["live"] 驱动（playback_badges
        # .py:81-84），点播期间先藏一次；悬停再想显示时由格子过滤器拦
        # Show 事件（实例级，不 patch 宿主类）。
        badge = getattr(tile, "stream_badge", None)
        if badge is not None:
            try:
                badge.hide()
            except RuntimeError:
                pass
        if not self.owner._play_on_host(tile, url.strip(), merge_headers(
                getattr(stream, "headers", None)), start_paused=binding.resume_paused):
            self.release_binding(binding, restart=True)
            self.owner._notify_warning("播放器启动失败，请用浏览器打开该视频。")
            return
        player = self.owner._host_player(tile)
        if player is None:
            self.release_binding(binding, restart=True)
            return
        # VOD：EOF 不是冻屏（宿主 _check 会把 eof 当 stall 计分）。
        try:
            player.freeze_watch = False
        except Exception:
            pass
        try:
            player._set("keep-open", "always")  # 结束停在最后一帧
        except Exception:
            pass
        binding.pending_seek = 0.0
        binding.seek_issued = False
        if start > 0:
            text = ("%d" % int(start)) if start == int(start) else ("%g" % start)
            try:
                player._set("start", text)  # 复播时内核已建，选项生效
            except Exception:
                pass
            # 首播时宿主内核在 _play_on 内部才 bind，上面的写入是空操作；
            # 节拍器在文件加载后校验位置，丢了才补 seek（v1.0 实际运行环境证据）。
            binding.pending_seek = start
        binding.pending_audio = ""
        binding.audio_attempts = 0
        if getattr(stream, "is_dash", False):
            audio_url = getattr(stream, "audio_url", "") or ""
            if isinstance(audio_url, str) and audio_url.strip():
                binding.pending_audio = audio_url.strip()
        binding.stream = stream
        binding._trace_tick = binding._trace_count = 0
        binding._trace_paused = None
        binding.cid = _as_int(getattr(stream, "cid", 0)) or binding.cid
        if binding.danmaku is None and not binding.dm_pending:
            self.start_danmaku_fetch(binding, self.cookie_snapshot())
        binding.subtitle_loaded = False
        # 需求：：接口给的可选档位进绑定，右键「画质」菜单用。
        try:
            from .vod_stream import qualities_from_stream

            binding.qualities = qualities_from_stream(stream)
            actual = _as_int(getattr(stream, "qn", 0))
            if actual > 0:
                binding.quality = actual
        except Exception:
            binding.qualities = []
        self.owner._apply_tile_chrome(binding)
        binding.state = "paused" if binding.resume_paused else "playing"
        binding.progress = start
        # v1.0 任务 3：左上角立刻换成「N/总数」，不留一个空洞。
        self.owner._sync_position_badge(binding)
        self.owner._sync_subtitle_control(tile)
        # stateChanged 连接卫生：同一播放器重注入（清晰度切换/续播）先断
        # 旧 lambda，避免重复回调累积。播放器实例被重建（拖格回退 release
        # 后新建）时不对旧对象断连——libpyside 会刷 Failed to disconnect
        # 警告；旧实例销毁时连接自然消失。
        slot = lambda state, b=binding: self.on_host_player_state(b, state)
        try:
            if (binding._state_slot is not None
                    and binding._state_player is player):
                try:
                    player.stateChanged.disconnect(binding._state_slot)
                except Exception:
                    pass
            player.stateChanged.connect(slot)
            binding._state_slot = slot
            binding._state_player = player
        except Exception:
            pass
        # 需求：：预览雪碧图 provider（懒加载，首次悬停才抓取）。
        from .preferences import read_preferences
        if binding.preview is None and read_preferences(self.owner.context)['video_preview']:
            try:
                from .vod_preview import VodPreviewProvider

                binding.preview = VodPreviewProvider(
                    binding.bvid, binding.cid,
                )
            except Exception as error:
                binding.preview = None
                self.owner._log("preview create failed type=" + type(error).__name__)
        self.owner._ensure_tile_widgets(binding)
        if binding.card is not None:
            binding.card.hide()
        if binding.strip is not None:
            binding.strip.reset()
        entry = self.owner._bar_progress.get(id(tile))
        if entry is not None and binding.preview is not None:
            try:
                entry["widget"].set_preview_provider(binding.preview)
            except Exception:
                pass
        if entry is not None:
            try:
                entry["widget"].reset()
                entry["widget"].set_duration(binding.duration or binding.item.duration)
                entry["widget"].set_position(start)
            except Exception:
                pass
        self.owner._ensure_space_shortcut()
        self.owner._sync_fullscreen_state(tile)
        self.ensure_tick_timer()
        self.owner._sync_order_wall_marks()

    def on_host_player_state(self, binding, state: str) -> None:
        """只观察宿主播放器状态，绝不回写宿主（宿主自己的槽已接好）。"""
        if self.owner._shutting_down or binding.state not in ("playing", "paused"):
            return
        if state != "error":
            return
        player = self.owner._host_player(binding.tile)
        try:
            if player is not None and player._prop("eof-reached", False):
                return  # EOF 由节拍器走 ended，不算错误
        except Exception:
            pass
        binding.state = "error"
        self.show_card_error(binding, "播放中断或失败，可重试")

    def show_card_error(self, binding, message: str) -> None:
        binding.state = "error"
        self.owner._ensure_tile_widgets(binding)
        if binding.card is not None:
            try:
                binding.card.show_error(message)
            except RuntimeError:
                pass
        if binding.strip is not None:
            try:
                binding.strip.hide()
            except RuntimeError:
                pass

    @staticmethod
    def mpv_command(player, *args) -> bool:
        """v1.0 头号修复：宿主 TilePlayer **没有 command()**（player.py 只有
        _prop/_set/play/stop/release/set_paused），裸 mpv 在 ``player.player``。

        v1.0 直接 ``player.command(...)`` 抛 AttributeError 被 except 吞掉，
        导致进度条拖不动（需求：）、拖格续播定位丢失（需求：）、DASH 挂
        音轨静默失败。顺序：真宿主裸 mpv → 测试替身的 command → seek 的
        属性写兜底（time-pos）。
        """
        raw = getattr(player, "player", None)
        command = getattr(raw, "command", None)
        if callable(command):
            try:
                command(*args)
                return True
            except Exception:
                return False
        command = getattr(player, "command", None)
        if callable(command):
            try:
                command(*args)
                return True
            except Exception:
                return False
        if args and args[0] == "seek" and len(args) >= 2:
            try:
                player._set("time-pos", float(args[1]))
                return True
            except Exception:
                return False
        return False

    def seek_binding_relative(self, binding, delta: float) -> None:
        """需求 2：B 站原版左右键 —— 以播放器真实 time-pos 为基准 ±5 秒。

        旧实现算 ``binding.progress + 5`` 再发绝对 seek：全屏重排会释放/重建
        播放器，节拍器也可能滞后，``progress`` 停在旧值时连按几次都跳到同一
        个位置，肉眼就是「全屏下左右键没反应」。mpv 的相对 seek 以真实播放
        位置为准，按住连发每次都实打实走 5 秒；节拍器下一拍会从 time-pos
        把 ``binding.progress`` 修正回真实值。
        """
        if binding is None or self.owner._shutting_down:
            return
        delta = float(delta)
        if not delta:
            return
        player = self.owner._player_for_binding(binding)
        if player is None:
            return
        current = float(getattr(binding, "progress", 0.0) or 0.0)
        duration = float(getattr(binding, "duration", 0.0) or 0.0)
        local_target = max(0.0, current + delta)
        if duration > 0:
            local_target = min(local_target, duration)
        raw = getattr(player, "player", None)
        command = getattr(raw, "command", None)
        if not callable(command):
            command = getattr(player, "command", None)
        if callable(command):
            # relative+exact = 精确 ±5 秒（不吸附关键帧），与 B 站网页一致；
            # 少数 mpv 构建不认组合 flag 时退回普通 relative。
            if (self.mpv_command(player, "seek", delta, "relative+exact")
                    or self.mpv_command(player, "seek", delta, "relative")):
                binding.progress = local_target
                binding.pending_seek = 0.0
                binding.seek_issued = False
                return
        # 没有裸 mpv command 的替身播放器：_mpv_command 的 _set 兜底只认
        # 绝对秒数，所以按本地进度做一次绝对跳。
        self.seek_binding(binding, local_target)

    def seek_binding(self, binding, seconds: float) -> None:
        player = self.owner._player_for_binding(binding)
        if player is None:
            return
        target = max(0.0, float(seconds))
        if self.mpv_command(player, "seek", target, "absolute"):
            # 立即回写进度：节拍器下一拍前滑杆/时间显示不闪回。
            binding.progress = target
            binding.pending_seek = 0.0
            binding.seek_issued = False

    def retry_binding(self, binding) -> None:
        if self.owner._shutting_down or binding.state == "resolving":
            return
        if binding.card is not None:
            binding.card.hide()
        try:
            binding.tile.set_status("正在取流…")
        except Exception:
            pass
        self.start_resolution(binding, start=binding.progress)

    def replay_binding(self, binding) -> None:
        if self.owner._shutting_down or binding.state == "resolving":
            return
        if binding.card is not None:
            binding.card.hide()
        binding.dm_cursor = 0
        binding.dm_last_pos = 0.0
        self.start_resolution(binding, start=0.0)

    def open_binding_browser(self, binding) -> None:
        self.owner._open_url(_video_page_url(binding.item))

    def has_next(self, binding) -> bool:
        parts = int(getattr(binding.item, "parts", 1) or 1)
        return binding.part + 1 < parts or self.owner._next_item_for(binding.bvid) is not None

    def sync_next_button(self, binding) -> None:
        """结束卡上的「下一个视频」可用性跟随列表变化（删除/刷新后仍准）。"""
        card = binding.card
        if card is None:
            return
        try:
            card.set_next_available(self.has_next(binding))
        except (AttributeError, RuntimeError):
            pass

    def next_binding(self, binding) -> None:
        """播放列表里的下一条；到末尾就明确提示，绝不悄悄重播当前这条。

        复用 :meth:`_start_vod_for_tile` 的既有换片路径（同格不同 bvid 会
        先 ``_release_binding(restart=False)`` 静默替换），不新建播放器路径。
        """
        if self.owner._shutting_down or binding is None:
            return
        parts = int(getattr(binding.item, "parts", 1) or 1)
        if binding.part + 1 < parts:
            # v1.0 需求：：同一个多 P 视频先播下一 P，播完所有 P 再进下一条。
            prepared = self.take_prefetched(binding, binding.item, binding.part + 1, int(binding.quality or 0))
            self.cancel_binding_media(binding)
            if binding.preview is not None:
                binding.preview.release()
                binding.preview = None
            self.unlink_paths([path for _label, path in binding.subtitles])
            binding.subtitles = []
            binding.subtitle_loaded = binding.subtitle_on = False
            binding.danmaku = None
            binding.cid = 0
            binding.part += 1
            from .preferences import read_preferences
            binding.resume_paused = not read_preferences(self.owner.context)['auto_play']
            binding.dm_cursor = 0
            binding.dm_last_pos = 0.0
            if prepared is not None:
                self.start_resolution(binding, start=0.0, prepared=prepared)
            else:
                self.start_resolution(binding, start=0.0)
            return
        item = self.owner._next_item_for(binding.bvid)
        if item is None:
            self.owner._notify_warning("已经是稍后再看列表里的最后一个视频了。")
            self.sync_next_button(binding)
            return
        tile = binding.tile
        if tile is None or tile not in self.owner._host_tiles():
            self.owner._notify_warning("这个格子已经不在了，无法播放下一个视频。")
            return
        prepared = self.take_prefetched(binding, item, 0, 0)
        if prepared is not None:
            self.start_vod_for_tile(tile, item, prepared=prepared)
        else:
            self.start_vod_for_tile(tile, item)

    def release_binding(self, binding, *, restart: bool) -> None:
        """结束一个点播绑定：停播放器、还原房间、清理控件。

        只用于合成房间**还在格子上**的路径（还原按钮/账号切换/卸载/
        起播替换）。格子已被宿主换房的清理走 :meth:`_detach_binding`。
        """
        tile = binding.tile
        alive = tile in self.owner._host_tiles()
        if alive:
            room = getattr(tile, "room", None) or {}
            owns_room = room.get("watchlater_bvid") == binding.bvid
            if not owns_room:
                self.detach_binding(binding)
                return
            self.owner._stop_host_tile(tile)
            self.owner._restore_tile_chrome(binding, quality=True)
            prev = binding.prev_room
            try:
                tile.set_room(prev if prev and prev.get("room_id") else None)
            except Exception:
                pass
            if (restart and not self.owner._shutting_down and prev
                    and prev.get("room_id") and prev.get("live")):
                self.owner._start_host_tile(tile)
        self.detach_binding(binding)

    def cancel_binding_media(self, binding) -> None:
        """Invalidate a binding's subtitle/danmaku deliveries before replacing it."""
        if self.next_prefetch is not None:
            self.next_prefetch.drop(id(binding))
        for name in ('warm_lease', 'pending_warm_lease'):
            lease = getattr(binding, name, None)
            if lease is not None:
                lease.close()
                setattr(binding, name, None)
        for contexts, workers in ((self.dm_context, self.dm_requests),
                                  (self.sub_context, self.sub_requests)):
            for request_id, ctx in tuple(contexts.items()):
                if ctx[0] is binding:
                    contexts.pop(request_id, None)
                    for worker in tuple(workers):
                        if worker.request_id == request_id:
                            worker.cancel()
        binding.dm_pending = binding._sub_pending = False

    def detach_binding(self, binding) -> None:
        """只收走插件自己的控件与状态，绝不碰格子当前的房间/播放器。

        合成房间从格子上消失（用户关闭这一路、或把别的直播拖进来替换）
        时，格子现在归宿主：停掉新播放器或把旧房间 set_room 回去，都会
        吃掉用户刚拖入的直播。这里只做插件侧清理。
        """
        if self.bindings.get(binding.bvid) is binding:
            self.bindings.pop(binding.bvid, None)
        self.owner._sync_order_wall_marks()
        self.owner._restore_tile_chrome(binding, quality=False)
        self.owner._vod_press.pop(id(binding.tile), None)
        self.cancel_binding_media(binding)
        if binding.request_id:
            self.vod_context.pop(binding.request_id, None)
        if binding.strip is not None and binding.preview is not None:
            try:
                binding.strip.set_preview_provider(None)
            except Exception:
                pass
        preview = binding.preview
        binding.preview = None
        if preview is not None:
            try:
                preview.release()
            except RuntimeError:
                pass
            except Exception as error:
                self.owner._log("preview release failed type=" + type(error).__name__)
        if binding._state_slot is not None:
            player = self.owner._host_player(binding.tile)
            if player is not None and player is binding._state_player:
                try:
                    player.stateChanged.disconnect(binding._state_slot)
                except Exception:
                    pass
            binding._state_slot = None
            binding._state_player = None
        for widget in (binding.strip, binding.card, binding.line, binding.badge):
            if widget is not None:
                try:
                    widget.hide()
                    widget.deleteLater()
                except RuntimeError:
                    pass
        self.owner._restore_order_pill(binding, getattr(binding.tile, "stream_badge", None))
        binding.strip = None
        binding.card = None
        binding.line = None
        binding.badge = None
        # v1.0 需求：：清理字幕临时文件 + 藏字幕按钮。
        self.unlink_paths([path for _label, path in binding.subtitles])
        binding.subtitles = []
        binding.subtitle_loaded = False
        binding.subtitle_on = False
        self.owner._sync_subtitle_control(binding.tile)
        timer = self.owner._click_timers.pop(id(binding.tile), None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        entry = self.owner._bar_progress.get(id(binding.tile))
        if entry is not None:
            try:
                entry["widget"].set_preview_provider(None)
                if entry.get("visible") or not entry["widget"].isHidden():
                    entry["visible"] = False
                    entry["widget"].hide()
                    entry["ctx"].invalidate_layout()
            except Exception:
                pass
        if self.owner.wall is not None and binding.cell is not None:
            try:
                self.owner.wall.bind(binding.cell, None)
            except Exception:
                pass
        if not self.bindings:
            if self.tick_timer is not None:
                self.tick_timer.stop()
            self.owner._sync_pip_filter()

    def ensure_tick_timer(self) -> None:
        if self.owner._shutting_down:
            return
        if self.tick_timer is None:
            # 宿主 api.Plugin 是普通 Python 类、不是 QObject，不能当 parent
            # （实际运行环境 QTimer(self) 会 TypeError）。无 parent，卸载时显式释放。
            self.tick_timer = QTimer()
            self.tick_timer.setInterval(300)
            self.tick_timer.timeout.connect(self.tick)
        if not self.tick_timer.isActive():
            self.tick_timer.start()

    @measure('playback_tick')
    def tick(self) -> None:
        if self.owner._shutting_down:
            return
        if not self.bindings:
            if self.tick_timer is not None:
                self.tick_timer.stop()
            return
        self.owner._sync_pip_filter()
        tiles = self.owner._host_tiles()
        for binding in list(self.bindings.values()):
            try:
                self.tick_binding(binding, tiles)
            except Exception as error:
                self.owner._log("tick failed type=" + type(error).__name__)
        self.owner._sync_order_wall_marks()

    def hide_pause_overlay(self, tile) -> None:
        """v1.0 任务 5：结束态不留「已暂停」浮层。

        宿主 ``Tile.set_paused(True)``（widgets.py）会
        ``pause_overlay.start()``，结束卡底下就叠着「已暂停」。这里只停
        浮层，保留 ``tile.paused`` 状态本身 —— 宿主 watchdog
        （player.py ``if self.owner.paused: return``）与重连判定都还靠它安静。
        """
        overlay = getattr(tile, "pause_overlay", None)
        if overlay is None:
            return
        try:
            overlay.stop()
        except Exception:
            pass
        try:
            overlay.hide()
        except RuntimeError:
            pass

    def cancel_host_retries(self, tile) -> None:
        """v1.0 任务 6：取消宿主已排队的「画面静止/断流」重连。

        宿主的冻结恢复（``playback_recovery.picture_frozen``，宿主
        playback_recovery.py）会直接 ``start_tile`` 重新起播 —— 那一步
        就是状态被打回 ``connecting``（用户看到的「连接中」）。它在
        ``tile.paused`` 时本会早退，但全屏进出重排可能把 paused 复位，所以
        进入 ended 时主动把已排队的刷新清掉，避免结束后又被拉起来连一次。
        """
        window = self.owner._host_window()
        recovery = getattr(window, "playback_recovery", None)
        if recovery is None:
            return
        for name in ("clear_freeze", "cancel_retry"):
            method = getattr(recovery, name, None)
            if not callable(method):
                continue
            try:
                method(tile)
            except Exception:
                pass

    def enter_ended(self, binding, tile, player) -> None:
        """v1.0 任务 5/6：进入结束态只发生一次，之后这条绑定彻底停住。

        * 任务 5：不上「已暂停」浮层，结束卡是唯一可见 UI。
        * 任务 6：全屏 EOF 之后反复「连接中」的两条入口都要堵住 ——
         ① 插件自己的续播分支（全屏重排释放播放器 → ``player is None``
          → ``_start_resolution``）；② 宿主的冻结恢复
          （``start_tile`` → 状态打回 ``connecting``）。前者靠
          :meth:`_tick_binding` 的 ended 早退，后者靠
          :meth:`_cancel_host_retries`。这里先清掉所有待续播状态。
        """
        if binding.state == 'ended':
            return
        binding.state = "ended"
        binding.ended = True
        binding.pending_seek = 0.0
        binding.pending_audio = ""
        binding.audio_attempts = 0
        binding.resume_paused = False
        try:
            tile.set_paused(True)
            player.set_paused(True)
        except Exception:
            pass
        self.cancel_host_retries(tile)
        self.hide_pause_overlay(tile)
        if binding.strip is not None:
            try:
                binding.strip.hide()
            except RuntimeError:
                binding.strip = None
        if binding.card is not None:
            binding.card.sync_geometry(tile)
            binding.card.show_ended(self.has_next(binding))
        self.owner._sync_position_badge(binding)
        self.owner._sync_subtitle_control(tile)
        from .preferences import read_preferences
        if read_preferences(self.owner.context)['autoplay'] and self.has_next(binding):
            QTimer.singleShot(0, lambda: self.auto_advance(binding))

    def auto_advance(self, binding):
        from .preferences import read_preferences
        if (not self.owner._shutting_down and self.bindings.get(binding.bvid) is binding
                and binding.state == 'ended' and read_preferences(self.owner.context)['autoplay']
                and self.has_next(binding)):
            self.next_binding(binding)

    def maybe_prefetch(self, binding):
        from .preferences import read_preferences
        enabled = read_preferences(self.owner.context)['prefetch_next']
        if not enabled:
            if self.next_prefetch is not None:
                self.next_prefetch.drop(id(binding))
            return
        if binding.state != 'playing' or binding.duration <= 0 or binding.duration - binding.progress > 30:
            return
        parts = int(getattr(binding.item, 'parts', 1) or 1)
        if binding.part + 1 < parts:
            item, part, quality = binding.item, binding.part + 1, int(binding.quality or 0)
        else:
            item, part, quality = self.owner._next_item_for(binding.bvid), 0, 0
            if item is None or item.bvid in self.bindings:
                return
        cookies = self.cookie_snapshot()
        if self.next_prefetch is None:
            from .next_prefetch import NextPrefetch
            self.next_prefetch = NextPrefetch(QApplication.instance())
        key = (self.account_generation, item.bvid, part, quality)
        self.next_prefetch.prepare(id(binding), key, cookies, int(getattr(item, 'cid', 0)) if part == 0 else 0)

    def take_prefetched(self, binding, item, part, quality):
        from .preferences import read_preferences
        if self.next_prefetch is None or not read_preferences(self.owner.context)['prefetch_next']:
            return None
        return self.next_prefetch.take(id(binding), (self.account_generation, item.bvid, part, quality))

    def configure_preview(self, binding, enabled):
        if not enabled and binding.preview is not None:
            from .card_preview import retire_preview
            retire_preview(binding.preview)
            binding.preview = None
        elif enabled and binding.preview is None:
            from .vod_preview import VodPreviewProvider
            binding.preview = VodPreviewProvider(binding.bvid, binding.cid)
        if binding.strip is not None:
            binding.strip.set_preview_provider(binding.preview)
        entry = self.owner._bar_progress.get(id(binding.tile))
        if entry is not None:
            entry['widget'].set_preview_provider(binding.preview)

    def tick_binding(self, binding, tiles) -> None:
        bvid = binding.bvid
        tile = next(
            (t for t in tiles
             if isinstance(getattr(t, "room", None), dict)
             and t.room.get("watchlater_bvid") == bvid),
            None,
        )
        if tile is None:
            if binding.state == "resolving":
                if binding.request_id not in self.vod_context:
                    self.detach_binding(binding)  # 请求已死，绑定成孤儿
                return
            # 格子被关/被直播拖入替换：合成房间已不在任何格子上，
            # 格子当前的房间/播放器归宿主，只 detach 插件控件。
            self.detach_binding(binding)
            return
        if tile is not binding.tile:
            # 秒切回退路径：合成房间搬到了别的格子 → 控件跟着搬。
            old_cell = binding.cell
            binding.tile = tile
            for widget in (binding.strip, binding.card, binding.line,
                           binding.badge):
                if widget is not None:
                    try:
                        widget.setParent(tile)
                    except RuntimeError:
                        pass
            new_cell = self.owner._resolve_cell_from_tile(tile)
            if new_cell is None and self.owner.wall is not None:
                # 需求：加固：宿主链暂时查不到（布局过渡期）就按
                # _cell_tiles 反查，绝不让 binding.cell 留在旧格子上——
                # 否则旧格子下一次起播的 _cancel_cell_requests 会误杀
                # 本格子的续播请求。
                try:
                    for candidate in list(self.owner.wall._cells.values()):
                        if self.owner._cell_tiles.get(id(candidate)) is tile:
                            new_cell = candidate
                            break
                except Exception:
                    new_cell = None
            if new_cell is not None:
                binding.cell = new_cell
                if self.owner.wall is not None:
                    try:
                        if old_cell is not None and old_cell is not new_cell:
                            self.owner.wall.bind(old_cell, None)
                        self.owner.wall.bind(new_cell, binding.item)
                    except Exception:
                        pass
            self.owner._sync_tile_widgets(tile)
        # 需求：：宿主 rewire/relayout 可能重新显示录制按钮，发现可见就再藏。
        try:
            rec = getattr(tile, "recording_button", None)
            if rec is not None and rec.isVisible():
                tile.set_recording_available(False)
        except Exception:
            pass
        if binding.state == "ended":
            # v1.0 任务 6：结束态是终止态，只做最小几何同步。
            # 这里**绝不**再走 _start_resolution / _play_on / 续播分支：
            # 全屏重排释放播放器后，``player is None`` 那条恢复分支正是
            # 「反复连接中」的入口（宿主 _play_on 会把状态打回 connecting）。
            self.hide_pause_overlay(tile)
            self.owner._sync_fullscreen_state(tile)
            self.sync_next_button(binding)
            card = binding.card
            if card is not None:
                try:
                    if card.isVisible():
                        card.sync_geometry(tile)
                except RuntimeError:
                    binding.card = None
            return
        player = self.owner._host_player(tile)
        if player is None:
            # 布局隐藏确实会释放播放器，可见后才按进度重建。正常拖格
            # 已在 Drop 阶段交换 Tile 对象，不会进入这个恢复分支。
            if binding.state in ("playing", "paused"):
                try:
                    visible = tile.isVisible()
                except Exception:
                    visible = False
                if visible:
                    binding.resume_paused = binding.state == "paused"
                    try:
                        tile.set_status("正在续播…")
                    except Exception:
                        pass
                    # 隐藏很久的签名URL可能过期；真正被释放后重新解析。
                    self.start_resolution(binding, start=binding.progress)
            return
        prop = getattr(player, "_prop", None)
        if not callable(prop):
            return
        duration = float(prop("duration", 0) or 0)
        position = prop("time-pos", None)
        position = float(position) if position is not None else None
        loaded = duration > 0 or position is not None
        if loaded:
            self.apply_subtitle(binding, player)
        if loaded and binding.resume_paused:
            binding.resume_paused = False
            try:
                tile.set_paused(True)
                player.set_paused(True)
                binding.state = "paused"
            except Exception:
                pass
        # DASH 音轨只能在文件加载后挂（v1.0 实际运行环境证据）。
        if binding.pending_audio and loaded:
            if self.mpv_command(
                player, "audio-add", binding.pending_audio, "select"
            ):
                binding.pending_audio = ""
            else:
                binding.audio_attempts += 1
                if binding.audio_attempts >= 3:
                    binding.pending_audio = ""  # 有画无声胜过没画
        # Keep the saved time until the asynchronous restore seek is observed.
        seeking = bool(prop('seeking', False))
        if binding.pending_seek > 0 and loaded:
            target = binding.pending_seek
            if position is not None and position >= target - 1.0 and not seeking:
                binding.pending_seek = 0.0
                binding.seek_issued = False
            elif not binding.seek_issued:
                binding.seek_issued = True
                if not self.mpv_command(player, "seek", target, "absolute"):
                    binding.pending_seek = 0.0
                    self.owner._notify_warning('恢复进度定位失败，可手动拖动进度条。')
        position_ready = position is not None and not binding.pending_seek and not seeking
        # EOF：走宿主暂停 API 让 watchdog 安静，再上结束卡。
        if binding.state == "playing" and bool(prop("eof-reached", False)):
            self.enter_ended(binding, tile, player)
        # 宿主暂停按钮/快捷键改了 tile.paused → 同步绑定状态。
        try:
            paused = bool(tile.paused)
        except Exception:
            paused = False
        if binding.state == "playing" and paused:
            binding.state = "paused"
        elif binding.state == "paused" and not paused:
            binding.state = "playing"
        # The host watchdog returns early while paused, so it cannot acknowledge
        # a frame loaded in that state. Clear startup feedback only for a real
        # configured video frame; metadata alone does not prove readiness.
        if (paused and position_ready and prop("vo-configured", False)
                and not getattr(tile, "_player_active", False)):
            tile.set_video_active(True)
            tile.set_buffering(False)
            tile.set_status("")
            tile.set_paused(True)
        from .acceptance_trace import playback_sample
        playback_sample(binding, player)
        if position_ready:
            binding.progress = position
        if duration > 0:
            binding.duration = duration
        self.maybe_prefetch(binding)
        self.owner._sync_fullscreen_state(tile)
        self.owner._sync_subtitle_control(tile)
        # v1.0 任务 3/8：列表刷新/删除后序号与总数要跟着变。
        self.owner._sync_position_badge(binding)
        entry = self.owner._bar_progress.get(id(tile))
        if entry is not None and entry.get("visible"):
            widget = entry.get("widget")
            try:
                if widget is not None:
                    widget.set_duration(binding.duration or binding.item.duration)
                    widget.set_position(binding.progress)
            except RuntimeError:
                self.owner._bar_progress.pop(id(tile), None)
        line = binding.line
        if line is not None:
            try:
                if line.isVisible():
                    if binding.duration > 0:
                        line.set_ratio(max(0.0, min(
                            1.0, binding.progress / binding.duration)))
                    line.sync_geometry(tile)
            except RuntimeError:
                binding.line = None
        card = binding.card
        if card is not None:
            try:
                if card.isVisible():
                    card.sync_geometry(tile)
            except RuntimeError:
                binding.card = None
        self.feed_danmaku(binding, tile, position if position_ready else None, paused)

    def feed_danmaku(self, binding, tile, position, paused: bool) -> None:
        """按播放进度把点播弹幕喂给宿主原生引擎（需求 5）。

        渲染/轨道/速度/开关全用宿主 ``tile.video_danmaku``；引擎满轨道或
        弹幕开关关闭时 add_event 自己拒绝，这里像直播一样丢弃不积压。
        """
        if not binding.danmaku or paused or binding.state != "playing":
            return
        if position is None:
            return
        engine = getattr(tile, "video_danmaku", None)
        add_event = getattr(engine, "add_event", None)
        if not callable(add_event):
            return
        if position < binding.dm_last_pos - 2.0:
            # 后退 seek：清屏并从新位置重新对表。
            try:
                engine.clear_comments()
            except Exception:
                pass
            binding.dm_cursor = bisect.bisect_left(
                [entry[0] for entry in binding.danmaku], position
            )
        horizon = position + 0.25
        comments = binding.danmaku
        cursor = binding.dm_cursor
        while cursor < len(comments) and comments[cursor][0] <= horizon:
            moment, mode, color, text = comments[cursor]
            cursor += 1
            if moment < binding.dm_last_pos - 0.5:
                continue  # 前进 seek 跳过的历史弹幕不补喷
            try:
                add_event({"kind": "danmaku", "text": text, "color": color})
            except Exception:
                pass
        binding.dm_cursor = cursor
        binding.dm_last_pos = position
