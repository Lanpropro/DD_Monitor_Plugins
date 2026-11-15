# 国内直播平台功能边界

本批从 `plugin.py` 分出完整平台适配、协议、展示与诊断职责。它是结构整理，不改变画质、缓存、超时、重试或发送策略，也不宣称解决实播缓冲。

| 模块 | 职责与状态所有者 |
| --- | --- |
| `plugin.py` | 注册平台与互动控件、恢复映射、宿主事件路由和关闭顺序；保留旧类及协议函数导出 |
| `platform_base.py` | 官方分享链接校验/解析、画质元数据、数字房间及共用取流入口；`_qualities` 仍属于各平台实例 |
| `huya_platform.py` | 虎牙房间、链接与批量状态；构造同一平台的画质、CDN 锁和失败记录 |
| `huya_protocol.py` | WUP/Tars 编码、凭据域限制与响应校验；无界面和后台定时器 |
| `douyu_platform.py` | 斗鱼房间与靓号资料、弹幕接线；构造同一平台的画质和 CDN 状态 |
| `douyin_platform.py` | 抖音房间身份规范化和弹幕接线；构造映射、缓存和广播匹配器，保留旧异常类型导出 |
| `douyu_accounts.py`、`huya_accounts.py`、`douyin_accounts.py` | 各平台专用的账号和关注功能类；包含登录配置、身份确认、关注分页及取消；抖音另含账号引用、主页关联和头像补取 |
| `douyu_playback.py`、`huya_playback.py`、`douyin_playback.py` | 原生画质与取流；斗鱼的请求/实际 CDN 关联与避让，虎牙的 VIP 授权、签名、FLV/HLS 探测与连接交接；只使用原平台状态 |
| `douyin_rooms.py` | 分享/完整页面/公开接口/异步房间查询及验证回退，调用同一实例的账号映射与错误分类 |
| `douyin_room_data.py` | 无状态的分享身份、React Flight/SDK 数据解码和房间资料校验；原静态助手保留为同一函数的兼容绑定 |
| `platform_logo.py` | Logo 设置和关注卡片展示；QObject 父对象仍是宿主窗口，卸载时移除事件过滤器 |
| `douyin_errors.py`、`diagnostic_errors.py` | 查找/验证异常类型及跨平台共用的异常脱敏；避免平台日志反向依赖插件入口 |
| `playback_diagnostics.py` | 协议异常脱敏、mpv 错误分类和播放状态观察；协调器仅引用插件，观察记录保留在插件原字典，不复制状态 |
| `douyu_composer.py`、`interaction_popup.py` | 已有输入控件、礼物/通知界面和交互协调；Qt 父对象、计时器、请求取消规则不变 |
| `douyu_interaction.py`、`huya_interaction.py`、`douyin_interaction.py` | 各平台发送协议、回执及广播复核；本批不修改 |
| `douyin_browser.py`、`douyin_native.js` | 官网原生输入、单次提交及核对窗口；本批不修改 |

实现模块不反向导入 `plugin.py`；旧调用方仍可从入口取得同一个类对象。账号、房间查询和播放功能类组合到同一个平台实例，调用 `self.normalize`、`self.room_info` 等原适配器方法，保留覆写和取消回调。它们没有自己的构造器、线程、定时器或可变状态副本；抖音四份映射／资料字典仍由平台构造并在原主线程流程持久化。输入控件里的礼物/发送详情界面仍需结合控件所有者批次审查，见[六平台职责与验收矩阵](platform-responsibilities.md)。

媒体请求仍在原调用线程创建与关闭：虎牙 `play_url` 的 `finally` 关闭会话，FLV 仅在原交接成功条件下保留响应；斗鱼的失败时间与请求/实际 CDN 映射继续受原实例锁保护。抖音异步查询保留原 await/取消流程。共享诊断异常类型不装载其他平台适配器或注册生命周期。

诊断不创建新的定时器、不调整 mpv 日志级别、不重建播放器；同一 core 复用观察连接，换 core、格子停止、关闭或卸载按原顺序断开。主线程仍负责账号映射持久化，网络线程不接管配置写入。

## 验证

`python tests/check_domestic_modules.py --host <本体目录>` 检查独立导入、旧对象身份、依赖方向、平台实例状态隔离及诊断卸载。可用 `--plugins-dir` 检查隔离安装目录。原平台、关注、分享、凭据、CDN、发送和原生浏览器专项继续执行；夹具的替换点随实现模块迁移，不减少原断言。包检查验证所有新模块随 ZIP 交付。

账号职责补充 `python tests/check_platform_accounts.py --host <本体目录>`：独立导入不装载平台入口、取消后不请求、虎牙身份与关注保留适配器覆写、抖音映射原对象更新及实例隔离。原 `check_douyu_follows`、`check_douyin_accounts`、`check_douyin_lookup` 继续覆盖分页、离线账号和重启。

媒体边界补充 `python tests/check_platform_media.py --host <本体目录>`：取流/查询模块独立导入，不装载入口、输入界面或其他平台实现；抖音旧静态助手与数据解析函数保持同一对象。原画质、授权、CDN、FLV 交接及查询专项只调整实际实现模块的替身位置，保留全部断言。

根 `run.cmd` 验证使用临时插件目录、合成配置和禁止联网的夹具；不能代替真实多路播放。人工在根入口核对斗鱼/抖音/B站的原画质、切换/重连、关注与关闭；真实发送仍由用户手动操作。

## 2026-11-15 同步缓冲的真实证据

本机记录 `work/test-artifacts/playback-capture/20261115-184002-unknown.json`：18:39:02–18:40:02，共61次采样。斗鱼与抖音都出现 `demuxer-cache-duration=0`、`paused-for-cache=true` 和停止推进的音视频时间戳，随后看门狗判错；同期B站61次均为playing，缓存最低约8.874秒。界面采样最大间隔1000ms，不能支持“Python主线程长时间阻塞”的解释。存在解码/输出错误日志，但本轮可见停顿首先有缓存饥饿证据。

这不能继续细分本机网络、CDN、平台流地址或共享带宽，更不能认定拆分文件会改善它。保留既有恢复阈值；需在相同环境做单变量画质/线路对照后再修复。采样不读取账号、不记录媒体完整URL或凭据；诊断文件不随插件发布。

返回[国内插件说明](../../plugins/domestic_live/README.md) · [仓库目录](../repository-layout.md)。

单变量对照：用户只把抖音原画降为超清，反馈“仅抖音改善，斗鱼仍缓冲”。18:48:14–18:49:14第二轮61次采样确认抖音1280×720、同CDN主机FLV、61次playing且缓存暂停0次；斗鱼缓存暂停24次；B站缓存暂停0次。这支持分别排查，不能据此认定抖音故障已根治或斗鱼必然只有CDN问题。

测试随本体既有结构迁移同步维护：输入规范化直接调用 `RoomNavigation`；Logo 检查同时覆盖收集前的旧网格位置与收集后的扩展区；QSS 比对使用 `source_stylesheet` 保留原样式断言；分享链接夹具预置已有头像，避免把独立头像补取加入链接测试的请求预算。基线工作区的原Logo/原始QSS断言失败日志保留本机；没有为测试修改Logo或发送业务。
