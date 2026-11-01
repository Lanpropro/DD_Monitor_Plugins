# 发布流程

本流程只针对本 Fork 独立维护的 `match_sync` 修改版。其他三个插件由原作者发布，本仓库不重复发布，也不覆盖上游已公开的安装包。

版本号规则与命名见 [`versioning.md`](versioning.md)。

## 一、准备

1. 在 `main` 之外的功能分支完成代码与文档改动，确认工作树里只有本次要发布的文件。
2. 更新 `plugins/match_sync/plugin.json` 的 `version`。
3. 更新 `plugins/match_sync/README.md`：首行标题、安装包文件名、必要的功能说明。
4. 更新 `plugins/match_sync/CHANGELOG.md`：在顶部加本版本小节，按「新增 / 修复 / 优化 / 兼容性」归类，只写可核对的改动。
5. 新增 `docs/releases/RELEASE-match-sync-v<版本>.md`，首行固定为 `# 比赛二路同步 v<版本>`，并把这一行加进 `docs/releases/README.md` 的索引表。
6. 更新根 `README.md` 的源码版本表。

## 二、构建

```powershell
python tools/build_plugins.py
```

- 构建只读取 Git 跟踪文件，新文件必须先 `git add`。
- 产物在 `dist/`，例如 `dist/match_sync-1.9.2.zip`；ZIP 顶层目录为 `match_sync`，包内包含 `LICENSE` 与 `NOTICE.md`。
- 同一版本重复构建不改变版本号，但要以最后一次校验通过的产物为准。

## 三、校验

```powershell
python tests/check_contributing.py
python tests/check_packages.py --host <DD监控室CE 源码目录>
python tests/check_match_sync_pip.py
python -m compileall -q plugins tests tools
```

- `check_packages.py` 会装载宿主插件管理器，验证清单、版本、包内文件、五平台注册与源码一致性；需要宿主依赖与本机可用的 libmpv。
- 校验不通过就不打 Tag：发布说明、清单与安装包必须互相对应。
- 记录产物的 `SHA256` 与字节数，供发布说明与后续核对使用。

## 四、发布

1. 打 Tag：`match-sync-v<版本>`，指向用于构建该产物的提交。
2. 新建 Release：标题 `比赛二路同步 v<版本>`，正文取自 `docs/releases/RELEASE-match-sync-v<版本>.md`；不勾选预发布，除非版本号本身带 `-beta`。
3. 上传资产 `match_sync-<版本>.zip`；同一个 Release 不混放其他插件的安装包。
4. 发布后核对：Release 标题、Tag、资产名、`plugin.json` 版本、`dist/` 产物、`SHA256` 六处一致。

## 五、说明

- 已有安装包不覆盖、不改名。发现问题时发新的 PATCH 版本。
- 手动安装说明统一写「先在设置页删除旧版 `match_sync`，再装载新 ZIP」，宿主不支持同 ID 覆盖安装。
- 本机无法直连 `github.com` 时，可用 GitHub REST API 完成等效发布（创建 blob/tree/commit、更新分支、建 Tag、建 Release、上传资产），流程与校验要求不变。
