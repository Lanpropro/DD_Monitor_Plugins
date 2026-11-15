# 插件文档中心

> 类型：索引 ｜ 状态：维护中 ｜ 适用版本：全部版本 ｜ 更新：2026-11-15 ｜ 读者：插件作者、维护者、用户

本仓库的文档入口。插件源码在 `plugins/<ID>/`，每个插件独立版本；网站已迁出到
[DD_Monitor_Website](https://github.com/Lanpropro/DD_Monitor_Website)。

## 快速入口

| 我想… | 先看 |
| --- | --- |
| 第一次参与插件开发 | [贡献指南](../CONTRIBUTING.md) → [插件开发指南](development/README.md) |
| 了解某个插件 | 下方[插件文档](#插件文档) |
| 知道本版改了什么、还没发布什么 | [增量更新记录](changes/README.md) |
| 查已发布的说明 | [发布记录](releases/README.md) |
| 查目录和本机资料边界 | [仓库目录与资料分类](repository-layout.md) |
| 写文档或整理文档 | [插件文档规范](standards/documentation.md) |
| 了解网站如何同步 | [网站迁移与入口](../WEBSITE.md) |

## 文档地图

### 开发流程

| 文档 | 内容 |
| --- | --- |
| [贡献指南](../CONTRIBUTING.md) | 分支、PR、验证、打包与发布规则 |
| [插件开发指南](development/README.md) | 宿主契约、环境、测试、打包、发布与文档清单 |
| [插件文档规范](standards/documentation.md) | 本仓库的文档分层、元信息、记录与校验规则 |
| [真实使用监测记录](development/match-live-evidence.md) | 比赛二路现场数据的保留方式 |

### 变更与发布

| 文档 | 内容 |
| --- | --- |
| [增量更新记录](changes/README.md) | 每个插件上次公开发布后的条目、证据和发布注意 |
| [发布记录](releases/README.md) | 公开版本说明、已删除 Release 的快照与哈希来源 |
| [历史开发资料](archive/README.md) | 已完成的实施计划；不代表当前待办 |

### 插件文档

| 插件 | 源码版本 | 文档 |
| --- | --- | --- |
| 国内直播平台（虎牙 / 斗鱼 / 抖音） | 1.4 | [README](../plugins/domestic_live/README.md) |
| 海外直播平台（Twitch / YouTube） | 1.2 | [README](../plugins/global_live/README.md) |
| 比赛二路同步 | 1.6 | [README](../plugins/match_sync/README.md) |
| B站直播互动 | 1.0 | [README](../plugins/bili_live/README.md) |
| 录制与切片（开发候选） | 0.1.3 | [README](../plugins/DDCut_Replay/README.md)、[设计](../plugins/DDCut_Replay/DESIGN.md)、[功能](../plugins/DDCut_Replay/FEATURES.md)、[验收](../plugins/DDCut_Replay/ACCEPTANCE.md)、[对照](../plugins/DDCut_Replay/PARITY.md) |

源码版本取自各插件 `plugin.json` 的当前分支值，不代表已发布的安装包。2026-11-15 核对的
[比赛二路同步最新正式版为 v1.7.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.7.0)；其他公开版本以
[发布记录](releases/README.md)和 GitHub Releases 为准。

### 资源与边界

| 文档 | 内容 |
| --- | --- |
| [仓库目录与资料分类](repository-layout.md) | 持续维护的文件、本机资料、分支与工作树 |
| [平台图标说明](assets/platforms/README.md) | README 卡片图标的来源与许可 |
| [网站迁移与入口](../WEBSITE.md) | 网站仓库、站点构建与发布入口 |

## 维护约定

- 长期文档在标题下写元信息：类型、状态、适用版本、更新日期、读者；发布说明和历史归档除外。
- 每完成一项插件改动，立即更新 `docs/changes/<插件ID>.md`，随代码同一个 PR 提交；发布时再汇总到
  `docs/releases/`。
- `docs/releases/` 受 `sources.json` 哈希约束，发布后只追加、不改写；新结论写进新的说明或增量记录。
- 新增或移动文档时同步更新本页索引和相对链接；跨仓库引用使用完整 GitHub URL。
- 提交前运行：

```powershell
python tests/check_contributing.py
python -m compileall -q plugins tests tools
git diff --check
```

完整流程见[插件开发指南](development/README.md)，共享的写作原则见本体仓库的
[开发文档规范](https://github.com/Lanpropro/DD_Monitor_CE/blob/develop/docs/standards/documentation.md)。
