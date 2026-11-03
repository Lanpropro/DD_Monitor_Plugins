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
