<div align="center">

# DD监控室CE · 插件

让喜欢的直播，多一种看法。

为 DD监控室CE 扩展直播平台、比赛二路与直播互动。

[![DD监控室CE](https://img.shields.io/badge/DD监控室CE-插件扩展-5cd6ff)](https://github.com/Lanpropro/DD_Monitor_CE) [![插件](https://img.shields.io/badge/插件-4_款-5cd6ff)](#插件一览) [![许可](https://img.shields.io/badge/License-LGPL--2.1-blue.svg)](LICENSE)

[下载插件](https://github.com/Lanpropro/DD_Monitor_Plugins/releases) · [插件一览](#插件一览) · [安装与更新](#安装与更新) · [开发与文档](#开发与文档) · [反馈问题](https://github.com/Lanpropro/DD_Monitor_Plugins/issues)

</div>

## 插件一览

按需安装，独立启用。每个插件分别维护版本，选择你需要的平台与功能即可。

<table>
<tr>
<td width="50%" valign="top">
<h3><a href="plugins/domestic_live/README.md">国内直播平台</a></h3>
<p><img src="docs/assets/platforms/huya.png" alt="虎牙" width="24" height="24"> &nbsp; <img src="docs/assets/platforms/douyu.png" alt="斗鱼" width="24" height="24"> &nbsp; <img src="docs/assets/platforms/douyin.png" alt="抖音" width="24" height="24"></p>
<p><strong>虎牙 · 斗鱼 · 抖音</strong></p>
<p>将国内平台的直播间加入关注与画面墙，解析直播流，在同一套界面里观看。</p>
<p>维护：<a href="https://github.com/Lanpropro">@Lanpropro</a></p>
<p><a href="plugins/domestic_live/README.md">功能与兼容说明</a> · <a href="https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/domestic-live-v1.5">下载插件</a></p>
</td>
<td width="50%" valign="top">
<h3><a href="plugins/global_live/README.md">海外直播平台</a></h3>
<p><img src="docs/assets/platforms/twitch.png" alt="Twitch" width="24" height="24"> &nbsp; <img src="docs/assets/platforms/youtube.png" alt="YouTube" width="24" height="24"></p>
<p><strong>Twitch · YouTube</strong></p>
<p>接入海外平台直播，提供关注、播放、悬停预览与聊天弹幕，支持自动画质。</p>
<p>维护：<a href="https://github.com/Lanpropro">@Lanpropro</a></p>
<p><a href="plugins/global_live/README.md">功能与兼容说明</a> · <a href="https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2">下载插件</a></p>
</td>
</tr>
<tr>
<td width="50%" valign="top">
<h3><a href="plugins/match_sync/README.md">比赛二路同步</a></h3>
<p><img src="docs/assets/platforms/bilibili.png" alt="哔哩哔哩" width="24" height="24"> &nbsp; <img src="docs/assets/platforms/huya.png" alt="虎牙" width="24" height="24"> &nbsp; <img src="docs/assets/platforms/douyu.png" alt="斗鱼" width="24" height="24"> &nbsp; <img src="docs/assets/platforms/douyin.png" alt="抖音" width="24" height="24"> &nbsp; <img src="docs/assets/platforms/twitch.png" alt="Twitch" width="24" height="24"> &nbsp; <img src="docs/assets/platforms/youtube.png" alt="YouTube" width="24" height="24"></p>
<p><strong>一路主画面，多路解说</strong></p>
<p>对齐比赛主画面与二路直播，混合解说声音、合并带来源标记的弹幕。其他平台需先启用对应平台插件。</p>
<p>维护：<a href="https://github.com/Lanpropro">@Lanpropro</a></p>
<p><a href="plugins/match_sync/README.md">功能与兼容说明</a> · <a href="https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.4">下载插件</a></p>
</td>
<td width="50%" valign="top">
<h3><a href="plugins/bili_live/README.md">B站直播互动</a></h3>
<p><img src="docs/assets/platforms/bilibili.png" alt="哔哩哔哩" width="24" height="24"></p>
<p><strong>弹幕 · 表情 · 礼物</strong></p>
<p>使用本体登录，在直播格子里发送文字与表情，浏览礼物目录和背包并参与互动。</p>
<p>作者：<a href="https://github.com/reon-nynes">@reon-nynes</a><br>兼容维护：<a href="https://github.com/Lanpropro">@Lanpropro</a></p>
<p><a href="plugins/bili_live/README.md">功能与兼容说明</a> · <a href="https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/bili-live-v1.0">下载插件</a></p>
</td>
</tr>
</table>

插件依赖配套的 [DD监控室CE](https://github.com/Lanpropro/DD_Monitor_CE/releases/latest) 运行。具体能力、账号要求与最低本体版本，以各插件说明及对应 Release 为准。

## 安装与更新

在软件内打开插件商店，选择需要的插件，按提示安装、启用并重启。

也可以手动导入：

1. 从上方插件下载入口或 [Releases](https://github.com/Lanpropro/DD_Monitor_Plugins/releases) 下载对应的插件 ZIP。
2. 在软件“设置 → 插件”选择“装载插件…”，或将 ZIP 拖入插件页面。
3. 勾选插件，点击“保存并重启”。

更新优先使用软件内的插件更新入口，并按提示重启。各插件独立更新，无需一起替换。

<details>
<summary>手动安装与安装包格式</summary>

ZIP 内只有插件 ID 对应的顶层文件夹。GitHub 自动生成的 **Source code ZIP** 包含整个仓库，不能作为单插件安装包导入。

手动复制时，将插件文件夹放入软件的 `plugins_user/<ID>/`。已存在同 ID 的插件不能重复导入；手动更新前请退出软件，按对应 Release 的步骤操作。

源码运行需要安装本体的 `requirements.txt`；安装版与便携版使用本体随附的运行环境。平台请求使用本体的网络与代理配置。

</details>

## 开发与文档

[开发与贡献](CONTRIBUTING.md) · [待发布更新](docs/changes/README.md) · [仓库目录](docs/repository-layout.md) · [发布记录](docs/releases/README.md) · [展示网站仓库与迁移说明](WEBSITE.md)

插件源码位于 `plugins/<ID>/`，包含清单、实现、资源和各自的 README。网站独立维护，插件安装与更新仍由本仓库提供。

<details>
<summary>查看源码版本与本地打包方式</summary>

以下是**当前分支的源码版本**，不代表最新公开安装包。下载请使用上方入口，公开版本以 Releases 为准。

| 插件 | ID | 源码版本 |
| --- | --- | --- |
| 国内直播平台 | `domestic_live` | 1.4 |
| 海外直播平台 | `global_live` | 1.2 |
| 比赛二路同步 | `match_sync` | 1.4 |
| B站直播互动 | `bili_live` | 1.0 |

先按文件明确暂存需要发布的源码，再执行：

```powershell
python tools/build_plugins.py
python tests/check_packages.py --host ../DD_Monitor_CE
```

输出为 `dist/domestic_live-1.4.zip`、`dist/global_live-1.2.zip`、`dist/match_sync-1.4.zip` 和 `dist/bili_live-1.0.zip`。
构建只读取 Git 跟踪文件，并将许可与来源说明放入每个安装包。

每个插件使用独立的版本标签和 Release，同一个 Release 不混放不同插件的 ZIP。`plugin-catalog.json` 汇总各插件独立 Release 的在线更新信息。

相关专项、宿主集成和验收方式见贡献指南与各插件 README。

</details>

欢迎通过 [Issue](https://github.com/Lanpropro/DD_Monitor_Plugins/issues) 反馈问题，或提交 Pull Request 贡献插件与改进。提交时请注明适用插件、本体版本及复现步骤。

## 来源与许可

保留原项目的 GNU LGPL v2.1 许可，第三方来源与声明见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE.md)。
