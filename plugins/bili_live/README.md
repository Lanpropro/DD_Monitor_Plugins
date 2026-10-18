# B站直播互动

需要 `codex/plugin-tile-extensions` 配套软件：运行时检查公开的格子扩展和登录快照接口。
在本体登录 B 站；各格子的输入草稿相互独立，Enter 发送，中文输入法选词回车不会误发。
小表情插入文字，大表情按当前登录权限发送。请求超时不自动重试。
关闭/禁用/移除插件使用本体保存并重启流程。插件不提供播放平台，不保存账号信息。
礼物功能开发记录与验证证据将在本分支后续提交补齐。

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
