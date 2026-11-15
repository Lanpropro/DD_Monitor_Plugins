# 插件开发指南

> 类型：指南 ｜ 状态：维护中 ｜ 适用版本：全部版本 ｜ 更新：2026-11-16 ｜ 读者：插件作者、维护者

本页把插件从改动到发布的顺序、命令和文档动作放在一起。分支、提交和 PR 规则见
[贡献指南](../../CONTRIBUTING.md)；每个插件的功能与兼容范围见 `plugins/<ID>/README.md`。

## 1. 全流程

| 阶段 | 要做的事 | 文档动作 |
| --- | --- | --- |
| 需求 | 确认目标平台、宿主版本、是否依赖其他插件 | 在 Issue 或 PR 写清范围和不做的事 |
| 设计 | 确认状态归属、线程/进程、失败与恢复 | 复杂关系需要长期说明时，可写 `DESIGN.md` 并从 README 链接 |
| 实现 | 入口只做清单和接线，界面、业务、协议分开 | README 补适用本体或接口要求和限制 |
| 验证 | 离线替身 → 宿主集成 → 原生 → 真实直播 | 把命令、结果和未覆盖写进增量记录 |
| 记录 | 每项改动通过对应验收后追加 | `docs/changes/<插件ID>.md` |
| 打包 | 日常候选从已提交的工作树构建，正式包从已集成的标签生成 | 核对清单、README、版本和包结构 |
| 发布 | 独立标签、草稿 Release 与附件核验，检查通过后公开 | 新增 `docs/releases/RELEASE-<插件ID>-v<版本>.md`，更新索引与哈希 |
| 网站 | 网站仓库同步已发布附件 | 不改写插件说明；下载数据来自 GitHub Release |
| 归档 | 完成的计划移入归档 | `docs/archive/`，保留原文 |

## 2. 环境与宿主

- 需要与本体同一套 Python 开发依赖和 libmpv；宿主源码默认在相邻目录 `../DD_Monitor_CE`。
- 插件依赖宿主公开契约；宿主接口见
  [插件接口](https://github.com/Lanpropro/DD_Monitor_CE/blob/develop/docs/plugins.md)。
- 需要最低本体版本约束时，在 `plugins/<ID>/plugin.json` 设置 `min_app_version`，并同步到插件 README 和发布说明；未设置时在 README 写明已知的宿主接口要求。
- 测试默认使用隔离数据：

```powershell
$env:DDM_NO_SAVE = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
```

真实直播、登录、原生窗口和声音听感需要单独人工验收；不使用真实 Cookie 作为测试数据，不自动发送弹幕。

## 3. 目录与职责

```text
plugins/<ID>/
  plugin.json        清单：ID、版本、平台、能力及可选的最低本体版本
  plugin.py          入口：清单、生命周期、宿主 API 接线
  README.md          功能、兼容、安装与限制
  DESIGN.md          状态归属、线程/进程、失败与恢复（有则写）
  FEATURES.md        功能清单与验收口径（有则写）
  ACCEPTANCE.md      真实直播/录制/账号的人工验收步骤（有则写）
  ...                按职责拆分的界面、业务、协议模块
tests/               离线替身、宿主集成、原生与性能专项
tools/               打包与诊断工具
```

入口不堆界面和协议实现；状态只有一个所有者，不在新旧模块间复制可变状态。复用宿主公开接口，
不导入宿主内部模块或本机安装副本。

## 4. 测试

```powershell
python tests/check_contributing.py
python -m compileall -q plugins tests tools
```

按改动选择专项，例如宿主集成、平台授权、打包结构和真实播放：

```powershell
python tests/check_huya_high_resolution.py --host ../DD_Monitor_CE
python tests/check_packages.py --host ../DD_Monitor_CE
python tests/check_release_upgrade.py --host ../DD_Monitor_CE
```

专项需要宿主、FFmpeg、libmpv 或原生窗口时，先确认条件再运行；结果写明命令、退出码和未覆盖范围。
不能因为仓库检查通过就宣称真实直播已验收。

## 5. 打包与发布

1. 明确暂存并提交要发布的源码，不用 `git add .` 收集本机产物；在候选提交上确认工作树和暂存区无改动；
2. 日常候选运行 `python tools/build_plugins.py`。该工具用 Git 跟踪清单选择路径，ZIP 内容读取当前工作树文件，因此构建前必须完成上一步；
3. 候选包运行 `python tests/check_packages.py --host ../DD_Monitor_CE` 检查包结构与源码一致；
4. 从 `docs/changes/` 汇总本次实际包含且已验收的条目，核对上次公开标签后的差异，参照同一插件上次正式 Release 的正文写本版说明，更新索引和 `sources.json`；
5. 完成源码审查和人工验收，将实际发布源码集成到 `develop`、`main` 后创建独立插件标签；具体要求见[新版本发布门槛](../../CONTRIBUTING.md#新版本发布门槛)；
6. 正式包由 `tools/verify_plugin_release.py` 读取标签 Git 对象生成并核验（使用其 `--build` 选项）；已有包使用同一检查器核验，文件须与标签源码逐字节一致；
7. 创建草稿 Release，上传已核验 ZIP 和说明；手动运行 [Verify and publish plugin draft](../../.github/workflows/publish-plugin.yml) 重新检查实际附件，成功后才公开；失败保持草稿；
8. 发布后核对附件和输出摘要，再同步网站数据。网站只使用已发布附件的版本、地址和 SHA-256，不用本机重新打包的哈希替代公开附件。

同一 Release 不混放不同插件的 ZIP；插件版本独立，不为每次微调递增版本号。确认新 Release 的 ZIP、清单、正文与网站下载指向一致后，归档被替代版本的最终正文和来源信息，再移除该插件的旧 Release；每个插件 ID 的 GitHub Releases 只保留最新正式版，历史标签与仓库文档保留。

## 6. 文档动作

- 长期文档写元信息（类型、状态、适用版本、更新日期、读者），格式见[插件文档规范](../standards/documentation.md)。
- 新增文档放入 `docs/` 对应目录，并在[插件文档中心](../README.md)或子目录索引登记。
- 每项通过对应验收的改动随代码提交更新增量记录；已发布正文的修订按[文档规范](../standards/documentation.md)的授权与留存规则办理。
- 跨仓库引用使用完整 GitHub URL；不写本机绝对路径和真实账号信息。
- 提交前运行 `python tests/check_contributing.py` 和 `git diff --check`。

## 相关文档

- [插件文档中心](../README.md)
- [插件文档规范](../standards/documentation.md)
- [增量更新记录](../changes/README.md)
- [发布记录](../releases/README.md)
- [仓库目录与资料分类](../repository-layout.md)
- [贡献指南](../../CONTRIBUTING.md)
