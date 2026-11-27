# 比赛二路本机诊断 MCP

在 Windows 本体根目录运行 `run.cmd --diagnose-match-d3d11-quick`，打开比赛二路工作区。开发入口加载试验插件，渲染选择和弹幕开关沿用工作区自己的 Qt 控件。stdio 服务使用独立 Python 环境，依赖见同目录的 `requirements.txt`，PySide6 使用本体相同版本。本机 Codex 工具名为 `match-monitor`。

| 工具 | 参数 | 用途 |
| --- | --- | --- |
| `get_watch_party_state` | `pid` | 返回本机活动同看房间的座位、在线和锁定状态，以及最近的界面提示。 |
| `recheck_watch_party` | `pid` | 调用「立即复核双方对齐」的同一处理函数一次。返回的 requested 表示已排队，最终锁定结果通过状态查询确认。此工具和状态读取在普通开发版加入房间后即可使用，不要求页签可见。 |
| `get_scene` | 可选 `pid` | 读取最近五秒内的实播负载。有控制桥时返回当前后端、弹幕开关、Quick 窗口编号，以及画布和所在屏幕的逻辑像素交集。 |
| `set_presenter` | `pid`、`backend` | 选择 `raster` 或 `d3d11`。核对 `applied` 和 `actual`，处理回放或初始化导致的临时光栅回退。 |
| `set_scrolling` | `pid`、`enabled` | 操作画面弹幕按钮，返回开关实值。 |
| `get_sources`、`set_main_quality`、`set_independent` | `pid`，画质编号或副路序号及开关 | 查看各路可选画质和对齐状态，通过原控件处理逻辑切换主路画质或副路独立播放。 |
| `set_precise_wait` | `pid`、`enabled` | 对当前 D3D11 工作线程切换四毫秒等待方式，比较桌面更新间隔。重建线程时恢复原等待方式。 |
| `list_visible_buttons` | `pid` | 枚举宿主和二路窗口内可见按钮，返回进程内编号、代码角色和启用状态。 |
| `set_row_toggle` | `pid`、`source`、`control_name`、`checked` | 通过正常点击调节指定行的声音、左右声道、弹幕或辅助对齐，返回原状态、点击耗时和新状态。 |
| `set_console_open` | `pid`、`panel`、`opened` | 测量播放控制台、弹幕列表和发送台的收起与恢复按钮。 |
| `set_sidebar_collapsed` | `pid`、`collapsed` | 测量本体侧栏收起和展开。`probe_panel` 的 `host_pip` 动作可单独测量侧栏画中画入口。 |
| `set_raster_paint` | `pid`、`immediate` | 光栅主画面在逐帧同步绘制和默认排队绘制之间切换，两个模式使用相同的重绘区域。 |
| `hold_periodic_search` | `pid`、`seconds` | 限时暂停新的定时对齐搜索，保留自动模式、偏移及混音准入，现有搜索可结束。零秒立即恢复，最长 150 秒后自动恢复。 |
| `probe_panel` | `pid`、`action`、可选 `source`、`profile` | 打开可见按钮，记录窗口显示、首次开始绘制的延时并取消测试面板。涵盖侧栏布局预设与画面墙设置。定位轮可额外采集 Qt 事件种类和函数自耗时。Python 运行时可能同时计入解码线程的函数，常规计时保持关闭。独立进程设置的可见性需由桌面观测。 |
| `confirm_mix` | `pid`、`source` | 对已经匹配、正在等待确认的副路点击原有加入按钮。实际音频输出路数由 `get_scene` 核对。 |
| `get_crop_layout` | `pid` | 返回每块画中画当前的精确裁切、来源和位置，可原样用于恢复。 |
| `restore_crop` | `pid`、`source`、`slot`、`rect`、`placement` | 按房间顺序和裁切槽恢复画中画。矩形使用归一化坐标，放置位置使用桌面逻辑像素 x、y、宽度。 |
| `get_button_timings` | `pid` | 读取最近 16 条二路按钮输入记录，包括进程内按钮编号、代码角色、墙钟时间、到下一次事件循环及到按钮下次开始绘制的耗时。隐藏按钮可能没有后续绘制。 |
| `set_comparison_open` | `pid`、`opened` | 通过可见的对照微调按钮切换到指定状态，记录 Qt 同步点击耗时。相同状态不重复点击。另记录面板显示或隐藏、分割区调整的同步耗时，以及打开后面板首次开始绘制的延时。 |
| `start_sample` | `pid` | 记录起始负载和 Quick 窗口编号。同一 stdio 会话内用相同 PID 结束采样。 |
| `stop_sample` | `pid` | 从轮转日志提取同负载诊断统计，给出实际覆盖时间、有效桶数、等待桶数和超过 50 / 100 ms 的交换间隔数。 |

根开发诊断入口启动前可设置 `DDM_MATCH_MONITOR_RESUME=1`，让试验工作区恢复上次保存的房间和主源并开始播放。

设置 `DDM_MATCH_DESKTOP_CADENCE=1` 可用桌面或窗口探针测量实际文字位移。此时仍记录弹幕密度，交换间隔字段留空。

后台静音验收可在启动前同时设置 `DDM_MATCH_MONITOR_SILENT=1`。二路在打开输出设备前把最终输出音量置零，原混音输入与音频时钟继续工作。

一次对照使用同一主路、画布、音频路数、画中画和显示器。待切换稳定后再开始下一组。Quick 数据按 PID 和窗口编号归属，旧进程仍可读取主画面统计，Quick 统计留空。比较前检查实际覆盖和弹幕密度。每个诊断桶约一秒，逐桶 P95 取中位数。屏幕交集表示几何位置，遮挡和桌面实际更新需用屏幕区域采样另行核对。

控制桥只在试验主工作区可见时接受当前 Windows 用户的命名管道请求。服务仅读取两类数值诊断日志，工具的输入和输出均限定在场景、渲染开关和性能统计。真实账号、媒体地址和弹幕发送均在接口范围之外。

新版 D3D11 工作线程在两次成功写入帧槽间隔超过 50 ms 时记录轮询间隔、取帧耗时、目标时钟年龄、重复或缺失帧次数，以及当前帧的写入耗时。采样结果 `observed_publish_gaps` 按呈现子进程和帧序号去重。诊断窗口保留最近两秒内最多十二个间隔，数量表示实际观测下限。各项最大值可能来自同一轮询，不能相加。旧进程对此字段返回空值。

时钟候选版在长间隔中额外记录 `wait_max_ms`，表示工作线程进入定时等待到返回并获得 Python 执行机会的最长实耗。对比 `poll_interval_max_ms` 可定位两次轮询之间的其他工作。旧版日志仍可读取。

### 验证

在插件工作树运行 `python tests/check_match_monitor_telemetry.py` 验证指标归属。使用 MCP 环境运行 `python tests/check_match_monitor.py --host <本体根目录> --seconds 10` 读取当前实播。离线切换回归使用本体解释器运行 `python tests/check_match_sync_d3d11_trial.py --host <本体根目录> --native-dir <本体根目录>/work/match-d3d11-prototype --quick-overlay --monitor-python <MCP环境的python.exe>`。离线回归覆盖实际 Qt 窗口、原生呈现子进程和手动偏移保持。
