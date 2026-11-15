# CHZZK直播增量记录

插件ID：`chzzk_live`。本插件尚未公开发布，当前为0.1开发候选。

协议实现来源于[组合插件公开基线](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2)及后续开发源码；独立包当前没有自己的公开发布基线。

## 待发布

### 按平台独立打包（2026-11-15，待人工验收）

- 只注册`chzzk`平台；按显式清单内置共享代码，运行时不依赖其他插件。原平台取流／账号行为沿用，房间ID、关注与账号存储键不变。
- 首次安装继承声明的组合包设置和启用状态，关闭后重启保持关闭；对应旧组合平台停止重复注册，保留目录与设置用于回滚。
- 独立安装、共存迁移、源码一致性与Qt控件专项通过，配套宿主迁移替身10项通过。参见[PR67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)及[设计与回滚](../development/platform-packages.md)。实际账号和声音听感待人工验收，候选未合并、未发布。
