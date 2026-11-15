# 海外平台模块边界

> 类型：设计 ｜ 状态：维护中 ｜ 适用版本：海外平台开发候选 ｜ 更新：2026-11-15 ｜ 读者：开发者

`plugin.py` 负责组合、注册和卸载，保留旧类／工具函数的兼容导出。`twitch_platform.py`、`youtube_platform.py`、`chzzk.py`分别拥有平台协议；`public_live.py`提供公共HLS画质与只读HTTP工具。`twitch_accounts.py`与`youtube_accounts.py`分别负责网页登录及关注分页，`account_common.py`只提供无状态会话工具，`web_accounts.py`保留兼容导出。

画质缓存由每个平台实例独占；请求会话在原调用线程创建并关闭，没有转移到入口或建立全局可变副本。OAuth与账号加密仍由宿主处理。`platform_logo.py`控制器由插件创建，Qt父对象、事件过滤器及卸载清理保持原流程。

本批保留取流、请求超时、重试和授权时序，不将结构拆分当作性能改善。平台内部账号／关注／取流仍在各自适配模块中；独立安装包及组合包迁移在下一批实现。国内平台复用[已有模块拆分](domestic-modules.md)。

离线验证入口：`python tests/check_global_modules.py --host <宿主目录>`，以及`check_global_web_accounts.py`、`check_chzzk.py`、`check_youtube_account_errors.py`。根宿主`selfcheck_global_live`按实际协议所有者设置替身，避免依赖旧聚合入口的内部导入。
