# B站直播互动增量记录

插件 ID：`bili_live`。

上次公开发布基线：[bili-live-v1.1](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/bili-live-v1.1)。

记录规则与条目模板见[增量记录入口](README.md)。

## 待发布

### 2026-11-08｜修复：表情刷新按钮的矩形焦点边框

- 更新内容：表情刷新／重试按钮不再因鼠标点击留下蓝色矩形边框，保留键盘聚焦及空格激活。
- 验收：自动离屏检查表情 22/22、礼物弹窗 22/22、表情排序 17/17 通过；深浅主题鼠标焦点和键盘激活已覆盖。真实播放器弹窗外观待人工验收。
- 追踪：本次修复提交及面向 develop 的 PR。
- 集成状态：`function/emoticon-focus-20261108`，尚未合入 develop。
- 发布注意：无新增宿主接口依赖；开发副本需重新安装候选包后验收，尚未公开发布。

暂无已验收但尚未发布的条目。

## 已发布 v1.1（2026-11-05）

[发布说明](../releases/RELEASE-bili-live-v1.1.md) · [GitHub Release](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/bili-live-v1.1)。源码提交 `5dae9abc1e8991767f0f1c705530c351a7e766fe`；公开安装包 SHA256：`0afa256d970f4ddf03f24de23522555ac5dcd11ddb75e9f1372a56c88db7c992`。

### 2026-11-05｜修复：收到的独立表情支持通过 +1 原样发送

- 更新内容：使用表情完整 ID 发送，保留输入草稿；发送前实时检查当前房间的表情权限，缺失、未解锁或查询失败时提示并停止，不降级为描述文字。
- 验收：此前用户已确认表情 +1 成功；本次用户再次确认本机自带、购买及房间表情测试正常。权限拒绝和上下文取消由离线回归验证，不扩称这些分支已完成人工验收。
- 追踪：[PR22](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/22)，功能提交 `1c8cd68`、`1f28f0d`；宿主 [PR102](https://github.com/Lanpropro/DD_Monitor_CE/pull/102)。
- 集成状态：已通过 PR22 合入 develop，集成提交 `30aebf2`；经 PR45 合入 main，已随 v1.1 公开发布。
- 发布注意：公开 1.0 安装包没有 `repeat_message` 接口，会使宿主禁用所有独立大表情的 +1；本机同版本号的副本已有修正。v1.1 已包含此项，用户需更新插件；不能仅更新宿主或以旧版本号相同认定源码相同。
