# 二路插件响应性回归

`check_match_sync_contention.py --host <宿主目录>` 检查音频供给持锁时 GUI
读取时钟不等待，并检查失败搜索的逐路 2/4/8 秒退避、手动重试和重连重置。
宿主需要可用的 libmpv、PySide6、PyAV 和 NumPy。

```powershell
python tests/check_match_sync_contention.py --host ../DD_Monitor_CE
python tests/check_match_sync_responsiveness.py --host <宿主测试工作树> --mpv-dir <libmpv所在目录> --ffmpeg <ffmpeg.exe> --output <结果.json>
```

响应性测试显式导入本仓 `plugins/match_sync`，保留真实 `MainWindow`、设置按钮
信号、设置窗口首帧、视频解码、自动匹配和默认设备的真实音频供给线程。
本地三路 720P60 素材分别使用原图、水平翻转和垂直翻转；音频为静音。
测试禁用宿主无关的账号、头像和状态启动任务，使用临时配置及插件目录，并阻止
Python socket 联网。不会启动或关闭用户正在使用的客户端。

默认先积累 65 秒缓存，再各测 30 秒的匹配／对照四种组合。`--fixtures <目录>`
可复用本地生成素材；`--size 3840x2160` 可另测 UHD，不能将默认结果称作 UHD 验收。

```powershell
python tests/check_match_sync_responsiveness.py --host <宿主测试工作树> --mpv-dir <libmpv所在目录> --ffmpeg <ffmpeg.exe> --soak --seconds 600 --output <长测.json>
```

长测持续开启自动匹配及对照，保持无共同画面；确认副路不会被错误对齐。
每约两秒从独立线程投递真实按钮鼠标事件，间隔交错以免与搜索周期固定错开。
结果分别记录发送线程唤醒延迟、Qt 事件排队、设置首帧和包含发送延迟的首帧时间，
以及事件循环延迟、匹配数据量／候选数／耗时、CPU、音频及历史锁等待、显示帧率。
长测对两种设置首帧统计均要求 p95 < 200ms、最大值 < 500ms，并检查点击样本量。
JSON 保存宿主路径、Qt 版本、插件源码 SHA256、素材规格和测量时长。

这属于真实 Qt 宿主中的合成媒体测量，仍需用户在真实直播、实际分辨率和皮肤下
验收设置打开、拖动及播放连续性。旧 `selfcheck_match_sync_comparison.py` 的
渲染性能结果不能替代本测试：它停用了自动匹配，并替换了音频时钟。

Qt 6.11.2 上 `selfcheck_match_sync_retry_rows.py` 的离屏全屏窗口退出存在
原有 access violation（旧提交也可复现）；该专项应另以
`QT_QPA_PLATFORM=windows` 验证原生窗口路径。其他离屏专项仍正常执行。
