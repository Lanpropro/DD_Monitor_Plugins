# 插件发布记录

发布说明集中保存在本目录。2026-11-03 同步当前 GitHub Release 的最新正文，包括维护者在网页上的修改；正文仅统一换行和文件末尾换行，不改写内容。

删除 GitHub Release 不会删除已提交的说明。网页后续修改不会自动回写仓库；删除发布页前，应先再次同步并提交。[来源记录](sources.json)保存各文件的来源、网页更新时间及正文校验值。

## 当前在线发布说明

| 插件 | 仓库说明 | GitHub 来源 |
| --- | --- | --- |
| B站直播互动 | [1.1](RELEASE-bili-live-v1.1.md) | [bili-live-v1.1](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/bili-live-v1.1) |
| 比赛二路同步 | [1.7.0](RELEASE-match-sync-v1.7.0.md) | [match-sync-v1.7.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.7.0) |
| 比赛二路同步 | [1.6.1](RELEASE-match-sync-v1.6.1.md) | [match-sync-v1.6.1](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.6.1) |
| 比赛二路同步 | [1.6](RELEASE-match-sync-v1.6.md) | [match-sync-v1.6](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.6) |
| 国内直播平台 | [2.0](RELEASE-domestic-live-v2.0.md) | [domestic-live-v2.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/domestic-live-v2.0) |
| 海外直播平台 | [1.2](RELEASE-global-live-v1.2.md) | [global-live-v1.2](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2) |

## 替代版本的核验归档

新版公开下载及商店、网站入口验证完成后，先同步旧 Release 最新正文和来源信息、提交归档，再删除旧发布页及附件。保留源码标签和提交；原始 Release JSON、安装包及目录附件另存本地备份，不加入源码仓库。

| 插件 | 历史说明 | 保留源码 | 替代版本 |
| --- | --- | --- | --- |
| 比赛二路同步 | [1.5](RELEASE-match-sync-v1.5.md) | [match-sync-v1.5 标签](https://github.com/Lanpropro/DD_Monitor_Plugins/tree/match-sync-v1.5) | [1.6](RELEASE-match-sync-v1.6.md) |
| 国内直播平台 | [1.5](RELEASE-domestic-live-v1.5.md) | [domestic-live-v1.5 标签](https://github.com/Lanpropro/DD_Monitor_Plugins/tree/domestic-live-v1.5) | [2.0](RELEASE-domestic-live-v2.0.md) |
| B站直播互动 | [1.0](RELEASE-bili-live-v1.0.md) | [bili-live-v1.0 标签](https://github.com/Lanpropro/DD_Monitor_Plugins/tree/bili-live-v1.0) | [1.1](RELEASE-bili-live-v1.1.md) |

二路 1.5、B站 1.0 与国内平台 1.5 于 2026-11-05 在删除前分别核对最新正文及两个附件，正文未改写。作者、原发布者、发布时间、最后更新时间、源码提交和附件大小／SHA256 均保存在 [sources.json](sources.json)。`archived_verified` 表示归档前已核对网页最后编辑版，与下方无法确认最后编辑版的旧备份区分。

## 已删除 Release 的可恢复快照

以下正文从 2026-10-25 保存的 Release JSON 备份恢复。保留备份中的网页更新时间，但无法确认删除前是否还有后续编辑；不是对最后编辑版的保证。

| 原标签 | 仓库说明 | 备份中的更新时间（UTC） |
| --- | --- | --- |
| `v1.1`（国内直播平台历史版） | [原始正文](RELEASE-v1.1.md) | 2026-10-25 04:52:23 |
| `domestic-live-v1.2` | [1.2](RELEASE-domestic-v1.2.md) | 2026-10-25 04:47:28 |

## 仓库保留的历史说明

以下文件保留已有 Git 历史中的正文，目前没有在线 Release 可复核。来源提交见 [sources.json](sources.json)，无法确认是否包含网页删除前的全部修改。

| 插件 | 仓库说明 |
| --- | --- |
| 国内直播平台 | [1.4](RELEASE-domestic-live-v1.4.md)、[1.3](RELEASE-domestic-v1.3.md) |
| 海外直播平台 | [1.1](RELEASE-global-v1.1.md) |
| 比赛二路同步 | [1.4](RELEASE-match-sync-v1.4.md)、[1.3](RELEASE-match-sync-v1.3.md)、[1.1](RELEASE-match-sync-v1.1.md)、[1.5-pre.1](RELEASE-match-sync-v1.5-pre.1.md) |

`v1.0`、`match-sync-v1.2`、`plugins-2026.10.03` 目前只找到标签，未找到可核对的 Release 正文，未编造说明。

同步说明不修改插件源码、版本、标签或已经发布的安装包。每项功能的开发与验收记录继续保存在[插件增量更新](../changes/README.md)。

返回[仓库目录说明](../repository-layout.md)。
