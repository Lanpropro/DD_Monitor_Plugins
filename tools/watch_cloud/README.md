# 三人同看查号服务

房主默认在本机运行只监听回环地址的单房间服务器。随包的 cloudflared 2026.11.2（Windows amd64，上游 Cloudflare Apache-2.0）使用临时隧道，仅转发房间协议。插件校验上游发布的 SHA-256，客户端各自拉取直播。关闭房间后撤销登记并关闭隧道；异常中止后，本机房间的登记约三分钟到期。

运营账号部署此 Worker 和 SQLite Durable Objects。每个六位码对应一个对象。本机房间仅登记和查询地址；房主每 45 秒续期。云端房间直接接受成员 WebSocket 并执行指纹匹配。Worker 登录信息仅用于部署；插件只携带公开的 DIRECTORY 地址。生产运营需要可达的 HTTPS 入口。Quick Tunnel 是本机模式的试验传输，重开会分配新地址。

在本目录运行 `wrangler dev --local` 进行本地协议验证，使用 `wrangler deploy` 部署到已授权的运营账号。客户端默认入口在 `plugins/match_sync/watch_room_route.py`。先从目标网络检查 `/health`，再以 `tests/check_watch_hosting.py --directory packaged --mode local` 和 `--mode cloud` 分别复测。前者会真实启动一个测试隧道并使用三个独立进程；两者使用合成指纹，仅作功能回归。

隧道二进制来自 https://github.com/cloudflare/cloudflared/releases/tag/2026.11.2 ，许可证随二进制提供。

## 通过 DNSPod 接入 Pages

`pages/public/_worker.js` 通过同账号的 `ROOM_SERVICE` 绑定，将原请求和 WebSocket 升级响应透传给房间 Worker。仅在 `pages` 目录运行 `wrangler pages deploy --branch main` 部署入口。在 Pages 项目关联专用子域名后，DNSPod 只需新增该主机名的 CNAME，目标以 Pages 项目返回的域名为准。用新域名的 HTTPS 和 WebSocket 实测通过后再更新插件入口。

`node tests/check_watch_pages.mjs` 在仓库根目录检查 HTTP 和 WebSocket 的透传契约。

当前公网入口为 `https://watch.ddmonitor.top`，DNSPod 的 `watch` CNAME 指向 `ddm-watch-gateway-ff03034.pages.dev`。房间查号、隧道健康检查和 WebSocket 遵循系统 HTTP／HTTPS 代理及绕过规则，房主回环连接直连。本机入口首次建立失败时，插件在分配房间码前清理该次隧道并重试一次。
