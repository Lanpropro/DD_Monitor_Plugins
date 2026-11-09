# 网络代理 v0.1

开发预览，插件 ID：`proxy_control`。通过插件管理内置代理内核，导入订阅和节点。当前仅 Windows x64 提供内核自动准备；其他平台可自行选择兼容的 Mihomo 程序。

## 使用方式

1. 首次使用点击「准备内核」，或选择已有的 Mihomo 程序。开发机已准备好时可跳过下载。
2. 选择「订阅地址」「本地 Clash／Mihomo 配置」或「粘贴节点链接」，填写相应资料。
3. 点击「启动代理」。节点由官方内核解析，支持 YAML／JSON `proxies`、Base64 订阅和内核支持的节点 URI。
4. 就绪后从「当前节点」中选择节点，并重新播放直播间。
5. 点击「停止代理」结束内核，恢复软件原本使用的系统／环境代理；订阅和节点选择保留。

不是用户的节点或订阅就不能凭空建立可用线路。内置内核只是代理客户端的一部分，不提供代理服务本身。

## 安装与入口

本地候选包：`dist/proxy_control-0.1.zip`；尚无公开下载插件附件、Release 或标签。在宿主设置 → 插件中导入并重启，日常统一从宿主根目录 `run.cmd` 启动。清单最低版本为 0.7.2，实际需具备左侧功能轨接口及现有代理读取链路。

从功能中心固定「网络代理」到关注栏左侧。点击图标打开独立小窗口，再次点击收起；设置不会替换直播墙。收起、Esc 或关闭设置窗口继续保留已应用的代理。点击停止、卸载或退出宿主，会停止插件自有内核并恢复原环境。

启动后新建连接使用内置代理，正在播放的直播间需要重新播放。插件不会自动中断直播或录制。成功启动后重启会恢复内置代理；点击「停止代理」会保存停用状态，下次启动软件时不会自动启动内核。首次安装默认停用。

旧版配置自动迁移：原来的内置订阅、节点与内核路径保留；已移除的手动外部代理地址会清理，旧系统／直连／外部模式迁移为停用内置代理。原有宿主的系统代理能力继续由宿主提供。

首次准备核心使用官方固定版本 `v1.19.33` Windows amd64 compatible 包，按 GitHub 公布的 SHA256 校验。下载和解压异步进行，停止状态不加载内核。也可从插件目录使用：

```powershell
python prepare_core.py --directory C:/DDM-runtime/proxy-control
```

然后选择输出的 `mihomo.exe`。没有在插件安装包中捆绑内核，更新或准备内核不会更改 Windows 系统代理。

## 接入范围与限制

插件设置本软件进程中的标准 HTTP／HTTPS／ALL_PROXY／NO_PROXY 环境变量。宿主的 Requests、urllib、Streamlink、HLS 转发、海外弹幕代理解析、mpv 与新启动的 FFmpeg 均沿用现有代理能力；没有全局替换网络库函数。回环地址始终直连，插件内核的子进程不继承这些代理，避免连接回自己。

它不是 TUN／VPN，不接管操作系统流量，也不能强制改写不读取这些设置、显式禁用代理的第三方插件，或独立浏览器登录页面的网络行为。已建立的连接不会热切换。原有系统代理／环境代理不属于插件管理的进程，不会被插件结束。

导入配置时只使用节点内容，用户配置中的 TUN、LAN 监听、系统 DNS、控制 API 和路由规则不直接套用。插件自行生成回环监听、随机控制口和临时鉴权 Key。默认不启用健康检查轮询、测速面板、GEO 数据库或系统服务。首版由用户手动选节点，不自动挑选最快节点；未知格式会显示加载失败，不发送给在线订阅转换网站。

订阅最多 4 MiB，内核下载最多 48 MiB，网络读取缓冲 64 KiB，下载最多等待 60 秒。内核最多等待 30 秒载入节点。临时会话文件在停止后清理；程序、订阅缓存和个人设置保留在数据目录 `utils/proxy_control/`，不要纳入 Git 或分享。

订阅 URL、节点配置可能含密码或令牌，按用户输入保存在上述本机数据目录，**没有承诺加密存储**。安装包、日志和错误提示不写入这些内容；不要把本机数据目录作为插件包发送给别人。

## 内存与验证

启动后运行一个自有内核，每两秒显示该进程 RSS，停止后释放；这个数字不包含宿主 Qt／网络缓冲增量。尚未证明内置方案比已有客户端更省内存、更快或更稳定。

使用带 PySide6 的宿主解释器：

```powershell
python tests/check_proxy_control.py --host ../DD_Monitor_CE --core C:/DDM-runtime/proxy-control/mihomo.exe
python tests/check_contributing.py
python tests/check_packages.py --host ../DD_Monitor_CE
python -m compileall -q plugins/proxy_control tests/check_proxy_control.py
git diff --check
```

专项测试使用临时目录和本机服务，不读取真实账号、不发送真实弹幕。覆盖内核 HTTP 入口、Requests／urllib／HLS 转发、原生 mpv 与 FFmpeg、旧配置迁移、订阅下载、Base64 文件、真实 Shadowsocks 本机加密节点、节点切换、控制口鉴权、官方内核下载校验与停止回收。没有 `--core` 时实际内核检查会跳过；不能把跳过记为通过。

真实验收请使用用户自己的订阅和节点，从根 `run.cmd` 打开 YouTube／Twitch，核对播放、弹幕、切节点与停止后的表现，再采集 30 分钟稳定阶段内存与延迟。本机协议测试不代替真实线路、各类节点参数或高峰期稳定性验收。

## 模块职责与参考项目

| 模块 | 职责 |
| --- | --- |
| `plugin.py` | 宿主左侧入口及卸载协调 |
| `panel.py` | 独立设置窗口与节点选择；不拥有内核 |
| `controller.py` | 会话状态所有者、应用／恢复设置、准备流程和资源统计 |
| `environment.py` | 进程内代理覆盖及精确恢复，不操作注册表 |
| `configuration.py` | 用户输入、个人配置、隔离核心配置生成 |
| `network.py` | 有界异步下载；取消后忽略迟到回调 |
| `core.py` | 一个 QProcess、临时目录、鉴权 API、节点列表及停止回收 |
| `prepare_core.py` | 固定官方程序来源、摘要校验与异步准备子进程入口 |

优先使用现有实现：[Mihomo](https://github.com/MetaCubeX/mihomo) 提供内核、节点解析与转换；[官方代理集合文档](https://wiki.metacubex.one/en/config/proxy-providers/)、[控制 API](https://wiki.metacubex.one/api/) 和 [端口文档](https://wiki.metacubex.one/en/config/inbound/port/) 提供配置依据。[Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev) 用于参考界面与内核分离的产品方式；没有复制其完整客户端或源码。

Mihomo 为 GPL-3.0，固定版本来源为 [v1.19.33](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.33)；内核独立下载和运行，其源码和许可归上游。原创插件代码沿用仓库 LICENSE。Qt、Requests、mpv、FFmpeg 等沿用宿主依赖和各自许可。
