---
title: 下载与兼容性
---

# 下载 DD 监控室 CE

## Windows 便携版

目前公开发布版本为 **v0.2**，支持 Windows 10 / 11 64 位。下载 ZIP、完整解压后启动程序，不需要另外安装 Python 或 VLC。

<div class="card-actions">
  <a class="action primary" href="https://github.com/Lanpropro/DD_Monitor_CE/releases/download/v0.2/DDMonitorCE-v0.2-exe.zip">下载 v0.2 便携版</a>
  <a class="action secondary" href="https://github.com/Lanpropro/DD_Monitor_CE/releases/tag/v0.2">查看发布说明</a>
</div>

安装包：`DDMonitorCE-v0.2-exe.zip` · 约 316 MiB · 附件更新于 2026-10-01。

发布说明已提示 v0.2 仍有待修复问题。这里提供现有公开包，后续版本与修复进度以发布页为准。[安装与启动帮助](/guide#启动软件)。

<details><summary>核对安装包 SHA256</summary>
<code class="checksum">a3b423be41e50ac66db9c9d898cad56601bcd36edcb2fc01a0740b293e54bc36</code>
</details>

## 插件兼容性

::: warning 两个 1.0 插件需要配套软件
公开 **v0.2 EXE 安装包缺少插件所需模块**，请不要把它当作两个 1.0 插件的已确认配套版本。
:::

国内直播平台与 Twitch / YouTube 插件依赖软件的直播平台扩展、画质、预览与聊天接口，要求源码基线 `4bf84bc` 或之后。

截至 2026-10-03，公开软件 `main` 已核实为 `9f007e8`，包含所需接口。已有源码运行环境的用户，可使用此基线或之后的源码并安装仓库的 `requirements.txt`。便携版用户建议等待经过兼容验证的配套安装包，再安装这两个插件。

| 软件来源 | 插件兼容状态 |
| --- | --- |
| 公开 main 源码（已核实 `9f007e8`） | 包含所需接口，需按源码说明准备运行依赖 |
| 公开 v0.2 EXE 附件（2026-10-01 更新） | 缺少所需模块，不作为 1.0 插件的配套版本 |

本次只读检查了与公开附件 SHA256 一致的安装包：内嵌模块和包内文件缺少 `ddm.live_danmaku`、`ddm.global_danmaku`、`ddm.auto_quality`。这些分别由国内／海外插件的聊天与自动画质功能依赖，因此当前附件不能作为两个插件的完整配套版本。

版本名称不能代替构建验证；v0.2 的源码标签也不代表后来替换的 EXE 附件内容。

## 从源码运行

需要 Python 3.10+、VLC 3.x 运行库及 FFmpeg。软件仓库的安装说明是完整操作入口，插件不需要你重新开发或打包。

[打开软件源码与安装说明](https://github.com/Lanpropro/DD_Monitor_CE#快速开始)

## 插件下载

[浏览插件列表](/plugins/)并按平台选择。各插件 ZIP 独立发布，主下载固定为 1.0；不要使用整个仓库的 Source code ZIP 代替插件包。

GitHub 附件的下载速度取决于网络环境。下载异常时先打开发布页重试，勿使用来源不明的重打包文件。
