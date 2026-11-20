# IPTV Play v1.0

需要包含频道列表、预设及关注卡片台标接口的 DD 监控室 CE。初版 v0.8 缺少这些接口。插件 ID 为 `m3u_live`，原先导入的频道可继续使用。

1. 在配套宿主中导入并启用 `m3u_live-1.0.zip`，重启客户端。
2. 选择「添加直播间」，粘贴 HTTP(S) 的 M3U／M3U8 链接，或用「选文件」读入本地列表。无扩展名的 HTTP(S) HLS 链接可在地址前加 m3u:。
3. 频道列表可以按名称搜索、按源分组筛选，选中后导入指定关注文件夹。单条 HLS 直接添加。关注卡片可照常拖到画面墙播放。

## 电视预设

在添加面板下方点电视图标打开「频道预设」，选择频道即可在当前所选文件夹生成关注卡片。「选文件」位于展开面板内。从空格打开添加面板时，入口位于「从关注列表选择」旁边。预设只添加到关注，重复点选会提示已存在。

- CCTV-1 综合。[iptv-org 中国频道列表](https://github.com/iptv-org/iptv/blob/master/streams/cn.m3u)
- CCTV-5+（体育赛事）、CCTV-6 电影、CCTV-13 新闻。[CCSH/IPTV 频道列表](https://github.com/CCSH/IPTV/blob/main/live_lite.txt)
- CCTV-1 综合、CCTV-5+ 和 CCTV-6 电影各有一条标为「备用」的预设。点选备用条目会建立独立关注卡片，可手动上墙。[best-fan 央视列表](https://github.com/best-fan/iptv-sources/blob/c5181316e6afe1b456d00b38602af47ca60febda/cn_cctv.m3u8)

已添加的频道卡片可继续使用。其他频道可通过 M3U／HLS 自行导入。

预设链接的可用性随频道来源和网络而变化。节目和画质以实际播出为准。

标题优先使用每条 EXTINF 后的频道名称，允许多个频道共用同一 tvg-name。支持 UTF-8 BOM、相对地址、tvg-logo、group-title 和 EXTGRP。需要 User-Agent 或 Referer 的自有频道可在 EXTINF 与频道地址之间加入 EXTVLCOPT 的 http-user-agent 或 http-referrer。

IPTV 随关注栏一起定时刷新，也可点击「刷新」立即检测。可确认的 HLS 和 HTTP 直播源会显示「直播中」，并按当前关注排序显示。检测尚未确认时显示「待检测」，仍可上墙试播。

开发核验时，用同一导入入口分别试本地文件、远程列表和任一单条公开 HLS。筛选并选择 CCTV-5+ 时，确认不会同时选中同名标签的 CCTV-5。发布前还需用有权访问的赛事源在实际开播时核对跟播和清晰度。 [需求 Issue #241](https://github.com/Lanpropro/DD_Monitor_CE/issues/241)
