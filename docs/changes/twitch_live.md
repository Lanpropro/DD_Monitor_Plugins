# Twitch直播增量记录

插件ID：`twitch_live`。本插件尚未公开发布，当前为0.1开发候选。

协议实现来源于[组合插件公开基线](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2)及后续开发源码；独立包当前没有自己的公开发布基线。

## 待发布

### 2026-11-15｜维护：完整功能职责与独立包依赖

- 更新内容：网页/官方授权账号与关注、频道资料、公开HTTP/URL校验和HLS媒体各由对应模块维护；实例画质缓存与原会话时序不变。包内自足，旧入口保留兼容导出；源码职责见[六平台矩阵](../development/platform-responsibilities.md)。
- 验收：本平台隔离装卸、单包构建、升级/备份回滚、设置/映射、独立禁用重启及其他包保持不变通过；相关边界/协议专项通过。根 `run.cmd`临时安装六个新包和两个旧组合包，独立所有者、关注/分组/画质及UID保留通过，未读取真实账号或尝试外网。
- 补充验收：三种安装顺序、六个平台分别禁用重启及继承设置保留在独立进程通过；包含控件/Logo 的原六项包回归单独通过。混合启动与GUI触发的宿主主题错误按既有边界保留。
- 追踪：[职责拆分 PR76](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/76)；源码分支 `function/platform-functional-modules`，基于[拆包PR67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)的`0233ff5`。
- 集成状态：尚未合入 develop，未公开发布；自动回归与真实平台验收分开记录。
- 发布注意：真实登录、关注完整性与播放/声音待人工验收，不修改缓存、代理或授权策略。清单保持0.1，测试0.2仅在临时ZIP；支持范围与待验收项按上述边界执行。

### 按平台独立打包（2026-11-15，待人工验收）

- 只注册`twitch`平台；按显式清单内置共享代码，运行时不依赖其他插件。原平台取流／账号行为沿用，房间ID、关注与账号存储键不变。
- 首次安装继承声明的组合包设置和启用状态，关闭后重启保持关闭；对应旧组合平台停止重复注册，保留目录与设置用于回滚。
- 独立安装、共存迁移、源码一致性与Qt控件专项通过，配套宿主迁移替身10项通过。参见[PR67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)及[设计与回滚](../development/platform-packages.md)。实际账号和声音听感待人工验收，候选未合并、未发布。
