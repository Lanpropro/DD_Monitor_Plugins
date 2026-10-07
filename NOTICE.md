# 来源与第三方说明

国内和海外直播平台插件提取自 https://github.com/Lanpropro/DD_Monitor_CE ，
源码基线为 `4bf84bc`，保留原项目的 GNU Lesser General Public License v2.1 文件。

直播流解析使用宿主提供的 Streamlink 8.6.1（BSD-2-Clause），
上游：https://github.com/streamlink/streamlink 。
播放、转流、图片及聊天由宿主接口和依赖支持，安装包不包含 VLC、FFmpeg 或第三方 Python 运行库。

国内直播协议参考 biliup，上游：https://github.com/biliup/biliup 。
保留原有 MIT 声明于 `plugins/domestic_live/biliup-MIT.txt`；宿主中相关协议实现
及其许可继续由软件仓库维护。

插件使用平台公开直播入口，不读取浏览器账号或 Cookie，不发送聊天。
详细支持范围见各插件 README。

比赛二路同步插件提取自同一软件源码的 1.0 发布基线。图像解码与匹配使用宿主环境的 PyAV（BSD-3-Clause）、NumPy（BSD-3-Clause）及 FFmpeg；安装包不包含这些第三方运行库。
