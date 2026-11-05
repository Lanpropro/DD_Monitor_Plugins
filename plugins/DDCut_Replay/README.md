# DDCut Replay

专门用于 DD监控室CE 的录制与切片功能插件。当前状态：开发工作区已建立，尚未实现插件入口、页面、媒体任务或安装包，不是可安装版本。

2026-11-04，维护者明确要求在本插件仓库创建名为 `DDCut_Replay` 的插件文件夹，并在新任务中接续设计与实现。

## 目标

提供专用录制管理、录播/回放档案、预览、标记、时间选区和导出界面，与本体画面墙及已有录制能力衔接。需要新的页面与UI设计，先明确用户流程与宿主接口，再实现可运行首版。

首版建议流程：录制或快速保存 → 录播档案 → 独立回看 → 标记/选区 → 后台导出 → 打开成品。多选区、素材库、弹幕热度定位及更深入剪辑可以按需求分阶段加入；完整多轨、AI和真实投稿尚未确定为首版范围。

## 已有研究与接口入口

- [BiliBili ShadowReplay录制与切片设计研究](https://github.com/Lanpropro/DD_Monitor_CE/blob/develop/docs/development/bili-shadowreplay-learning.md)：录播、素材、工程及任务模型；当前源码和旧说明的差异。
- [直播水族馆设计参考](https://github.com/Lanpropro/DD_Monitor_CE/blob/develop/docs/development/liveaquarium-learning.md)：缓存配额、任务调度及回溯交互。
- [本体插件接口](https://github.com/Lanpropro/DD_Monitor_CE/blob/develop/docs/plugins.md)：现有接口和源码/安装副本边界；通用页面和录制服务接口仍需核对，不能假定存在。
- [贡献流程](../../CONTRIBUTING.md)、[插件增量验收记录](https://github.com/Lanpropro/DD_Monitor_Plugins/blob/develop/docs/changes/README.md)。

本体已有 `ddm/recording.py` 和 `ddm/capture_control.py`，负责FFmpeg会话、分段缓存、快速保存、画质及退出收尾。新插件默认兼容并复用已有能力，不直接替换播放后端或移除原录制功能。必要的宿主接口改动应与插件分别提交并记录兼容条件。

## 目录与安装标识

源码文件夹按维护者指定保留为 `plugins/DDCut_Replay/`。现行宿主及本仓库构建器要求安装目录/清单ID匹配小写规则，且构建器默认把源码目录名作为ID。后续需要明确源码目录到运行时ID的映射与构建入口；可考虑 `ddcut_replay`，但这只是候选，尚无清单或最终决定。

不要提前加入不能通过现有安装规范的清单，不把本机安装副本当成唯一源码。本目录属于插件仓库，本体仅增加必要的稳定桥接和页面入口。

## 开发与验收

从 `develop` 准备独立 `function/<名称>` 分支，相关修改通过面向 `develop` 的PR集成。每批补充测试、精确暂存并提交；完成后在本体根目录普通 `run.cmd` 联调，单独打包插件ZIP，不自动发布。

验收覆盖安装/启停、已有录制兼容、边录边回看、关键帧与选区边界、未封口尾段、断流/换房、缓存淘汰与导出并发、取消、窗口关闭后的任务入口、退出收尾、声音/弹幕时间及深浅主题/高DPI。

测试使用隔离配置、专用插件目录和本地媒体；真实直播性能与用户验收另行记录。不读取真实账号、不自动发送弹幕、登录或投稿，不自动重启用户运行中的软件。
