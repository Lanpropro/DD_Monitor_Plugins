# 网站素材来源

- `logo.svg`：软件仓库 `assets/logo.svg`，保留原标志。
- `screenshots/landscape.webp`：用户于2026-10-21替换的实际软件横屏截图，保留本地原图并转为1920×1064 WebP，裁去顶部16像素原生标题栏。
- `screenshots/danmaku.webp`：软件 `docs/overview5_Landscape_mode.png` 转为1920×1064 WebP，裁去顶部16像素原生标题栏。
- `screenshots/portrait.webp`：软件 `docs/overview2_Portrait_Mode.png` 转为1080×1702 WebP，裁去顶部26像素原生标题栏。
- 软件仓库中的原截图未改动；网站派生图仅裁去原生标题栏，保持内容的像素比例。这些图片来自项目现有文档，用于布局展示，不代表最新版本的每项控件。
- 平台图标复制自软件 `assets/platforms/`，具体出处见 `platforms/README.md`，仅用于识别平台。

软件仓库：https://github.com/Lanpropro/DD_Monitor_CE

## 比赛二路实录预览（2026-10-21）

- `videos/match-sync-recording.mp4`：复用软件仓库宣传片2工程 `videos/dd-monitor-ce-plugins-promo/assets/match-continuous.mp4`，上游为 `Video_reference/二路直播对齐.mp4` 的6–34秒，连续28秒。H.264 / 1920×1080 / 30fps / 无音轨 / faststart；仅压缩，不变速、不拼接、不补造界面。该派生素材已经不含原生标题栏。
- `videos/match-sync-poster.webp`：上述视频首帧。
- 章节：0–6秒对照画面，6–16秒偏移微调，16–23秒同步观看，23–28秒合并弹幕。沿用原工程 BRIEF.md 与 MEDIA.md 的说明：已有录屏展示手动微调，不能宣称为自动对齐成功实录。
- 视频来源在本地复用，默认静音，无新增音乐、外部服务或上传；原宣传片工程及素材未修改。

## 国内与海外插件视频展示（2026-10-21）

- `videos/domestic-live-recording.mp4`：复用宣传片2工程 `assets/domestic-apex-v7.mp4`，源录屏为 `Video_reference/国内apex1+5 重置.mp4` 的8.9–25.3秒。连续16.4秒，1＋5六窗，不替换格子、不变速。沿用该工程已去除原生标题栏的画面。
- `videos/global-live-recording.mp4`：复用宣传片2工程 `assets/global-stitched.mp4`，6.066667秒。九格分别来自 `海外平台九格增补.mp4` 和 `海外平台重录.mp4` 的独立时间段，详见原工程 `cell-selections.json`；这是宣传片剪辑，不代表同一时刻九路同时播放的性能。没有再拼接或改速。
- 两份网页视频均为 H.264 / 1920×1080 / 30fps / 无音轨 / faststart，封面 `*-poster.webp` 为对应视频首帧。
- 保留原始软件内容，仅为网页压缩；原素材、宣传片工程未修改。使用原生视频控件手动播放，离屏、切换插件或进入后台时暂停。
