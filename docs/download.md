---
title: 下载与兼容性
---

# 下载 DD 监控室 CE

## Windows 便携版

支持 Windows 10 / 11 64 位。下载 EXE 便携包、完整解压后启动，无需另外安装 Python 或 VLC。

<div class="card-actions">
  <a class="action primary" href="https://github.com/Lanpropro/DD_Monitor_CE/releases/download/v0.4/DDMonitorCE-v0.4-exe.zip">下载 v0.4 便携版</a>
  <a class="action secondary" href="https://github.com/Lanpropro/DD_Monitor_CE/releases/tag/v0.4">查看 v0.4 发布说明</a>
</div>

当前版本 **v0.4**，发布于 2026-10-16。上方按钮直接下载官方 `DDMonitorCE-v0.4-exe.zip`。GitHub 自动生成的 Source code 是源码，不是便携程序。

<details><summary>核对安装包 SHA256</summary>
<code class="checksum">453718d02cf46b59ed44ce6ee9f3511e499ec55f46fbc265219bcb6aadb7cd17</code>
</details>

解压后启动 `DD监控室CE.exe`，保留随包目录。[安装与启动帮助](/guide#启动软件)。

## 插件兼容性

建议先将软件升级到最新公开版，再安装插件。当前插件清单要求至少 v0.3；不同插件还依赖配套软件提供的具体功能与运行库。

| 插件 | 下载版本 | 使用要求 |
| --- | --- | --- |
| [国内直播平台](/plugins/domestic_live) | 1.2 | 国内平台取流、画质与弹幕接口，FFmpeg |
| [海外直播平台](/plugins/global_live) | 1.1 | 海外平台取流、自动画质、聊天接口，FFmpeg |
| [比赛二路同步](/plugins/match_sync) | 1.2 | 比赛同步接口、PyAV、NumPy、FFmpeg |

v0.4 在现有插件商店与软件更新功能上，增加深浅色主题、图片／视频背景、可调布局和直播间组合。旧 v0.2 用户首次需手动升级软件，之后可使用配套版本提供的更新功能。新插件依赖的后续改进，以对应插件发布页的要求为准。

::: warning 旧 v0.2 的兼容限制
此前核实的 v0.2 EXE 附件缺少 `ddm.live_danmaku`、`ddm.global_danmaku`、`ddm.auto_quality` 等模块，不能只替换插件 ZIP 来补齐。请先升级软件。
:::

## 插件下载

[浏览全部插件](/plugins/)，选择需要的平台或比赛同步功能。每个插件有独立版本、固定下载链接与 SHA256 校验值，不要求所有插件同时更新。

下载后在「设置 → 插件」导入 ZIP、勾选启用并保存重启。[查看安装与升级帮助](/guide#安装插件)。

## 从源码运行

需要 Python 3.10+、VLC 运行库、FFmpeg，以及当前软件仓库 `requirements.txt` 中的依赖。比赛同步还需要 PyAV 与 NumPy。

[打开软件源码与完整安装说明](https://github.com/Lanpropro/DD_Monitor_CE#快速开始)

GitHub 附件下载速度取决于网络环境。下载异常时打开官方发布页重试，避免使用来源不明的重打包文件。
