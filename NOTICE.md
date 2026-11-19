# 来源与第三方说明

国内和海外直播平台插件提取自 https://github.com/Lanpropro/DD_Monitor_CE ，
源码基线为 `4bf84bc`，保留原项目的 GNU Lesser General Public License v2.1 文件。

直播流解析使用宿主提供的 Streamlink 8.6.1（BSD-2-Clause），
上游：https://github.com/streamlink/streamlink 。
播放、转流、图片及聊天由宿主接口和依赖支持，安装包不包含 VLC、FFmpeg 或第三方 Python 运行库。

国内直播协议参考 biliup，上游：https://github.com/biliup/biliup 。
保留原有 MIT 声明于 `plugins/domestic_live/biliup-MIT.txt`；宿主中相关协议实现
及其许可继续由软件仓库维护。

网络代理插件为保留订阅 DNS／hosts 配置，私有携带 PyYAML 6.0.3 的纯 Python 源码（MIT），
固定提交 `49790e73684bebad1df05ef8d828fa12f685bffb`，
来源 https://github.com/yaml/pyyaml 。原始许可证随 `proxy_control/vendor/yaml/LICENSE` 提供，
未包含 LibYAML C 扩展，不要求修改宿主 Python 环境。

插件使用平台公开直播入口，不读取浏览器账号或 Cookie，不发送聊天。
详细支持范围见各插件 README。

YouTube独立插件的高画质路径使用独立进程运行yt-dlp（Unlicense及其二进制分发依赖许可）、Node.js（MIT及上游依赖许可）和bgutil-ytdlp-pot-provider 2.0.2（GPL-3.0-only）。固定来源与SHA256见`youtube_runtime.py`；bgutil上游为https://github.com/Brainicism/bgutil-ytdlp-pot-provider，组件源码、package-lock及依赖许可保留在`youtube_live/youtube-provider.zip`。只启用脚本提供器。高画质分片的协议设计参考Fy1Sync提交`745c53b2dd58f6559f29e7b8c6e083ec64bd431f`，实现按本项目宿主/插件职责独立编写。

比赛二路同步插件提取自同一软件源码的 1.0 发布基线。图像解码与匹配使用宿主环境的 PyAV（BSD-3-Clause）、NumPy（BSD-3-Clause）及 FFmpeg；安装包不包含这些第三方运行库。
