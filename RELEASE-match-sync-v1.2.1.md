# 比赛二路同步 v1.2.1

- 以已发布的 `match_sync-1.2.zip` 为基线，保留持续同步、缓冲恢复、快捷键及对照框选修复。
- PCM 左右声道处理由插件自身提供，不再导入 `ddm.audio_output.route_pcm_s16_stereo`，避免主程序迁移 libmpv 后插件因缺少旧接口而加载失败。
- 保持原有左右声道平均混音、仅左/仅右输出、16 位满量程和不完整帧校验行为；画面墙继续由宿主播放器输出，比赛二路继续使用独立 Qt 音频混音。
- 兼容现有 VLC 主程序与 [DD_Monitor_CE PR #13](https://github.com/Lanpropro/DD_Monitor_CE/pull/13)；主程序版本和原有 PyAV、NumPy、FFmpeg 要求不变。

下载 `match_sync-1.2.1.zip`，按插件更新流程安装并重启。配置和已保存的用户数据无需手动修改。

本仓库 main 中的比赛二路源码仍为 1.1；本次先同步官方 1.2 发行包，再添加上述兼容修复。1.2 基线 ZIP 的 SHA-256 为 `b52b6053fbda18014a77a463306846179c1713ae9d309ceb0ae85ecddd463ef7`。
