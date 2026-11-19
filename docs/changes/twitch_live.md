# Twitch直播增量记录

插件ID：`twitch_live`。当前公开基线为 [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/twitch-live-v1.0)。

协议实现来源于[组合插件公开基线](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2)及后续开发源码；独立包的发布记录见下文。

## 已发布 v1.0（2026-11-19）

- 与 [本体 0.8 热修复 1](https://github.com/Lanpropro/DD_Monitor_CE/releases/tag/v0.8) 配套发布。 [公开说明](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/twitch-live-v1.0) · [ZIP](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/download/twitch-live-v1.0/twitch_live-1.0.zip)
- 标签源码 a7c9316134a0e85f36bcbcdf2d05d0ced4b48cc0 已进入 `develop` 与 `main`。正式包 22637 字节，SHA-256 `269fcf1ee5b109c417f0b1dbb42b1f7c79c30971f6ded1baf62ac96e0dae5f66`。

## 待发布

### 2026-11-19｜1.0正式发布准备（历史记录）

维护者要求随0.8热修复1发布，六个独立平台插件的版本统一为1.0，稍后再看保持1.0。保留最新develop中的海外播放、账号、音频同步和平台拆分修复；旧组合包版本及附件不覆盖。源码先经PR进入develop/main，再从标签Git对象构建并由受检工作流公开。尚未公开发布，最终包大小和SHA256在发布后核对记录。

### 按平台独立打包（2026-11-15，待人工验收）

- 只注册`twitch`平台；按显式清单内置共享代码，运行时不依赖其他插件。原平台取流／账号行为沿用，房间ID、关注与账号存储键不变。
- 首次安装继承声明的组合包设置和启用状态，关闭后重启保持关闭；对应旧组合平台停止重复注册，保留目录与设置用于回滚。
- 独立安装、共存迁移、源码一致性与Qt控件专项通过，配套宿主迁移替身10项通过。参见[PR67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)及[设计与回滚](../development/platform-packages.md)。实际账号和声音听感待人工验收，候选未合并、未发布。

## 2026-11-16：原有平台功能人工确认

- 验收：维护者在插件管理对话确认「原先平台的功能都是正常的」，允许先确认原有平台范围；本条只记录这一实际反馈，不补写未经逐项确认的场景或性能结论。
- 来源：安装的六个独立包共72个运行文件与PR #67原提交0233ff5一致；本平台的安装核对详见管理对话。
- 边界：后续抖音、斗鱼修复继续按具体提交和问题单独核对，不能视为已被本次验收覆盖；CHZZK不在本次通过范围。
- 集成状态：[PR #67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)保持草稿，待新增平台验收及当前develop冲突处理后再完整集成；本条不改变清单版本或发布附件。
