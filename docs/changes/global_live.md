# 海外直播平台增量记录

插件 ID：`global_live`。

上次公开发布基线：[global-live-v1.2](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2)。

记录规则与条目模板见[增量记录入口](README.md)。

## 待发布

### 网页登录与关注导入开发候选（2026-11-10，待人工验收）

- 首次登录无需自建OAuth客户端：配套宿主使用本应用独立Chrome／Edge窗口；插件负责Twitch当前账号与关注分页、YouTube当前账号与订阅管理页分页。官网结构变化时明确失败并提供原OAuth备选，不把半份结果或未知页面当作成功。
- 职责：插件入口只声明登录域和委托；官网协议在`plugins/global_live/web_accounts.py`，浏览器、异步生命周期、持久化与界面由宿主配套模块负责。旧宿主保持原OAuth入口，插件版本仍1.2。
- 验证入口：`python tests/check_global_web_accounts.py --host <宿主目录>`；配套宿主`run.cmd --check-browser-accounts`。夹具覆盖身份、分页去重、失败、空列表、取消及会话隔离；实际Google/Twitch登录与订阅完整性待用户验收，不能据此认定网页接口实况通过。
- 参考Fy1Sync固定源码fc875cc3a4761878db273f1cefaf19ed50379d7e的独立浏览器思路，未复制代码；其未提供两平台关注导入。本批不修改播放缓存、媒体重试或代理节点参数，未作性能改善结论，未发布。

2026-10-31 启用增量记录，尚未追加通过单项验收的新条目。此前开发增量未逐项回溯；本段为空不表示发布后没有改动。
