<div align="center">

# DD监控室CE · 插件

为 DD监控室CE 扩展直播平台、比赛二路、B站互动与网络功能。

[![DD监控室CE](https://img.shields.io/badge/DD监控室CE-插件扩展-5cd6ff)](https://github.com/Lanpropro/DD_Monitor_CE)
[![许可](https://img.shields.io/badge/License-LGPL--2.1-blue.svg)](LICENSE)

[下载插件](https://github.com/Lanpropro/DD_Monitor_Plugins/releases) · [下载本体](https://github.com/Lanpropro/DD_Monitor_CE/releases/latest) · [直播平台](#直播平台) · [功能插件](#功能插件) · [安装与更新](#安装与更新) · [开发与贡献](#开发与贡献)

</div>

## 直播平台

按平台安装和更新。六个独立平台插件支持 **DD监控室CE 0.8 或更新版本**，建议先升级至 **0.8 hotfix.1**，以获得完整的插件商店识别支持。

| 平台 | 功能 | 维护 | 下载 |
| --- | --- | --- | --- |
| <img src="plugins/huya_live/icon.png" alt="" width="20" height="20"> [虎牙直播](plugins/huya_live/README.md) | 直播播放、画质选择、账号关注、弹幕互动 | [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/huya-live-v1.0) |
| <img src="plugins/douyu_live/icon.png" alt="" width="20" height="20"> [斗鱼直播](plugins/douyu_live/README.md) | 直播播放、画质选择、账号关注、弹幕互动、礼物互动 | [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/douyu-live-v1.0) |
| <img src="plugins/douyin_live/icon.png" alt="" width="20" height="20"> [抖音直播](plugins/douyin_live/README.md) | 直播播放、画质选择、账号关注、官网弹幕输入（实验性） | [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/douyin-live-v1.0) |
| <img src="plugins/twitch_live/icon.png" alt="" width="20" height="20"> [Twitch直播](plugins/twitch_live/README.md) | 公开直播播放、画质选择、账号关注 | [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/twitch-live-v1.0) |
| <img src="plugins/youtube_live/icon.png" alt="" width="20" height="20"> [YouTube直播](plugins/youtube_live/README.md) | 公开直播播放、画质选择、账号关注 | [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/youtube-live-v1.0) |
| <img src="plugins/chzzk_live/icon.png" alt="" width="20" height="20"> [CHZZK直播](plugins/chzzk_live/README.md) | 公开直播播放、画质选择、弹幕观看 | [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/chzzk-live-v1.0) |
| <img src="plugins/qie_live/icon.png" alt="" width="20" height="20"> [企鹅体育](plugins/qie_live/README.md) | 公开直播播放、固定 720p、只读弹幕 | [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/qie-live-v1.0) |

<details>
<summary>旧版组合包与迁移</summary>

已安装组合包的用户可以逐个平台迁移。配套本体保留原有账号、关注、房间 ID 和分组，并让独立包接管对应平台。详细步骤见[迁移与回滚说明](docs/development/platform-packages.md)。

| 兼容组合包 | 平台 | 维护 | 下载 |
| --- | --- | --- | --- |
| [国内直播平台](plugins/domestic_live/README.md) | <img src="docs/assets/platforms/huya.png" alt="虎牙" width="20" height="20"> <img src="docs/assets/platforms/douyu.png" alt="斗鱼" width="20" height="20"> <img src="docs/assets/platforms/douyin.png" alt="抖音" width="20" height="20"> | [@Lanpropro](https://github.com/Lanpropro) | [v2.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/domestic-live-v2.0) |
| [海外直播平台](plugins/global_live/README.md) | <img src="docs/assets/platforms/twitch.png" alt="Twitch" width="20" height="20"> <img src="docs/assets/platforms/youtube.png" alt="YouTube" width="20" height="20"> | [@Lanpropro](https://github.com/Lanpropro) | [v1.2](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2) |

</details>

## 功能插件

| 插件 | 功能 | 作者与维护 | 下载 |
| --- | --- | --- | --- |
| [比赛二路同步](plugins/match_sync/README.md) | 对齐比赛主画面与多路解说，混合声音及弹幕。使用其他平台前先启用对应平台插件。 | 维护 [@Lanpropro](https://github.com/Lanpropro) | [v1.7.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.7.0) |
| <img src="docs/assets/platforms/bilibili.png" alt="" width="20" height="20"> [B站直播互动](plugins/bili_live/README.md) | 使用本体登录，发送弹幕和表情，浏览礼物目录及背包。 | 作者 [@reon-nynes](https://github.com/reon-nynes)，兼容维护 [@Lanpropro](https://github.com/Lanpropro) | [本体内置](https://github.com/Lanpropro/DD_Monitor_CE/releases/tag/v0.9.1) |
| <img src="docs/assets/platforms/bilibili.png" alt="" width="20" height="20"> [B站稍后再看](plugins/bili_watchlater/README.md) | 浏览稍后再看，将视频放入画面墙。需要本体 **0.8 热修复 1** 或具备相同接口的更新版本。 | 作者[@Mrhaimian](https://github.com/Mrhaimian)，兼容维,UI制作 [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/bili-watchlater-v1.0) |
| [网络代理](plugins/proxy_control/README.md) | 导入订阅与节点，按需启动代理，查看连接、测速与切换配置。 | 维护 [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/proxy-control-v1.0) |
| <img src="plugins/m3u_live/icon.png" alt="" width="20" height="20"> [IPTV Play](plugins/m3u_live/README.md) | 电视预设、M3U 播放列表及 HLS 导入。需要配套宿主的通用频道接口。 | [@Lanpropro](https://github.com/Lanpropro) | [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/m3u-live-v1.0) |

## 安装与更新

**插件商店安装**：在本体中打开「设置 → 插件 → 插件商店」，选择插件，按提示安装、启用并重启。插件安装与更新各自独立。

**手动导入**：从上表或 [GitHub Releases](https://github.com/Lanpropro/DD_Monitor_Plugins/releases) 下载对应版本的插件 ZIP，在「设置 → 插件」点击「装载插件…」或拖入 ZIP，启用后按提示重启。请选择 Release 附件里的单插件 ZIP，GitHub 自动生成的 Source code ZIP 是整个仓库的源码。

<details>
<summary>数据目录与旧版手动更新</summary>

手动复制时，将插件文件夹放入当前数据目录的 `plugins_user/<ID>/`。安装版默认使用当前用户的 AppData，便携版和源码版默认使用程序旁的目录，实际路径以本体「数据与存储」为准。手动更新同 ID 的插件前，先退出本体，再按对应 Release 的说明操作。

源码运行使用本体依赖，安装版与便携版使用本体随附的运行环境。平台请求沿用本体网络及代理配置。

</details>

## 开发与贡献

[文档中心](docs/README.md) · [插件开发指南](docs/development/README.md) · [开发与贡献](CONTRIBUTING.md) · [增量更新](docs/changes/README.md) · [发布记录](docs/releases/README.md) · [仓库目录](docs/repository-layout.md) · [展示网站](WEBSITE.md)

插件源码位于 `plugins/<ID>/`。欢迎通过 [Issue](https://github.com/Lanpropro/DD_Monitor_Plugins/issues) 反馈问题，或提交 Pull Request 贡献新插件。反馈时附上插件及本体版本、操作步骤和实际结果。

<details>
<summary>源码版本与本地打包</summary>

下表记录当前分支的源码版本。公开安装包版本见上方下载入口。

| 插件 | ID | 源码版本 |
| --- | --- | --- |
| 虎牙直播 | `huya_live` | 1.0 |
| 斗鱼直播 | `douyu_live` | 1.0 |
| 抖音直播 | `douyin_live` | 1.0 |
| Twitch直播 | `twitch_live` | 1.0 |
| YouTube直播 | `youtube_live` | 1.1 |
| CHZZK直播 | `chzzk_live` | 1.0 |
| 比赛二路同步 | `match_sync` | 1.7.0 |
| B站直播互动 | `bili_live` | 1.1 |
| B站稍后再看 | `bili_watchlater` | 1.0 |
| 网络代理 | `proxy_control` | 1.0 |
| IPTV Play | `m3u_live` | 1.0 |
| 企鹅体育 | `qie_live` | 1.0 |
| 国内直播平台（组合包） | `domestic_live` | 2.0 |
| 海外直播平台（组合包） | `global_live` | 1.2 |

在干净的源码工作树中构建并校验。仅打包指定插件时传入 `--plugin <ID>`。

```powershell
python tools/build_plugins.py
python tests/check_packages.py --host ../DD_Monitor_CE
```

单插件 ZIP 输出到 `dist/`，包内随附许可与来源说明。标签、ZIP 与正式发布校验流程见[贡献指南](CONTRIBUTING.md#新版本发布门槛)。

</details>

## 致谢与许可

感谢所有为 DD监控室CE 插件贡献代码、修复问题和完善文档的朋友。

[![DD监控室CE 插件贡献者](https://contrib.rocks/image?repo=Lanpropro/DD_Monitor_Plugins)](https://github.com/Lanpropro/DD_Monitor_Plugins/graphs/contributors)

本仓库从 [DD监控室CE](https://github.com/Lanpropro/DD_Monitor_CE) 的 `4bf84bc` 基线提取并独立维护，采用 [GNU LGPL v2.1](LICENSE)。第三方来源与声明见 [NOTICE](NOTICE.md)。
