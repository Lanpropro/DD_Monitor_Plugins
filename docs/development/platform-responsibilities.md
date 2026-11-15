# 六平台职责与验收矩阵

> 类型：设计 ｜ 状态：维护中 ｜ 适用版本：平台独立化开发候选 ｜ 更新：2026-11-15 ｜ 读者：开发者

本矩阵区分职责拆分、独立包交付和真实平台验收。源码职责整理不改变超时、重试、画质、CDN、发送或异步取消策略，也不证明实播性能改善。

| 平台 | 账号与关注 | 房间、协议与媒体 | 状态所有者与后续边界 |
| --- | --- | --- | --- |
| 斗鱼 | `douyu_accounts` 确认身份、头像与分页关注 | `douyu_platform` 房间/靓号；`douyu_playback` 原生画质和 CDN；`douyu_interaction` 发送；`douyu_danmaku` 连接 | 画质、CDN 失败及请求/实际线路仍在平台实例；输入展示由既有控件拥有 |
| 虎牙 | `huya_accounts` 登录 JSONP 校验和关注资料 | `huya_protocol` WUP/Tars；`huya_platform` 房间；`huya_playback` 授权、签名、取流与探测；`huya_interaction` 发送 | CDN 锁/失败记录仍在平台，调用线程创建/关闭会话并维持 FLV 交接；不新增礼物支持 |
| 抖音 | `douyin_accounts` 身份、稳定 UID 引用、关注/主页/头像 | `douyin_platform` 身份接线；`douyin_rooms` 查询；`douyin_room_data` 解码校验；`douyin_playback` 媒体；`douyin_interaction` 与 `douyin_browser` 原生发送 | 映射、资料缓存和广播匹配器只在平台构造；主线程协调保存；查询与媒体不新建状态副本 |
| Twitch | `twitch_accounts` 网页和官方授权身份及关注 | `twitch_platform` 频道校验和公开资料；`public_live` HLS；宿主弹幕协议 | 画质缓存属于平台；账号会话在调用线程使用，OAuth 与加密属于宿主 |
| YouTube | `youtube_accounts` 网页/Google 身份与订阅；`youtube_account_errors` 错误分类 | `youtube_platform` 频道/视频校验和直播资料；`public_live` HLS；宿主弹幕协议 | 画质缓存属于平台；保留网页函数和原入口导出，不复制账号/订阅状态 |
| CHZZK | 当前没有账号关注接口 | `chzzk` 是完整的公开频道适配；`public_live` HLS | 画质缓存属于平台；不为缩短模块将简单频道适配机械切碎 |

国内三个账号功能类与海外两个账号功能类都在原平台实例上执行。它们通过原 `self` 调用规范化、资料查询、校验和保存入口，没有新建状态容器或生命周期；宿主仍访问原平台类型。Qt 父对象、连接断开、输入请求取消及卸载顺序仍属于原控件/插件，不迁入账号模块。

国内界面/协调继续复用一份实现：`interaction_widgets`负责输入与发送详情，`interaction_gifts`负责礼物选择/确认/图片，`interaction_requests`负责可取消请求线程，`interaction_composers`负责每格发送协调，`interaction_controls`负责宿主注册和关闭。完整类体、父对象、250ms 身份定时器及取消/等待顺序不变；旧 `douyu_composer`只兼容导出。共用界面不导入斗鱼协议来初始化虎牙或抖音，协议仍按本平台在使用时接入。虎牙未增加礼物支持。

| 范围 | 离线验证入口 | 人工验收或交付边界 |
| --- | --- | --- |
| 账号模块独立性、取消、覆写和映射 | `check_platform_accounts`、`check_domestic_modules`、`check_global_modules` | 使用替身账号，不登录真实网站 |
| 国内媒体、查询与解析边界 | `check_platform_media`、`check_domestic_modules` | 旧异常导出与静态助手保留；不意外装载另一平台或界面 |
| 输入展示、后台请求与生命周期 | `check_interaction_modules`、`check_douyu_interaction`、`check_douyin_qt_browser` | Qt 父子、信号、草稿、广播确认、图片取消与请求等待继续按原流程；浏览器专项使用允许 WebEngine 子进程的环境 |
| 海外 HTTP/URL 校验与 HLS 边界 | `check_public_http`、`check_global_modules` | 公共 HTTP 工具不依赖宿主/Streamlink/Qt；旧工具函数同对象导出；三个海外包必须携带该依赖 |
| 国内账号、离线关注、分页和映射恢复 | `check_douyu_follows`、`check_douyin_accounts`、`check_douyin_lookup` | 根入口人工核对已有账号、关注与文件夹；不自动读取个人配置 |
| 海外网页/官方账号 | `check_global_web_accounts`、`check_youtube_account_errors` | 官方权限与真实账号由用户操作；不自动发送 |
| 原画质、授权、CDN 和分享链接 | `check_huya_high_resolution`、`check_huya_playback_auth`、`check_douyu_playback_auth`、`check_douyu_recovery`、`check_domestic_share_links`、`check_chzzk` | 真实多路播放、网络/缓存表现另验；斗鱼缓冲尚未根治 |
| 独立包、组合包共存与迁移 | `check_packages`、`check_platform_packages`、独立进程的 `check_platform_install_orders`及宿主迁移专项 | 六包隔离装卸、三种安装顺序、控件/Logo 所有者、逐平台升级/备份回滚、设置与逐平台禁用重启分别验证；真实资料另验 |
| 根开发版集成 | 从根 `run.cmd` 使用临时插件和配置复测 | 不结束用户客户端，不将专项通过写成真实直播/登录/发送验收 |

平台插件的入口只组装和注册；共享源码以打包清单选择进入单平台包，不通过本机安装目录导入，也不要求另一个平台插件存在。账号功能新模块随对应平台包交付；新增依赖必须同步更新拆包清单并验证包内容，不以源码可导入代替包独立性。

保留在原职责中的部分：平台适配器继续持有房间链接/身份与平台实例构造；已有 `platform_base`保留共用房间与画质契约；输入协调器保留其控件的组装和信号接线；YouTube/CHZZK 的短媒体适配与房间协议留在各自完整平台模块中。它们没有独立生命周期或重复状态需要再迁出，不为行数另建框架。真实平台播放、账号/关注完整性和发送仍待人工验收，已知缓冲、缺字与既有宿主通知检查失败未在本次职责整理中修复。

返回[国内模块边界](domestic-modules.md) · [海外模块边界](global-modules.md)。
