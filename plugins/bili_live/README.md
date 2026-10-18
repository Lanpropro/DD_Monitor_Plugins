# B站直播互动

需要 `codex/plugin-tile-extensions` 配套软件：运行时检查公开的格子扩展和登录快照接口。
在本体登录 B 站；各格子的输入草稿相互独立，Enter 发送，中文输入法选词回车不会误发。
小表情插入文字，大表情按当前登录权限发送。请求超时不自动重试。
关闭/禁用/移除插件使用本体保存并重启流程。插件不提供播放平台，不保存账号信息。
礼物按钮在表情和发送之间。面板展示当前房间分类、剩余电池与各批背包；
只有单次数量的普通礼物点击即赠送，其余礼物悬停选择数量，再点击赠送。
100 原始单位显示为 1 电池，未知余额会禁止购买，但可赠送有效背包。
特殊购买流程保留名称和禁用原因；不提供充值、SC、订阅或自动购买。
同一账号同时只提交一次；背包不足不会转为购买，超时结果未确认时先核对余额/背包。
首次成功前移出面板仍可浏览；成功后移出主窗与数量窗关闭，两窗间提供短暂过渡。
赠送中不会因移出关闭，Esc、外部点击、按钮切换及房间/账号变更仍可关闭。
关闭窗口不会再次提交，迟到的响应不会重新打开。

本分支产物为本地 `dist/bili_live-1.0.zip`，尚未发布到网站目录。
安装时与 `codex/plugin-tile-extensions` 配套使用；没有插件时本体仍保留播放和接收弹幕。

## 开发：礼物接口证据

2026-10-18 对照 PyPI 当前 `bilibili-api-python==17.4.2` wheel 的
`bilibili_api/live.py` 与 `data/api/live.json`；公开仓库旧 raw 路径已失效。
源包：[PyPI 17.4.2](https://pypi.org/project/bilibili-api-python/17.4.2/)。
固定普通赠送 `/xlive/revenue/v1/gift/sendGold`，背包 `/xlive/revenue/v1/gift/sendBag`。
共同字段 uid/ruid/biz_id/gift_id/gift_num/platform/send_ruid/storm_beat_id/price/biz_code，
两个 CSRF 字段；gold 另有 coin_type=gold/bag_id=0/rnd/visit_id，bag 使用所选 bag_id。
已知 v2 bag 变体没有在该维护客户端中使用；本实现选择该来源明确的 v1 字段，
绝不在失败后尝试 v2 或转为购买。真实赠送尚未验证，测试仅本地模拟。
公共 roomGiftList（未登录）响应已确认 gift_data/gift_config 的嵌套形状。
