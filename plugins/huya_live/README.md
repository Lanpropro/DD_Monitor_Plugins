# 虎牙直播独立插件（开发候选）

插件ID：`huya_live`，候选版本0.1。只注册`huya`，可以独立启用、更新与回滚。需要包含平台替代迁移接口的0.7.3开发版，未正式发布。

平台协议的唯一维护源码仍在`plugins/domestic_live/`对应职责模块中；入口与清单在本目录。构建工具按`tools/platform_bundles.json`将本平台协议及公共组件内置到ZIP，安装后不需要组合包或其他平台插件。不要直接复制本目录作为安装包。

旧组合包仍保留兼容。新插件首次启动继承旧启用状态和声明的设置，房间ID、账号身份、关注、分组及画质不改名；安装新插件后，对应平台由它接管，禁用后不会由组合包重新启用。

详情与验证见[平台独立包迁移](../../docs/development/platform-packages.md)。构建：`python tools/build_plugins.py`，验证：`python tests/check_platform_packages.py --host <宿主目录>`。候选包：`huya_live-0.1.zip`。
