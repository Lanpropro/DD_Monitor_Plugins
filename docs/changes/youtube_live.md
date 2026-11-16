# YouTube直播增量记录

插件ID：`youtube_live`。本插件尚未公开发布，当前为0.1开发候选。

协议实现来源于[组合插件公开基线](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/global-live-v1.2)及后续开发源码；独立包当前没有自己的公开发布基线。

## 待发布

### 1440p60 直播实况核验（2026-11-17，尚未实现）

- 来源：[本体 Issue #235](https://github.com/Lanpropro/DD_Monitor_CE/issues/235)，以及维护者提供的公开直播 `dklfSqHUu4c`。维护者官网统计截图确认2560×1440@60、VP9（itag 308）与AAC（itag 140）；匿名公开页面也返回对应两路自适应格式，当前取得的响应没有HLS/DASH清单。
- 当前缺口：插件只接受H.264 HLS；Streamlink 8.6.1的YouTube直播路径不返回这些自适应格式。只移除编码筛选无法补齐分离音视频、直播分片与请求授权。
- 实际探测：公开页面中的308和140媒体地址直接请求均返回HTTP403。核验固定版本yt-dlp 2026.10.26后，普通提取遇到登录/客户端验证提示，Android VR客户端明确缺少GVS PO Token，Safari/iOS测试未成功取得格式，公开嵌入客户端对这场视频返回不可用（152-18）。网络超时/TLS失败与403并存，尚未确认403的唯一根因；这些结果不证明所有YouTube频道都需要登录。
- 结果：尚无可验收的2K连续播放修复，本轮没有改播放协议、清单版本或代理设置。后续仍需实现并真实验证高画质来源校验、连续分片、VP9视频与AAC音频同步、切档/停止清理；不能以菜单出现1440p或合成测试代替实播通过。
- 状态：原[PR #67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)中保留此待实现边界；本条仅记录调查，不将Issue标为完成或发布新版本。技术依据：[Streamlink YouTube源码](https://github.com/streamlink/streamlink/blob/8.6.1/src/streamlink/plugins/youtube.py)、[yt-dlp PO Token说明](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)。

### 按平台独立打包（2026-11-15，待人工验收）

- 只注册`youtube`平台；按显式清单内置共享代码，运行时不依赖其他插件。原平台取流／账号行为沿用，房间ID、关注与账号存储键不变。
- 首次安装继承声明的组合包设置和启用状态，关闭后重启保持关闭；对应旧组合平台停止重复注册，保留目录与设置用于回滚。
- 独立安装、共存迁移、源码一致性与Qt控件专项通过，配套宿主迁移替身10项通过。参见[PR67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)及[设计与回滚](../development/platform-packages.md)。实际账号和声音听感待人工验收，候选未合并、未发布。

## 2026-11-16：原有平台功能人工确认

- 验收：维护者在插件管理对话确认「原先平台的功能都是正常的」，允许先确认原有平台范围；本条只记录这一实际反馈，不补写未经逐项确认的场景或性能结论。
- 来源：安装的六个独立包共72个运行文件与PR #67原提交0233ff5一致；本平台的安装核对详见管理对话。
- 边界：后续抖音、斗鱼修复继续按具体提交和问题单独核对，不能视为已被本次验收覆盖；CHZZK不在本次通过范围。
- 集成状态：[PR #67](https://github.com/Lanpropro/DD_Monitor_Plugins/pull/67)保持草稿，待新增平台验收及当前develop冲突处理后再完整集成；本条不改变清单版本或发布附件。
