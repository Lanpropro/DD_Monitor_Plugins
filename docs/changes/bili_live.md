# B站直播互动增量记录

插件 ID：`bili_live`。

上次公开发布基线：[bili-live-v1.0](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/bili-live-v1.0)。

记录规则与条目模板见[增量记录入口](README.md)。

## 待发布

### 2026-11-05｜修复：收到的独立表情支持通过 +1 原样发送

- 更新内容：使用表情完整 ID 发送，保留输入草稿；发送前实时检查当前房间的表情权限，缺失、未解锁或查询失败时提示并停止，不降级为描述文字。
- 验收：此前用户已确认表情 +1 成功；本次用户再次确认本机自带、购买及房间表情测试正常。权限拒绝和上下文取消由离线回归验证，不扩称这些分支已完成人工验收。
- 追踪：[PR22](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/22)，功能提交 `1c8cd68`、`1f28f0d`；宿主 [PR102](https://github.com/Lanpropro/DD_Monitor_CE/pull/102)。
- 集成状态：`function/repeat-emoticon`，等待本次检查后合入 develop。
- 发布注意：公开 1.0 安装包没有 `repeat_message` 接口，会使宿主禁用所有独立大表情的 +1；本机同版本号的副本已有修正。需要在后续插件更新中包含此项，不能仅更新宿主或以版本号相同认定源码相同。
