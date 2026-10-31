# 插件增量更新记录

每次插件功能或修复单项验收通过后，立即在对应文件追加记录，随该项改动提交。这里保存相对上次公开发布的开发增量，发布时据此汇总 Release 正文。

## 按插件记录

| 插件 | 增量记录 | 上次公开发布基线 |
| --- | --- | --- |
| 国内直播平台 | [domestic_live](domestic_live.md) | [domestic-live-v1.5](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/domestic-live-v1.5) |
| 海外直播平台 | [global_live](global_live.md) | [global-live-v1.2](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2) |
| 比赛二路同步 | [match_sync](match_sync.md) | [match-sync-v1.4](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.4) |
| B站直播互动 | [bili_live](bili_live.md) | [bili-live-v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/bili-live-v1.0) |

基线于 2026-10-31 按 GitHub Releases 核对，使用公开发布版本，不以开发分支的清单版本替代。该机制从本日起启用；此前的开发增量尚未逐项回溯，文件没有条目不代表发布后没有改动。

## 何时记录

1. 一项改动完成并通过对应验收后，立即追加条目，不等整个插件开发结束。需要人工验收的交互或实播改动，等待人工确认后再记为通过；自动测试通过与人工验收分别写明。
2. 未完成、失败或等待验收的工作留在 Issue、PR 或任务计划中，不写成已验收更新。旧改动补录必须能追溯验收证据，不能仅凭提交标题推定通过。
3. 使用面向用户的描述，说明增加了什么或修复了什么表现。相同功能的后续修正各记一条；发布汇总时再合并同类项。
4. 随该项修改明确暂存、提交记录。正文引用功能提交或 PR；记录本身与功能同一提交时可写本次 PR，避免引用自身尚不存在的提交 SHA。
5. 跨多个插件的改动分别记入受影响插件，并引用同一个 PR。宿主依赖写在对应条目中；仅仓库文档整理不充作插件功能更新。

## 条目模板

在对应插件的「待发布」段按验收日期顺序追加：

```markdown
### YYYY-MM-DD｜新增 / 修复 / 优化：简短标题

- 更新内容：用户可感知的变化，必要时说明修改前后的表现。
- 验收：验收日期、方式、范围和结果；人工确认与自动测试分开写，保留未覆盖范围。
- 追踪：功能提交 SHA 或 PR 链接。
- 集成状态：所在分支；已合入 develop / 尚未合入 develop。
- 发布注意：宿主接口或最低版本要求、兼容限制、升级步骤；无则写「无」。
```

后续撤销或调整已验收内容时，追加更正并注明关联条目，不悄悄删除历史。集成状态在合入后更新；已验收但尚未集成的条目不能自动纳入发布说明。

## 发布时如何收口

1. 按目标插件检查从上次公开标签到本次候选源码的差异，核对「待发布」条目、PR 和提交，补查遗漏与撤销项。
2. 只汇总实际进入发布源码且通过验收的条目，写入 [docs/releases/](../releases/README.md) 对应版本说明；合并重复描述，保留必要的依赖和限制。
3. GitHub Release 正式发布后，才将已包含条目移到本文件新的「已发布 v版本（日期）」段，附 Release 链接；未包含的条目继续留在「待发布」，不整段清空。
4. 更新本索引和插件记录的上次公开发布基线，开始下一轮增量；原始验收记录继续保留。候选构建、提交和推送不视为正式发布。

返回[开发与贡献](../../CONTRIBUTING.md) · [仓库目录](../repository-layout.md)。
