# 版本管理规范

本规范只约束本 Fork 独立维护的 `match_sync` 修改版。其他插件跟随上游，各自的版本以对应 `plugin.json` 与上游 Release 为准，不套用本文规则。

## 版本号

采用语义化版本 `MAJOR.MINOR.PATCH`：

| 位 | 递增条件 | 示例 |
| --- | --- | --- |
| PATCH | 只修问题、不改配置与使用方式 | `1.9.2` → `1.9.3` |
| MINOR | 增加向下兼容的功能 | `1.9.2` → `1.10.0` |
| MAJOR | 有不兼容的变化（设置格式、宿主接口要求或使用方式改变） | `1.9.2` → `2.0.0` |

- 预发布可写成 `1.10.0-beta.1`。
- 版本号独立于上游序列：修改版以作者 `1.5` 为基线，`1.5`–`1.9.2` 都是修改版自己的编号，不代表上游存在同号版本。
- 已经发布的版本号不改名、不重发；发现问题时发新的 PATCH 版本，并在更新日志里说明。

## 发布标识

| 项目 | 规则 | 示例 |
| --- | --- | --- |
| Tag | `match-sync-v<版本>` | `match-sync-v1.9.2` |
| Release 标题 | `比赛二路同步 v<版本>` | `比赛二路同步 v1.9.2` |
| 安装包 | `match_sync-<版本>.zip` | `match_sync-1.9.2.zip` |
| 发布说明文件 | `docs/releases/RELEASE-match-sync-v<版本>.md`，首行 `# 比赛二路同步 v<版本>` | `RELEASE-match-sync-v1.9.2.md` |
| 更新日志 | `plugins/match_sync/CHANGELOG.md` 顶部新增一节 | `## 1.9.3` |

`plugin.json` 的 `id`、`name`、`description` 与目录名不随版本变化；`version` 必须与安装包内清单、Release 标题和发布说明一致。

## 同步位置

同一个版本要同时更新以下位置，缺一处校验脚本会报错：

1. `plugins/match_sync/plugin.json` 的 `version`；
2. `plugins/match_sync/README.md` 首行标题与安装包文件名；
3. `plugins/match_sync/CHANGELOG.md`；
4. `docs/releases/RELEASE-match-sync-v<版本>.md`；
5. `docs/releases/README.md` 的索引表；
6. 根 `README.md` 的源码版本表（`| \`match_sync\` | <版本> |`）。

## 兼容性

`min_app_version` 表示宿主的最低版本要求，当前为 `0.6`。提高该值时属于兼容性变化，应升 MAJOR，并在发布说明里单独说明原因与替代方案。
