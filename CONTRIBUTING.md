# 开发与贡献

本仓库维护插件源码；展示网站已独立至 [DD_Monitor_Website](https://github.com/Lanpropro/DD_Monitor_Website)。日常开发以 `develop` 为基线，正式发布由各插件自己的版本标签和 GitHub Releases 标识。

## 分支与目录

| 分支 | 用途 |
| --- | --- |
| `main` | 从 `develop` 接收经过整体验证、准备发布的改动 |
| `develop` | 汇总插件和日常维护，进行整体验证 |
| `function/<名称>` | 从 `develop` 创建的一项功能或修复 |
| `release/<插件及版本>` | 保留已有发布标签对应的源码基线 |
| `archive/<名称>` | 保留旧方案和整理前的提交历史 |

- `plugins/<ID>/`：插件实现、清单、资源与插件 README。
- 网站内容、前端依赖与网站测试已迁出；入口和迁移说明见 [WEBSITE.md](WEBSITE.md)。
- `tools/`、其他 `tests/`：打包工具与专项检查。
- `dist/`、`node_modules/`、`work/`、网站构建输出：本机产物，遵循 Git 忽略规则。

已有 `function/domestic-huya-4k` 和 `function/website` 是整理后的功能快照，已汇总到 `develop`，无需重复合并。三个 `release/` 分支分别对应国内插件 1.2、海外插件 1.1 和二路插件 1.2 的既有标签。插件独立管理版本，不沿用软件本体的 0.4.1 开发标识。

## 日常开发与 PR

开始前确认目录、分支和未提交文件。工作区未归类清楚时，不切换分支或拉取合并。

```powershell
git status --short --branch
git switch develop
git pull --ff-only origin develop
git switch -c function/domestic-quality-fix
```

1. 插件按任务分别开发，补充相关测试；网站改动在独立网站仓库完成。
2. 维护者的小型文档或注释修改可以直接在 `develop` 验证、提交，不强制创建 PR 或 Draft PR。
3. 功能、修复和外部贡献通过 PR 合入 `develop`。未完成时可使用 Draft PR，完成后使用普通 PR。
4. PR 写明目的、修改范围、验证命令与结果、软件接口依赖和已知限制。界面修改提供截图或复现步骤。
5. 准备发布时，整体验证 `develop`，再通过 PR 合入 `main`。已有面向 `main` 的 PR 先核对依赖与差异，再决定是否调整目标。

```powershell
git add -p
# 新文件明确指定路径；不要整体暂存本机配置与产物。
git diff --cached
git diff --cached --check
git commit -m "fix(domestic): preserve selected quality"
```

## 尝试与提交整理

- 开发中可以多次 commit 保存进度。功能完成后通常用 Squash and merge，将最终成果汇总为一个清晰的提交。
- 完全放弃的尝试可关闭 PR、删除对应功能分支；先确认没有待保留的未提交文件或 worktree。
- 未推送的个人功能分支可整理历史；已推送但未合并时，先确认无人基于它开发并保留备份，再使用 `--force-with-lease` 更新。
- 已进入 `main` 或 `develop` 的历史保留；撤销使用 `git revert`，不强推这两个共享分支。
- 历史快照整理前保留原提交引用或 bundle，核对最终代码树一致，并保留贡献者和来源记录。

## 验证

贡献指南与本地链接检查不需要软件宿主或网络：

```powershell
python tests/check_contributing.py
git diff --check
```

网站构建与回归检查在独立网站仓库执行，使用方式见 [WEBSITE.md](WEBSITE.md)。本仓库无需安装 Node.js 依赖。

插件按改动执行相关专项检查。例如虎牙画质与授权的离线替身测试：

```powershell
$env:DDM_NO_SAVE = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
python tests/check_huya_high_resolution.py --host ../DD_Monitor_CE
python tests/check_huya_playback_auth.py --host ../DD_Monitor_CE
python tests/check_platform_logo_settings.py --host ../DD_Monitor_CE
python tests/check_domestic_share_links.py --host ../DD_Monitor_CE
python -m compileall -q plugins tests tools
```

测试使用替身账号和请求数据，不读取真实 Cookie、不自动发送弹幕。真实直播、登录、播放与打包验证单独说明条件和结果；未执行或失败的项目明确记录。

## 版本、打包与网站发布

- 插件版本以各自的 `plugins/<ID>/plugin.json` 为准。发布前核对清单、README、发布说明、标签源码和安装包是否对应。
- 版本标签对应的源码清单必须与公开附件一致；不能只改版本号来认定与发布包一致。
- 先明确暂存发布文件，再运行 `python tools/build_plugins.py`。它只读取 Git 跟踪文件；包结构及源码一致性检查使用 `python tests/check_packages.py --host ../DD_Monitor_CE`。
- 网站下载数据与 SHA256 在独立网站仓库维护，来自已发布附件；本机重新打包的哈希不能替代公开附件哈希。
- 提交源码、推送分支、发布插件 ZIP 与部署网站是不同动作。发布或部署前准备可审阅的产物、版本和验证结果，再按维护者授权执行。
- `dist/` 是构建输出；最终安装包、校验文件与来源记录复制到配套本体仓库的 `results/`，再按授权上传 Release。测试日志和备份仍留在 `work/`。
