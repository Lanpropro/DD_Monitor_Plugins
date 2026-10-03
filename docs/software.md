---
title: 软件介绍
---

# 一面画面墙，装下喜欢的直播

DD 监控室 CE 是面向 Windows 的多窗口直播观看工具，基于 Python、PySide6 与 VLC。B 站直播由本体提供，其他平台通过独立插件扩展。

## 多画面与布局

- 最多同时管理 16 路直播画面，从关注列表拖入指定格子。
- 横屏提供平分、大带小和弹幕布局；竖屏提供顶部关注栏与纵向画面墙。
- 将窗口拖成另一个方向时，切换到可容纳相近路数的布局。
- 每个格子可以独立暂停、重载、调整音量、静音与画质，也可单格全屏观看。

![软件横屏大带小布局](/screenshots/landscape.webp)

## 关注与开播状态

添加房间号或官方直播间链接，管理关注、置顶与排序。软件定期刷新开播状态，也支持开播提醒和已开播卡片的静音悬停预览。

B 站支持扫码登录及关注导入。插件平台的公开直播通常不需要登录，具体限制见各插件说明。

## 弹幕集中阅读

选择弹幕布局，独立弹幕区跟随主画面。调整字号、屏蔽词与保留条数，在多个直播间之间切换时保留自己的阅读习惯。

![软件横屏弹幕布局](/screenshots/danmaku.webp)

## 竖屏显示器也能用

竖屏时关注列表移到顶部。主画面与小画面纵向排列，不需要把横屏窗口挤在狭长的显示器里。

<img src="/screenshots/portrait.webp" alt="软件竖屏布局与顶部关注栏" width="500" loading="lazy" />

以上为项目现有文档中的真实截图，用于展示布局；不同版本的按钮与细节可能有所调整。

## 录制与即时回放

公开 v0.2 发布说明包含单路录制和即时回放。首次使用前，在「设置 → 录制」选择保存目录；再使用格子的录制按钮。便携包内置 FFmpeg。多路同时录制会增加资源占用，请按机器性能选择。

最新源码还包含后续修复与控制细节，不能仅凭 `v0.2` 版本名判断具体功能。[查看软件下载与版本说明](/download)。

## 独立扩展更多平台

| 插件 | 支持平台 | 主要功能 |
| --- | --- | --- |
| [国内直播平台](/plugins/domestic_live) | 虎牙、斗鱼、抖音 | 格子播放、实际画质、静音预览、聊天弹幕 |
| [Twitch 与 YouTube](/plugins/global_live) | Twitch、YouTube | 格子播放、自动画质、静音预览、聊天弹幕 |

两个插件均为 1.0，可分别启用。需要配套更新的软件，公开 EXE 缺少插件所需模块。[查看插件列表](/plugins/)。

## 项目与来源

本项目是 [DD监控室](https://gitee.com/zhimingshenjun/DD_Monitor_latest) 的二次开发版本，原作者为[执明神君](https://space.bilibili.com/637783)。

- [软件源码与完整说明](https://github.com/Lanpropro/DD_Monitor_CE)
- [软件问题反馈](https://github.com/Lanpropro/DD_Monitor_CE/issues)
- [LGPL-2.1 许可](https://github.com/Lanpropro/DD_Monitor_CE/blob/main/LICENSE)与[第三方声明](https://github.com/Lanpropro/DD_Monitor_CE/blob/main/NOTICE.md)
