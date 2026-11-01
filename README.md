# DD监控室CE · 二路插件（分支修改版）

这是 [DD 监控室 CE](https://github.com/Lanpropro/DD_Monitor_CE) 二路插件 `match_sync` 的**分支修改版**：基于原作者 `1.5` 修改，当前版本 `1.9.2`。

本仓库只包含二路插件本体，不包含其他插件，也没有其他插件的下载入口。

## 适用范围

- 本体：DD 监控室 CE `0.6` 及以上，Windows 10/11
- 运行依赖：本体自带的 libmpv，以及 PyAV、NumPy、FFmpeg
- 用途：选一路比赛主画面，自动对齐多路二路直播，混合解说声音，合并带来源标记的弹幕

## 下载

- 最新安装包：[match-sync-v1.9.2](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v1.9.2)，文件名 `match_sync-1.9.2.zip`
- 逐版本变化：[plugins/match_sync/CHANGELOG.md](plugins/match_sync/CHANGELOG.md)
- 版本与发布规则：[docs/versioning.md](docs/versioning.md)、[docs/releases.md](docs/releases.md)

| 插件 | ID | 版本 |
| --- | --- | --- |
| 比赛二路同步 | `match_sync` | 1.9.2 |

## 修改版做了什么

- **画中画**：把二路解说的框选区域叠在主画面里，可拖动、拖右下角等比缩放，位置随设置保存
- **每路画质**：下方控制台每一路单独换档，不必切主画面，换档只重启该路
- **声道**：点按循环 原始 → 仅左 → 仅右，右键直接选，不依赖弹出菜单
- **分隔条**：弹幕栏宽度跟随光标，双击恢复默认，拖动时不再回弹

## 安装

1. 下载 `match_sync-1.9.2.zip`；GitHub 自动生成的 Source code ZIP 不能当作安装包
2. 先在「设置 → 插件」删除旧的 `match_sync`（本体不支持同 ID 覆盖）
3. 「装载插件…」选择该 ZIP，或把 ZIP 拖入插件页面
4. 勾选插件，点「保存并重启」

手动复制时把 ZIP 里的 `match_sync/` 整个目录放进本体的 `plugins_user/` 也可以。

## 文档

- [插件说明与人工验收](plugins/match_sync/README.md)
- [更新日志](plugins/match_sync/CHANGELOG.md)
- [开发与贡献](CONTRIBUTING.md)
- [待发布更新](docs/changes/README.md)
- [仓库目录](docs/repository-layout.md)
- [发布记录](docs/releases/README.md)

## 来源与许可

派生自 [Lanpropro/DD_Monitor_Plugins](https://github.com/Lanpropro/DD_Monitor_Plugins) 中的 `match_sync`，原作者 [@Lanpropro](https://github.com/Lanpropro)；修改版维护 [@Sarff11](https://github.com/Sarff11)。

保留 GNU LGPL v2.1 许可，第三方来源与声明见 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。
