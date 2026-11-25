# 比赛二路同步增量记录

插件 ID：`match_sync`。

当前公开预览：[2.0 Pre-release 2](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v2.0.2)。

记录规则与条目模板见[增量记录入口](README.md)。

## 待发布

[PR #65](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/65) 已按维护者要求关闭，未合并；实现分支保留，完整独立框选接线未纳入当前开发基线，也不记为已验收。

## 已发布 2.0 Pre-release 2（2026-11-26）

本次打包当前已联调的 Qt Quick 滚动弹幕、D3D11 原生呈现、画中画和控件响应优化。Windows x64 随包提供原生呈现程序。主画面设置提示 D3D11 可减少播放卡顿，并记忆手动选择。使用旧本体的 Qt Quick 能力时同样保留透明叠加和光栅回退。原生合成与交互回归使用隔离素材，实播性能记录来自前置候选。详见[预览版说明](../releases/RELEASE-match-sync-v2.0.2.md)。

## 已发布 v1.7.0（2026-11-13）

- 在公开 v1.6.1 的基础上发布计时器辅助人工对齐、控件样式调整、主路缓冲安全余量、断流计时器复核、历史取帧延后、全屏控制栏自动隐藏、悬停反馈、插件独立弹幕观看设置及职责拆分。范围以[正式说明](../releases/RELEASE-match-sync-v1.7.0.md)为准。
- 标签内记录了功能专项、打包与隔离安装检查，以及计时器功能的用户验收。真实直播流畅度、低配置播放和 OCR 识别仍需人工复验；发布不表示这些体验问题已经解决。
- v1.6.1 的功能中心入口热修复已先行正式发布；本版从该公开版本继续迭代。

2026-11-16 已将计时器辅助对齐、控制台滚动、独立弹幕设置、对照与全屏调整等通过 [PR #68～#73](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/73) 纳入 `develop`；这次集成未更改 v1.7.0 的正式附件。自动检查和人工验收边界详见[开发与增量历史](../archive/match-sync-development-through-20261116.md)。

发布前的实验、回退、局部人工验收及旧版本状态完整保存在[开发与增量历史](../archive/match-sync-development-through-20261116.md)。
