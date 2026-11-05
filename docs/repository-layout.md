# 插件仓库目录与资料分类

本仓库维护四个插件，网站在独立仓库维护。整理只调整文档位置和本机历史资料，不变更插件行为、版本和发布状态。

## 持续维护的文件

| 位置 | 职责与处理方式 |
| --- | --- |
| `plugins/bili_live/`、`plugins/domestic_live/`、`plugins/global_live/`、`plugins/match_sync/` | 正式插件源码、资源、清单及各自 README；保留 |
| `tests/` | 离线替身回归、宿主集成、原生界面及性能专项；按测试自身条件执行，不能仅因未被批量调用而判定无用 |
| `tools/build_plugins.py` | 从 Git 跟踪文件生成安装包；保留 |
| `tools/inspect_host_package.py` | 针对旧 `v0.2` PyInstaller 宿主附件的只读诊断工具，硬编码旧 EXE 名；保留供历史包排查，不适用于当前包的通用验收 |
| [README](../README.md)、[贡献指南](../CONTRIBUTING.md)、`LICENSE`、`NOTICE.md` | 使用入口、开发约定及许可来源；保留 |
| [WEBSITE.md](../WEBSITE.md) | 独立网站仓库的迁移与本地入口；保留 |
| [releases/](releases/README.md) | 集中存放 10 份发布说明；4 份公开版本与 GitHub Releases 正文同步，其余保留历史正文和旧文件名 |
| [changes/](changes/README.md) | 按插件保存上次公开发布后的单项验收记录；每次通过后追加，发布时汇总并保留历史 |
| [archive/](archive/README.md) | 已完成的历史实施计划；不作为当前开发任务 |

## 本机资料

以下路径相对插件仓库的日常开发根目录，均不进入 Git 或插件安装包。独立 worktree 有自己的 `dist/`，不能混用不同分支的产物。

| 位置 | 分类与处理方式 |
| --- | --- |
| `dist/<插件ID>-<版本>.zip` | 与根目录当前清单同版本的候选构建产物；版本号相同不证明内容与源码或公开附件相同，交付前仍需对应验证 |
| `work/archive/2026-10-31/packages/` | 旧版本 ZIP 与过期的本机 `plugin-catalog.json` 快照；只作历史参考，不作为当前在线更新目录 |
| `work/archive/2026-10-31/website/` | 网站迁出后遗留的截图、临时 `.mjs` 脚本、旧预览日志、HTML 和网站交接材料；已集中归档 |
| `work/archive/2026-10-31/moves.json` | 本次本机归档的原路径、目标路径、大小和 SHA256，供逐项恢复与校验；不含文件正文 |
| `work/website-migration-20261022/` | 网站迁移 bundle、未提交差异、旧依赖及构建备份；保留原路径，避免破坏迁移记录 |
| `work/repository-organization-20261019/`、`work/release-backups/` | 之前的仓库整理与发布备份；保留原路径 |
| `work/cdp-profile/` | 本机浏览器运行资料，可能被现有进程使用；保留原路径，不上传 |
| `work/match-video-preview/` | 插件预览专项资料；用途尚不能判定已结束，保留 |
| 其他已登记的 `work/` 工作树 | 保留 Git 登记位置、分支及未合入成果；查看方式见下文 |
| `__pycache__/`、`*.pyc` | 可再生成的 Python 缓存，已被忽略；不纳入归档或提交 |
| 本地 `AGENTS.md` | 仅本机开发规则，不提交或上传 |

本次本机归档移动 58 个顶层条目、317 个文件，约 271.2 MiB；移动前后逐文件校验 SHA256，无永久删除。具体文件以本机 `moves.json` 为准；其他电脑不要求存在这些本机路径。

## 分支与工作树

```powershell
git status --short --branch
git worktree list
git log --oneline --left-right HEAD...origin/develop
```

2026-10-31 整理时，根目录 `function/douyin-room-follow-reliability` 与 `origin/develop` 各有独立提交，因此根目录保留原分支，受版本管理的整理从 `origin/develop` 建立 `function/plugin-repository-organization`，通过独立 worktree 提交。此记录是当时的快照，不代表后续分支状态。

不根据分支名、日期或工作树干净就认定工作已经合入；squash 合并也不能只靠祖先关系判断。删除工作树或分支前另行核对 PR、提交差异和保留需求，本次不删除。
