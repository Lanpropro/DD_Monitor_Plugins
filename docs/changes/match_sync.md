# 比赛二路同步增量记录

插件 ID：`match_sync`。

上次公开发布基线：上游 [match-sync-v1.4](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.4)；本 Fork 基线 [match-sync-v2.0.0](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v2.0.0)。

记录规则与条目模板见[增量记录入口](README.md)。

## 待发布

### 2026-11-02｜修复：2.0r3/r2 每帧抛异常，自动框选与状态行全部停更

- 更新内容：`say(text, hold)` 与 `_status_says(text)` 在 `2.0r3` 被打补丁时误加进了 `CompactStatus` 类，而 `Viewer.render()` 调用的是 `self._status_says(...)`，实机日志每 33 毫秒一条 `AttributeError: 'Viewer' object has no attribute '_status_says'`（`viewer.py:3389`），`render()` 在写状态行处直接抛出，导致状态行、画中画、同步与弹幕刷新全部停更。本次把两个方法移入 `Viewer` 类（`render()` 之前），`CompactStatus` 恢复原样。自动框选的取帧回退、重试、报错提示以及识别参数修正保持不变。版本号 `2.0r5`。
- 验收：2026-11-02，`tests/check_match_sync_pip.py` 54 例通过（新增 `ViewerStatusTests`：直接 import 插件模块断言 `Viewer` 自己定义 `say`/`_status_says`、`CompactStatus` 里不再有 `self._status_says(` 调用、hold 生效期间状态行不被覆盖），`tests/check_match_sync_autocrop.py` 14 例、`tests/check_match_sync_console.py` 3 例、`tests/check_contributing.py` 8 例、`tests/check_packages.py` 与 `compileall` 通过；实机日志 `E:\DDMonitorCE-v0.6-exe\DD监控室CE\logs\ddm-2026-11-02.log` 已确认修复前的异常来源。实机验收待维护者确认。
- 追踪：本次提交。
- 集成状态：尚未合入默认分支。
- 发布注意：`2.0r3`、`2.0r4` 均被本版取代；配置无需迁移。

## 已发布 v2.0r1（2026-11-02）

Release 链接：<https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v2.0r1>。

### 2026-11-02｜修复：手动框选后小窗跳位，移除自动框选

- 更新内容：画中画不再自动识别摄像头区域，「自动框选」按钮、开启画中画后的自动识别与手动框选松手后的四边贴合一起移除，裁剪只由手动框选决定（`viewer.py` 去掉 `autocrop` 全部接线，`autocrop.py` 与 `tests/check_match_sync_autocrop.py` 删除）。手动框选后裁剪比例变化时，小窗改为以中心为锚重算高度，高度放不下时先让出宽度，不再从左上角重新悬挂并被画面边界顶回去（新增 `_keep_pip_centre`）；只有调整后仍越界时才平移。版本号 `2.0r1`。
- 验收：2026-11-02，`tests/check_match_sync_pip.py` 44 例通过（含「手动框选是唯一入口」「换裁剪后小窗中心不动」两条断言），`tests/check_match_sync_console.py` 3 例、`tests/check_contributing.py` 8 例、`tests/check_packages.py`、`compileall` 通过；实机验收待维护者确认。
- 追踪：本次提交。
- 集成状态：尚未合入默认分支。
- 发布注意：相对 `2.0.0` 功能只减不增，配置无需迁移。

## 已发布 v2.0.0（2026-11-01）

Release 链接：<https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v2.0.0>。

### 2026-11-01｜修复：弹幕控制台窄宽度下房间列压住输入框

- 更新内容：`sending.py` 里房间选择列在紧凑布局下的垂直尺寸策略由 `Ignored` 改为 `Fixed`，`body_layout` 显式 `invalidate()/activate()`，并新增 `strip_height()` 让控制台在紧凑布局下按房间列高度加高；常规布局把房间列改回固定 `176 px`（此前只限制最大宽度，列会被输入框挤到 0 宽）。
- 验收：2026-11-01，新增 `tests/check_match_sync_console.py`（3 例：紧凑布局房间列与输入框分处两条带、常规布局房间列 176 px、紧凑布局控制台加高）通过；`check_match_sync_pip.py` 43 例、`check_match_sync_autocrop.py` 17 例、`check_contributing.py` 8 例、`check_packages.py` PASS、`compileall` 通过。
- 追踪：本次提交。
- 集成状态：尚未合入默认分支。
- 发布注意：只改界面布局，无配置迁移；2.0.0 待维护者实测确认后再发布。

## 已发布 v1.11.0（2026-11-01）

Release 链接：<https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v1.11.0>。该功能已在 `2.0r1` 移除。

### 2026-11-01｜新增：画中画自动框选摄像头区域

- 更新内容：画中画设置栏新增「自动框选」。点击（或开启画中画 1.2 秒后、裁剪区域仍是整帧时）在当前来源画面的 640×360 / 480×270 / 320×180 三档缩略图上识别摄像头窗口：优先按四条边配对找独立窗口，没有矩形时用 OpenCV 人脸框兜底（同一张脸需在两档分辨率上被检出）；命中后直接把小窗裁剪区域设为识别结果（再做一次边缘贴合），识别不到保留原状。手动「框选画面」松手后同样会按 4.5% 的搜索范围把四条边贴到最近的画面分界。识别只做一次，不额外拉流、不影响声音与时间对齐；1.10.0 的周期跟踪已移除。
- 验收：2026-11-01，`tests/check_match_sync_autocrop.py` 13 例通过（合成五位置、纯主画面假阳性、人脸兜底、多分辨率择优、界面接线），`check_match_sync_pip.py` 43 例无回归；真实二路直播画面的人工验收待补。
- 追踪：本次提交（`docs/project-documentation` 分支，与文档同一提交）。
- 集成状态：尚未合入默认分支。
- 发布注意：需要宿主自带的 NumPy；无 OpenCV 时人脸兜底自动跳过，边框检测照常工作。自动框选目前只对 CNCS 解说一哥（`6657` 直播间）做过适配测试，其他画面可能识别不出，手动框选与四边贴合不受影响。

2026-10-31 启用增量记录，尚未追加通过单项验收的新条目。此前开发增量未逐项回溯；本段为空不表示发布后没有改动。
