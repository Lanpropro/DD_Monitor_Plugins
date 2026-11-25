# YouTube直播独立插件 v1.0

插件ID：`youtube_live`，版本1.0。只注册`youtube`，可以独立启用、更新与回滚。最低支持 DD Monitor CE 0.8。请先升级本体，再安装插件。

账号、链接和基础HLS协议复用`plugins/global_live/`对应职责模块；高画质取流、连续分片、音视频复用和运行时准备只在本独立插件目录维护。构建工具按`tools/platform_bundles.json`内置账号及公共组件，安装后不需要组合包或其他平台插件。不要直接复制本目录作为安装包。

旧组合包仍保留兼容。新插件首次启动继承旧启用状态和声明的设置，房间ID、账号身份、关注、分组及画质不改名；安装新插件后，对应平台由它接管，禁用后不会由组合包重新启用。

详情与验证见[平台独立包迁移](../../docs/development/platform-packages.md)。构建：`python tools/build_plugins.py`，验证：`python tests/check_platform_packages.py --host <宿主目录>`。安装包：`youtube_live-1.0.zip`。

支持公开直播的VP9高画质分片与AAC音频，经FFmpeg只复用、不转码交给宿主播放器。档位取自真实格式，含源提供的1440p60；自动从最低档起步，手选保留实际分辨率。1080p及以下优先H.264；悬停预览优先沿用低码率HLS。

首次打开或自动模式下高画质来源不可用时，会尝试原有HLS路径；已经展示的手选高画质失败会明确报错。当前代理出现过元数据连接与分片读取失败；50秒媒体验证不等于所有网络下的完整播放验收。

需要包含`retain_media_source`接口的宿主（建议使用 0.8 热修复 1 或更新版本）；旧宿主继续使用原HLS。首次高画质播放自动准备固定版本yt-dlp 2026.11.23和Node 24.11.0（Windows x64），通过现有代理下载官方文件并核对SHA256，无需用户安装Python包、Node或手填Token。不读取浏览器资料、Cookie或现有登录令牌；遇到YouTube访问验证会给出固定错误，不承诺所有网络均可匿名播放。

私有来源校验组件为bgutil 2.0.2，随包保存源码、锁文件与许可证；只启用脚本提供器，不连接本机未知HTTP服务。缓存位于`%LOCALAPPDATA%/DDMonitorCE/YouTubeMedia`。重建组件：检出`26475e9d3665b972c4aeb83b4dbaafcf64b88438`，从server/package.json移除可选canvas原生依赖并更新锁文件，`npm ci --ignore-scripts`、`node node_modules/typescript/bin/tsc`，再运行`python tools/build_youtube_provider.py --provider <源码目录>`。实际变更说明在组件内`SOURCE.json`，未用canvas原生扩展；下载地址、SHA256在`youtube_runtime.py`中固定。

Issue #235实播验证及限制见[增量记录](../../docs/changes/youtube_live.md)。
