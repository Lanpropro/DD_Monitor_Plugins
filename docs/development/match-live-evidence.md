# 真实使用监测日志保留

外部监测的窗口响应、进程和线程计数应与同一真实客户端的 `match-playback.jsonl` 关联。内部日志会轮转，较长监测需要及时保留日志，不能在结束后只分析尚未覆盖的最后几分钟。

`tools/archive_match_playback.py` 只读取既有内部诊断日志及监测所有权文件，每两秒检查当前文件和两个轮转文件，按 PID、监测起止时间过滤并去重。可用已保存的同会话日志补齐启动前证据；已经被覆盖且没有备份的记录无法恢复。它不采集窗口、不连接直播、不读取账号、不注入客户端、不发送消息，也不终止任何进程。

使用已经运行的监测会话，不要为启动归档重复启动外部监测。所有权文件需包含 `target_pid`、`target_create_time`、`monitor_pid`、`monitor_create_time`、`output`（外部监测会话目录）和 `seconds`；该目录内 `session.json` 的 `started_utc` 决定采集时间范围。

```powershell
python tools/archive_match_playback.py --owner SESSION_OWNER.json --log HOST/logs/match-playback.jsonl --output NEW_ARCHIVE_DIRECTORY --seed SAVED_PLAYBACK.jsonl
```

`--seed` 可省略。输出目录必须尚不存在；启动前核对目标及监测器 PID 与创建时间。监测完成、任一会话进程退出或身份改变、到达既定时限后，归档做最后一次读取并自然退出。监测器若由 Python 虚拟环境包装进程启动，评估其 CPU 成本时应计入实际工作子进程，不能只统计包装进程。

输出为去重的 `playback.jsonl`、启动时的 `owner.json`、采集进度 `live.json` 和正常结束后的 `finished.json`。进度包括扫描次数、最大扫描墙钟耗时、自身累计 CPU 时间和累计运行时间，供检查归档自身成本。不得将归档墙钟耗时视为客户端阻塞，也不能将日志中的 `painted_cadence` 当成物理屏幕帧率。

文件轮转、半行、会话过滤、去重、进程身份和监测结束的功能回归：

```powershell
python tests/check_match_playback_archive.py
python tests/check_contributing.py
python -m py_compile tools/archive_match_playback.py tests/check_match_playback_archive.py
git diff --check
```

这些文件与身份测试不生成媒体，不是性能依据。真实基线和用户实际运行中的卡顿证据单独保存于宿主本地采集目录，不提交账号、日志或本机路径。此工具不修改插件运行逻辑，不需要重新启动客户端或经 `run.cmd` 重新加载插件。

## 点击与音频卡顿的阶段数据

插件重新加载后，`gui_pipeline.stages` 增加 `send_recipient_selection`（主播勾选及发送台更新）与 `chat_action_open`（创建并打开弹幕动作工具条）的调用墙钟耗时。这些字段从处理函数进入时开始计时，不能当作物理鼠标按下到屏幕显示的完整延迟。

`audio_pipeline.stages` 记录 `audio_lock_wait`、`audio_prepare`（PCM读取、声道路由与重采样）、`audio_mix` 和 `audio_device_write`。`audio_gap_ms` 仍表示距上次开始供给的时间；将它与分阶段耗时关联，区分供给调度间隔、锁等待、准备、混音或写入路径。阶段耗时不是CPU占比，读连续性和供给间隔也不能代替实际听感验收。

堆栈预算每60秒恢复120次，保留UI阻塞事件去重、至少1秒的UI采样间隔及每秒一次的播放快照；日志轮转与只读归档继续控制磁盘保留范围。长会话后没有旧 `ui_stall` 事件不能直接判定GUI未阻塞：旧版本的额度是整段会话共用120次。新开始事件的 `stack_budget_window_s=60` 标记已采用续额。
