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
## 本 Fork 的发布记录（仅 `match_sync` 修改版）

本仓库是上游的 Fork，只对 `match_sync` 修改版发布 Release；其他插件的下载以上游 Releases 为准。

| 版本 | 本地说明 | GitHub 来源 |
| --- | --- | --- |
| 2.0r5 | [2.0r5](RELEASE-match-sync-v2.0r5.md) | 待维护者实测后发布 |
| 2.0r4 | [2.0r4](RELEASE-match-sync-v2.0r4.md) | 被 `2.0r5` 取代（每帧异常） |
| 2.0r3 | [2.0r3](RELEASE-match-sync-v2.0r3.md) | 被 `2.0r4` 取代 |
| 2.0r2 | [2.0r2](RELEASE-match-sync-v2.0r2.md) | 被 `2.0r3` 取代 |
| 2.0r1 | [2.0r1](RELEASE-match-sync-v2.0r1.md) | [match-sync-v2.0r1（本 Fork）](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v2.0r1) |
| 2.0.0 | [2.0.0](RELEASE-match-sync-v2.0.0.md) | [match-sync-v2.0.0（本 Fork）](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v2.0.0) |
| 1.10.0 | [1.10.0](RELEASE-match-sync-v1.10.0.md) | [match-sync-v1.10.0（本 Fork）](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v1.10.0) |
| 1.11.0 | [1.11.0](RELEASE-match-sync-v1.11.0.md) | [match-sync-v1.11.0（本 Fork）](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v1.11.0) |
| 1.9.2 | [1.9.2](RELEASE-match-sync-v1.9.2.md) | [match-sync-v1.9.2（本 Fork）](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v1.9.2) |
| 1.9.1、1.9、1.8 | [1.9.1](RELEASE-match-sync-v1.9.1.md)、[1.9](RELEASE-match-sync-v1.9.md)、[1.8](RELEASE-match-sync-v1.8.md) | 仅本机构建，未单独发布 |
| 1.7 | [1.7](RELEASE-match-sync-v1.7.md) | [1.0.0-alpha（本 Fork，资产 `match_sync-1.7.zip`）](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/1.0.0-alpha) |
| 1.6、1.5 | [1.6](RELEASE-match-sync-v1.6.md)、[1.5](RELEASE-match-sync-v1.5.md) | 仅本机构建，未单独发布 |

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
