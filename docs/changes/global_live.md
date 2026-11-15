# 海外直播平台增量记录

插件 ID：`global_live`。

上次公开发布基线：[global-live-v1.2](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2)。

记录规则与条目模板见[增量记录入口](README.md)。

## 待发布

### CHZZK 公开直播（2026-11-15，待人工验收）

- 支持 CHZZK 频道／直播链接、本地关注卡片、公开直播状态、H.264 画质及预览；平台协议在 `chzzk.py`，入口只负责注册，沿用公共画质处理。本体配套将 CHZZK 加入海外自动画质与 HLS 代理转发。
- 使用实时 HLS，明确避开需要 Streamlink 自定义 reader 签名的时光机路径；不接入账号、关注导入、聊天、年龄验证或回放。
- 实测：公开频道返回 144p／360p／480p／720p60／1080p60；静音原生播放器经现有 HLS 转发连续解码 144p（256×144），停止后转发线程退出。1080p 测试的媒体请求多次 ReadTimeout，尚未通过持续播放验收，不宣称高画质全档可用。
+- 自动验证：CHZZK 8项、网页登录10项、YouTube错误6项、贡献13项及六个平台包结构／源码一致／临时安装检查通过；宿主自动画质（含CHZZK）、海外综合和播放协调专项通过。Logo设置专项在改动前原提交对照中同样失败，本批不修复该既有布局问题；完整宿主冒烟／原生结果另存配套宿主记录。
+- 验证入口 `python tests/check_chzzk.py --host <宿主目录>`，覆盖链接、离线／失败状态、头像封面、实时与回看分离、画质和平台生命周期；自动测试与真实播放结果分别记录在 PR。追加至 [PR67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)，维持 1.2 开发候选，未合入 develop、未发布。


### YouTube官方订阅错误原因（2026-11-10，待真实复测）

- 用户已完成真实Google授权，但读取订阅返回原来的统一未授权提示；原协议将HTTP 401和403合并，无法判断实际原因。本项只增加原因识别，尚不认定订阅读取故障已修复。
- `youtube_account_errors.py`负责Google订阅错误的固定原因代码与操作提示，入口只委托；区分未授予读取权限、API未启用、配额和未关联频道。不显示响应正文、未知原因文本、账号或令牌。
- `check_youtube_account_errors.py`覆盖原因区分、Google RPC详情、非JSON/未知响应不泄露、401提示和后续分页失败不发布半份订阅；真实原因仍需根run.cmd重试采集。追加至[PR67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)，保持1.2开发候选，不发布。

### 网页登录与关注导入开发候选（2026-11-10，待人工验收）

- 首次登录无需自建OAuth客户端：配套宿主使用本应用独立Chrome／Edge窗口；插件负责Twitch当前账号与关注分页、YouTube当前账号与订阅管理页分页。官网结构变化时明确失败并提供原OAuth备选，不把半份结果或未知页面当作成功。
- 职责：插件入口只声明登录域和委托；官网协议在`plugins/global_live/web_accounts.py`，浏览器、异步生命周期、持久化与界面由宿主配套模块负责。旧宿主保持原OAuth入口，插件版本仍1.2。
- 验证入口：`python tests/check_global_web_accounts.py --host <宿主目录>`；配套宿主`run.cmd --check-browser-accounts`。夹具覆盖身份、分页去重、失败、空列表、取消及会话隔离；实际Google/Twitch登录与订阅完整性待用户验收，不能据此认定网页接口实况通过。
- 参考Fy1Sync固定源码fc875cc3a4761878db273f1cefaf19ed50379d7e的独立浏览器思路，未复制代码；其未提供两平台关注导入。本批不修改播放缓存、媒体重试或代理节点参数，未作性能改善结论，未发布。

2026-10-31 启用增量记录，尚未追加通过单项验收的新条目。此前开发增量未逐项回溯；本段为空不表示发布后没有改动。
