# 比赛二路同步增量记录

插件 ID：`match_sync`。

上次公开发布基线：上游 [match-sync-v1.4](https://github.com/Lanpropro/DD_Monitor_Plugins/releases/tag/match-sync-v1.4)；本 Fork 基线 [match-sync-v2.0.0](https://github.com/Sarff11/DD_Monitor_Plugins/releases/tag/match-sync-v2.0.0)。

记录规则与条目模板见[增量记录入口](README.md)。

## 待发布

### 2026-11-02｜新增：让「自动框选」认得出真实摄像头

- 更新内容：`plugins/match_sync/autocrop.py` 重写为按人脸定位的检测器——`detect(frames, faces=True)` 取最近 4 帧灰度图，用 OpenCV 三个 Haar 级联（正面、正面 alt2、侧脸）在 720 宽上放大 1.0/2.0 倍检测，按框重叠 ≥0.30 聚类并要求同一张脸出现在至少两帧；对帧数最多、脸最大的那一簇按 `FACINGS=(3.0, 3.6, 4.2)`（窗口高/脸高）生成 16:9 候选，用窗口四边的亮度跃变（`_border_score`，至少三条边在画面内才有效）打分，在 ≥55% 最高分的候选里取面积最大的一个，再放大 6% 并以 `snap(tolerance=0.02)` 把四边贴到真实分界，最后 `clamp` 进画面。`viewer.py` 恢复「自动框选」按钮（`pip_auto_button`）、`_grab_picture`/`_pip_thumb`/`_pip_frames`/`_auto_pip_crop_for`，结果写入 `row.pip_crop` 后仍走 `_keep_pip_centre` 与 `_sync_pip_geometry`；识别失败只写提示，不改裁剪，也不再有开启画中画后的定时识别。版本号 `2.0r2`。
- 验收：2026-11-02，`tests/check_match_sync_autocrop.py` 14 例通过（平帧无结果、人脸开关、矩形窗口合成用例 IoU ≥0.55、贴边人脸不出框、夹取/最小尺寸、`snap` 贴边与平帧不动、边框分至少三条边、seed 跟随人脸），`tests/check_match_sync_pip.py` 45 例（含「自动框选结果也要走中心锚定」「不再有定时识别」两条断言）、`tests/check_match_sync_console.py` 3 例、`tests/check_contributing.py` 8 例、`tests/check_packages.py`、`compileall` 通过；合成序列标定 8 例中 7 例 IoU 0.65–0.98（均值 0.90），摄像头缩到画面宽度 20% 的极端小窗仍认不出，会提示手动框选。实机验收待维护者确认。
- 追踪：本次提交。
- 集成状态：尚未合入默认分支。
- 发布注意：相对 `2.0r1` 只增加识别入口，配置无需迁移；缺少 OpenCV 的环境仍可安装，点击会提示改用手动框选。

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
