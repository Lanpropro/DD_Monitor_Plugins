# 实时翻译增量记录

插件 ID：`live_translate`。首个公开候选版本为 `0.1` 开发预览。

## 待发布

### 0.1

- 新增单直播间实时翻译：识别宿主 `stream.resolved` 提供的音轨，依次显示原文和译文。
- 支持 ReazonSpeech 日英识别、Qwen3-ASR 目录适配、本地 llama.cpp 翻译、兼容 `/v1/chat/completions` 的 API 翻译和仅原文模式。
- 本地识别在 `DD监控室CE.exe` 的 CPython 3.12 进程内以后台线程运行，不再启动插件管理的 `python.exe worker.py`。
- FFmpeg 与本地 `llama-server.exe` 保持 native 子进程，但使用宿主同名临时启动镜像，任务管理器中归入 `DD监控室CE` 组；停止时清理子进程与启动目录。
- 字幕优先使用宿主 `set_tile_caption()` 覆盖层，旧宿主自动回退独立字幕窗。覆盖层支持独立开关、原文/译文组合、1–4 行、10–64 px 字号、背景透明度、框宽高、逐行贴合文字的黑色背景、全屏显示、PiP 隐藏和播放栏安全区域。
- 活动会话收到新的 `stream.resolved` 时替换 FFmpeg/HLS 输入并接续传译，复用识别器和本地翻译；源代际在识别线程和 GUI 会话层各校验一次，旧源迟到结果不会覆盖新字幕。
- API Key 使用当前 Windows 用户 DPAPI 加密保存为 `api_key_dpapi`，面板重新打开和宿主重启后恢复；清空后删除，配置、日志与导出不写明文。
- 提供源码版 `live_translate-0.1.zip` 和含模型、便携 Python、llama.cpp 的离线完整包 `live_translate-0.1-offline.zip`。离线包因宿主单包 100 MB/256 文件限制，通过包内 `install.ps1` 手动安装。

## 验证

- `tests/check_live_translate.py`：37 项通过，1 项 FFmpeg 条件跳过。
- `tests/check_live_translate_caption.py`：7 项通过。
- `tests/check_contributing.py`：13 项通过。
- `compileall`、`git diff --check` 和文档链接检查通过。
- Qwen3-ASR 官方短音频在宿主进程内识别线程产出非空字幕；FFmpeg → 嵌入识别 → llama.cpp 完整录音回归产出译文，宿主同名子进程和包装目录停止后回收。
- 用户已确认 DD监控室CE 进程分组验收，并确认当前字幕覆盖层、换源续传和 API Key 持久化验收通过。

自动测试和短音频不替代模型精度、2 秒/5 秒端到端延迟和连续 30 分钟资源趋势；这些边界继续在插件 README 中标注。

## 集成与发布状态

- 发布基线：尚无公开发布基线。
- 分支：`function/live-translate`，PR 目标为 `develop`。
- 旧宿主没有字幕覆盖层接口时回退独立字幕窗；内嵌字幕需要配套宿主接口。
- 进程归组由插件自身完成，不要求宿主修改；宿主进程内的识别线程随插件卸载和会话停止回收。
- 源码版和离线完整包发布为同一 GitHub 预发布标签下的两个附件，模型与便携运行环境不进入 Git。
