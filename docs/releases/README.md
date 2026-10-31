# 插件发布记录

本目录集中保存插件发布说明。公开版本的正文以 GitHub Releases 为准，保留线上措辞，仅统一换行和文件末尾换行；以下公开版本于 2026-10-31 核对同步。线上正文中的旧日期、依赖及描述也按原文保留。

开发期间每次单项验收后的变化记录在[插件增量更新](../changes/README.md)，发布时据此汇总，不等发布当天回溯整个开发过程。

## 当前公开 Releases

| 插件 | 本地说明 | GitHub 来源 |
| --- | --- | --- |
| 国内直播平台 | [1.5](RELEASE-domestic-live-v1.5.md) | [domestic-live-v1.5](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/domestic-live-v1.5) |
| 海外直播平台 | [1.2](RELEASE-global-live-v1.2.md) | [global-live-v1.2](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2) |
| 比赛二路同步 | [1.4](RELEASE-match-sync-v1.4.md) | [match-sync-v1.4](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.4) |
| B站直播互动 | [1.0](RELEASE-bili-live-v1.0.md) | [bili-live-v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/bili-live-v1.0) |

发布说明的同步不改变插件源码或版本。此整理分支的国内插件清单仍为 1.4，不能据新增的 1.5 说明将本分支构建视为公开 1.5 安装包；源码版本以各插件 `plugin.json` 为准。

## 仓库历史记录

以下文件在本次核对时没有对应的公开 Release，保留历史正文与旧文件名，不据此认定线上仍提供这些版本。

| 插件 | 仓库保留的说明 |
| --- | --- |
| 国内直播平台 | [1.4](RELEASE-domestic-live-v1.4.md)、[1.3](RELEASE-domestic-v1.3.md)、[1.2](RELEASE-domestic-v1.2.md) |
| 海外直播平台 | [1.1](RELEASE-global-v1.1.md) |
| 比赛二路同步 | [1.3](RELEASE-match-sync-v1.3.md)、[1.1](RELEASE-match-sync-v1.1.md) |

未列出的历史版本没有单独的说明文件，不补写未经核对的发布记录。

新增说明继续使用 `RELEASE-<插件>-v<版本>.md`，并加入此索引。打包检查从本目录读取对应版本的说明。

返回[仓库目录说明](../repository-layout.md)。
