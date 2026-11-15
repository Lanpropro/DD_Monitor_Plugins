# 插件仓库 2026-10 整理记录

> 类型：记录 ｜ 状态：历史 ｜ 适用版本：全部版本 ｜ 更新：2026-11-15 ｜ 读者：维护者

以下保留当时的整理结果与分支快照，不用于判断当前分支是否已集成。现行目录规则见[仓库目录](../repository-layout.md)。

本次本机归档移动 58 个顶层条目、317 个文件，约 271.2 MiB；移动前后逐文件校验 SHA256，无永久删除。具体文件以本机 `moves.json` 为准；其他电脑不要求存在这些本机路径。

2026-10-31 整理时，根目录 `function/douyin-room-follow-reliability` 与 `origin/develop` 各有独立提交，因此根目录保留原分支，受版本管理的整理从 `origin/develop` 建立 `function/plugin-repository-organization`，通过独立 worktree 提交。此记录是当时的快照，不代表后续分支状态。

当时已将 `function/domestic-huya-4k` 和 `function/website` 的成果汇总到 `develop`，无需重复合并。三个 `release/` 分支分别对应国内插件 1.2、海外插件 1.1 和二路插件 1.2 的既有标签。
