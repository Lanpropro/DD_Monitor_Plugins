# 海外平台模块边界

> 类型：设计 ｜ 状态：维护中 ｜ 适用版本：海外平台开发候选 ｜ 更新：2026-11-15 ｜ 读者：开发者

`plugin.py` 负责组合、注册和卸载，保留旧类／工具函数的兼容导出。`twitch_platform.py`、`youtube_platform.py`、`chzzk.py`分别拥有房间身份与直播资料协议；`public_live.py`提供公共HLS画质与只读HTTP工具。`twitch_accounts.py`与`youtube_accounts.py`分别负责各平台网页和官方 OAuth 账号身份及关注分页，`account_common.py`只提供无状态会话工具，`web_accounts.py`保留原网页函数兼容导出。

画质缓存由每个平台实例独占；请求会话在原调用线程创建并关闭，没有转移到入口或建立全局可变副本。OAuth与账号加密仍由宿主处理。`platform_logo.py`控制器由插件创建，Qt父对象、事件过滤器及卸载清理保持原流程。

账号功能类组合到同一平台实例，不另建会话或状态；所有账号方法和登录元数据仍可从原平台访问。网页函数兼容入口、官方授权验证、分页/取消及错误分类保留原行为。取流、请求超时、重试和授权时序不变，不将结构拆分当作性能改善。独立安装包及组合包迁移由对应拆包批次验收，完整范围见[六平台职责与验收矩阵](platform-responsibilities.md)。国内平台复用[已有模块拆分](domestic-modules.md)。

独立安装包及组合包迁移已提供开发候选，见[平台独立包迁移](platform-packages.md)。

离线验证入口：`python tests/check_global_modules.py --host <宿主目录>`，以及`check_platform_accounts.py`、`check_global_web_accounts.py`、`check_chzzk.py`、`check_youtube_account_errors.py`。根宿主`selfcheck_global_live`按实际协议所有者设置替身，避免依赖旧聚合入口或固定父类顺序的内部导入。
