# YouTube 直播 v1.1

- 更新至 [yt-dlp 2026.11.23](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.11.23)，修复 YouTube 更新播放器脚本后部分直播的 2K／4K 档位从画质列表消失的问题。高画质组件会在首次使用时自动下载并校验 SHA-256。[上游问题](https://github.com/yt-dlp/yt-dlp/issues/18123)
- 在一场公开直播中，从低画质分别切换至 2K 和 4K，确认实际解码为 2560×1440 和 3840×2160，均为 VP9、60 FPS，并收到音频。可用档位取决于直播源和网络。多路同时播放 4K 的流畅度尚未验收。

在插件商店更新「YouTube 直播」并重启监控室。离线安装包为 `youtube_live-1.1.zip`。最低支持 DD监控室CE 0.8，建议使用 0.8 热修复 1 或更新版本。
