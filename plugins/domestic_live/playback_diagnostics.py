"""Redacted protocol and player observation, separate from plugin lifecycle."""
import re
import time
from ddm import plugins as api
from .diagnostic_errors import diagnostic_reason

class PlaybackDiagnostics:
    def __init__(self, host):
        self.host = host

    _diagnostic_reason = staticmethod(diagnostic_reason)

    def _enable_diagnostics(self, context, platform):
        platform._diagnostic_log = context.log
        for operation in ("room_info", "play_url"):
            original = getattr(platform, operation)

            def diagnosed(*args, _original=original, _operation=operation, **kwargs):
                started = time.monotonic()
                try:
                    return _original(*args, **kwargs)
                except Exception as error:  # noqa: BLE001
                    try:
                        canonical = platform.normalize(args[0] if args else kwargs.get("room_id", ""))
                    except (ValueError, TypeError):
                        canonical = ""
                    room = canonical if re.fullmatch(r"[a-z]+:[A-Za-z0-9_-]{1,80}", canonical) else "分享链接或无效输入"
                    mapped = (f" UID映射={'有' if platform._anchor_uids.get(canonical) else '无'}"
                              if platform.kind == "douyin" else "")
                    context.log(f"[平台诊断] 平台={platform.kind} 阶段={_operation} 房间={room}"
                                f"{mapped} 耗时={time.monotonic() - started:.1f}s "
                                f"原因={self._diagnostic_reason(error)}")
                    raise

            setattr(platform, operation, diagnosed)

    @staticmethod
    def _player_error_category(prefix, text):
        lower = text.lower()
        status = re.search(r"HTTP(?:/\d(?:\.\d)?| error)?\s+([45]\d\d)", text, re.IGNORECASE)
        if status:
            return "HTTP_" + status[1]
        if any(word in lower for word in ("certificate", "tls", "ssl")):
            return "TLS_OR_CERTIFICATE"
        if any(word in lower for word in ("getaddrinfo", "resolve", "name or service not known")):
            return "DNS"
        if "timed out" in lower or "timeout" in lower:
            return "TIMEOUT"
        if prefix.startswith(("vd", "ffmpeg/video")) or "decoder" in lower or "hwdec" in lower:
            return "VIDEO_DECODE"
        if prefix.startswith("vo") or any(word in lower for word in ("direct3d", "d3d11", "swapchain")):
            return "VIDEO_OUTPUT"
        if prefix.startswith("ao") or "wasapi" in lower:
            return "AUDIO_OUTPUT"
        if "invalid data" in lower or prefix.startswith("demux"):
            return "DEMUX_OR_FORMAT"
        if any(word in lower for word in ("connection", "network", "failed to open", "protocol")):
            return "NETWORK_OR_OPEN"
        return "OTHER_MPV_ERROR"

    def _detach_player_diagnostic(self, tile):
        record = self.host._diagnostic_players.pop(tile, None)
        if record is None:
            return
        try:
            record["core"].unregister_event_callback(record["callback"])
        except (ValueError, RuntimeError):
            pass
        try:
            record["player"].stateChanged.disconnect(record["state_callback"])
        except (RuntimeError, TypeError):
            pass

    def on_event(self, event, payload):
        if not getattr(self.host, "_diagnostic_enabled", False):
            return
        if event == api.EVENT_CLOSING:
            for tile in list(self.host._diagnostic_players):
                self._detach_player_diagnostic(tile)
            return
        tile = payload.get("tile")
        if event == api.EVENT_TILE_STOPPED:
            self._detach_player_diagnostic(tile)
            return
        if event != api.EVENT_STREAM_RESOLVED:
            return
        source = payload.get("source")
        room = str(getattr(source, "room_id", ""))
        if not re.fullmatch(r"(?:huya|douyu|douyin):[A-Za-z0-9_-]{1,80}", room):
            self._detach_player_diagnostic(tile)
            return
        context = self.host.context
        player = getattr(context.window, "players", {}).get(tile)
        core = getattr(player, "player", None)
        if core is None or not callable(getattr(core, "register_event_callback", None)):
            context.log(f"[平台诊断] 阶段=player_attach 房间={room} 结果=播放器不支持事件观察")
            return
        previous = self.host._diagnostic_players.get(tile)
        if previous and previous["core"] is core:
            previous["room"] = room
            previous["seen"].clear()
            return
        self._detach_player_diagnostic(tile)
        record = {"core": core, "player": player, "room": room, "seen": set()}

        def callback(event):
            if event.event_id.value != 2:  # libmpv LOG_MESSAGE；只观察现有 error 日志，不改变日志级别。
                return
            data = event.data
            if data.level not in ("error", "fatal"):
                return
            category = self._player_error_category(data.prefix, data.text)
            if category not in record["seen"]:
                record["seen"].add(category)
                context.log(f"[平台诊断] 阶段=player_error 房间={record['room']} 类别={category}")

        def state_callback(state):
            if state in ("playing", "buffering", "error"):
                mode = player.decode_mode if player.decode_mode in ("auto", "none", "d3d11va", "nvdec", "dxva2") else "other"
                context.log(f"[平台诊断] 阶段=player_state 房间={record['room']} 状态={state} 解码方式={mode}")

        record.update(callback=callback, state_callback=state_callback)
        core.register_event_callback(callback)
        player.stateChanged.connect(state_callback)
        self.host._diagnostic_players[tile] = record
        context.log(f"[平台诊断] 阶段=player_attach 房间={room} 结果=已开始观察播放器")
