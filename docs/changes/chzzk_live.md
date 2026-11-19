# CHZZK直播增量记录

插件ID：`chzzk_live`。当前公开基线为 [v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/chzzk-live-v1.0)。

协议实现来源于[组合插件公开基线](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2)及后续开发源码；独立包的发布记录见下文。

## 已发布 v1.0（2026-11-19）

- 与 [本体 0.8 热修复 1](https://github.com/Lanpropro/DD_Monitor_CE/releases/tag/v0.8) 配套发布。 [公开说明](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/chzzk-live-v1.0) · [ZIP](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/download/chzzk-live-v1.0/chzzk_live-1.0.zip)
- 标签源码 a7c9316134a0e85f36bcbcdf2d05d0ced4b48cc0 已进入 `develop` 与 `main`。正式包 22594 字节，SHA-256 `75fa71ef15613b6ce5e7abb019a38dfacd3a50878521e3be954a1ae3dd4aa943`。

## 待发布

### 2026-11-19｜1.0正式发布准备（历史记录）

维护者要求随0.8热修复1发布，六个独立平台插件的版本统一为1.0，稍后再看保持1.0。保留最新develop中的海外播放、账号、音频同步和平台拆分修复；旧组合包版本及附件不覆盖。源码先经PR进入develop/main，再从标签Git对象构建并由受检工作流公开。尚未公开发布，最终包大小和SHA256在发布后核对记录。

### 2026-11-19｜公开直播人工验收

- 维护者确认 CHZZK 正常播放。此次反馈记录播放通过，未单独报告实际最高解码分辨率、持续时间或各操作结果。
- 验收对应[平台拆分 PR #67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)。0.1 首发候选要求 DD Monitor CE 0.8，兼容本体正式公开后发布。

### 2026-11-16｜冲突整合与人工验收准备

- 同步 develop 的平台列表、文档中心及包检查；共享依赖也进入标签来源的发布校验。CHZZK 运行实现与此前候选及本机已安装的6个运行／清单文件一致，不引入 #76。
- 自动包检查、六包独立安装／迁移5项、CHZZK离线8项、贡献文档14项及发布门槛10项通过；新增[人工验收清单](../../plugins/chzzk_live/ACCEPTANCE.md)，覆盖真实声音、最高画质持续播放、预览隔离、停止重播、本地关注、未开播与禁用重启。
- 追踪：[PR #67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)。本次仅完成冲突整合及自动回归，CHZZK仍未人工验收、未合并、未发布；历史144p静音样本不视为1080p或声音通过。

### 按平台独立打包（2026-11-15，待人工验收）

- 只注册`chzzk`平台；按显式清单内置共享代码，运行时不依赖其他插件。原平台取流／账号行为沿用，房间ID、关注与账号存储键不变。
- 首次安装继承声明的组合包设置和启用状态，关闭后重启保持关闭；对应旧组合平台停止重复注册，保留目录与设置用于回滚。
- 独立安装、共存迁移、源码一致性与Qt控件专项通过，配套宿主迁移替身10项通过。参见[PR67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)及[设计与回滚](../development/platform-packages.md)。实际账号和声音听感待人工验收，候选未合并、未发布。

## 2026-11-16：人工验收边界确认

- 维护者明确说明CHZZK尚未人工验收，保持待验收开发候选。原有平台功能正常的确认不适用于本新增平台，不据此合并整个PR或发布该平台。
- 追踪：[PR #67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)。现有自动回归和历史低画质样本不替代用户的真实播放、声音及稳定性验收。
