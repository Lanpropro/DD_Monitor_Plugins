---
title: Twitch 与 YouTube
---

# Twitch 与 YouTube

<PluginInfo id="global_live" />

## 功能

一个插件接入 Twitch 与 YouTube 的公开直播，与国内插件分别启用。支持关注卡片、拖入格子播放、实际尺寸与帧率、自动画质、静音悬停预览和聊天弹幕。

## 添加直播间

| 平台 | 支持的输入格式 |
| --- | --- |
| Twitch | `https://www.twitch.tv/频道名` 或 `twitch:频道名` |
| YouTube 单场直播 | `https://www.youtube.com/watch?v=直播视频ID`、`https://youtu.be/直播视频ID` 或 `/live/直播视频ID` |
| YouTube 频道 | `https://www.youtube.com/@频道/live` 或 `/channel/频道ID/live` |

关注频道后，下次刷新或取流会解析该频道当前直播，适合继续看后续场次。关注视频链接则固定该场直播。已结束的直播可以保留关注，但不会将录像当作正在直播播放。

## 自动画质

画质菜单展示直播实际提供的 H.264 HLS 档位和尺寸 / 帧率。默认「自动」从低档起播，根据分片下载速度、缓冲与带宽余量调整档位；也可以手动选档。

自动模式保存后会保留，按钮显示「自动 · 当前档位」。悬停预览独立选择低档位并保持静音，不修改画面格子的选择。

## 聊天与关注

聊天跟随主画面，支持普通文字、表情文字、屏蔽词、字号与保留数量。不发送聊天，不处理礼物、付费贴纸或聊天回放。YouTube 未开启聊天时会提示，视频仍可播放。

关注卡片包含主播昵称、标题、开播状态、头像与封面，不查询或显示人数。禁用插件后关注与格子转为暂存，重新启用后恢复。

## 安装与网络

[按安装教程导入 ZIP](/guide#安装插件)，确认使用配套更新的软件。源码环境需安装软件固定的 Streamlink 8.6.1 和 FFmpeg。

当前网络必须能访问平台，播放、图片与聊天沿用软件当前网络及代理环境。匿名方式不覆盖登录限制、年龄验证、会员权限或地区授权内容。网页协议变更可能需要更新插件。

## 更新记录

**1.0 · 2026-10-03**：统一海外插件版本为 1.0，沿用已有播放、自动画质、悬停预览与聊天实现。[发布说明](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/v1.0)。

[完整插件 README](https://github.com/Lanpropro/DD_Monitor_Plugins/blob/main/plugins/global_live/README.md) · [反馈问题](https://github.com/Lanpropro/DD_Monitor_Plugins/issues)
