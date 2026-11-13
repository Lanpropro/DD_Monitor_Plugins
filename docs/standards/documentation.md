# 插件文档规范

> 类型：规范 ｜ 状态：维护中 ｜ 适用版本：全部版本 ｜ 更新：2026-11-14 ｜ 读者：插件作者、维护者

本规范是插件仓库的文档基线，与本体仓库的
[开发文档规范](https://github.com/Lanpropro/DD_Monitor_CE/blob/develop/docs/standards/documentation.md)
共享同一套原则；下面只写插件仓库需要额外遵守的部分。

## 1. 文档分层

| 层 | 位置 | 说明 |
| --- | --- | --- |
| 入口 | 根 `README.md`、`CONTRIBUTING.md`、`docs/README.md` | 用户入口、贡献流程、文档导航 |
| 规范 | `docs/standards/` | 本页 |
| 开发指南 | `docs/development/` | 宿主契约、环境、测试、打包与发布 |
| 插件文档 | `plugins/<ID>/README.md` 等 | 每个插件的功能、设计、验收与限制 |
| 变更 | `docs/changes/` | 上次公开发布后的增量记录 |
| 发布 | `docs/releases/` | 已发布说明与哈希来源，只追加不改写 |
| 归档 | `docs/archive/` | 已完成的计划，不作为当前待办 |

## 2. 元信息

长期维护文档在标题下写：

```markdown
> 类型：指南 ｜ 状态：维护中 ｜ 适用版本：全部版本 ｜ 更新：2026-11-14 ｜ 读者：插件作者
```

- 类型：`规范`、`指南`、`参考`、`记录`、`索引`。
- 状态：`草案`、`维护中`、`稳定`、`冻结`、`历史`。
- 插件文档的“适用版本”写插件版本族（如 `match-sync 1.6.x`）或 `全部版本`；写最低本体版本时使用
  “适用本体”而不是混在“适用版本”里。
- 豁免：`README.md`、`CONTRIBUTING.md`、`NOTICE.md`、`docs/releases/` 下的发布说明、`docs/archive/` 下的历史资料。

## 3. 插件文档集

| 文件 | 何时必须有 | 必须写清 |
| --- | --- | --- |
| `plugins/<ID>/README.md` | 始终 | 功能、最低本体版本、依赖插件、安装与更新、已知限制 |
| `plugins/<ID>/DESIGN.md` | 有独立界面、线程或后台流程 | 状态归属、线程/进程、失败与恢复 |
| `plugins/<ID>/FEATURES.md` | 功能多于一项 | 功能清单与验收口径 |
| `plugins/<ID>/ACCEPTANCE.md` | 涉及真实直播、录制或账号 | 人工步骤、预期结果、未覆盖范围 |
| `plugins/<ID>/PARITY.md` | 存在参考实现或兼容目标 | 对照项、差异与保留原因 |

README 的首段写“这个插件解决什么问题”，不先写实现细节。最低本体版本必须与 `plugin.json` 的
`min_app_version` 一致；依赖其他插件时写清启用顺序。

## 4. 增量记录（docs/changes）

- 每个插件一个文件，文件名等于插件 ID；在 `docs/changes/README.md` 登记。
- 每完成一项改动立即追加，不等发布时回忆；条目包含：改动、验收证据、提交/PR、集成状态、发布注意。
- 未发布的条目放在“待发布”段；发布后移到“已发布”段并写版本与标签，不删除历史。
- 同一功能的连续修复追加在原条目下，不为每次微调新建条目。

## 5. 发布说明（docs/releases）

- `docs/releases/RELEASE-<插件ID>-v<版本>.md` 由 `sources.json` 记录来源与 SHA-256，**发布后不得改写**。
- 标题以 `# ` 开头并以 `<名称> v<版本>` 结尾；正文与 GitHub Release 一致。
- 新增说明时同步更新 `docs/releases/README.md` 索引和 `sources.json`；公开资产变化时更新哈希。
- 发布说明写：适用本体、依赖插件、源码 commit、验证范围、未覆盖范围、升级注意。
- 插件版本以各自 `plugin.json` 为准；文档中的版本必须能指回清单或发布标签。

## 6. 命名、链接与证据

- 文档文件名使用小写英文加连字符；发布说明使用 `RELEASE-<插件ID>-v<版本>.md`。
- 相对链接必须能在克隆后的仓库解析；跨仓库（本体、网站）使用完整 GitHub URL。
- 不写本机路径、真实 Cookie、令牌、账号或录制内容。
- 证据按层写清：离线替身 → 宿主集成 → 原生窗口 → 真实直播 → 用户验收；未执行写“未执行”。
- 禁止把“离线检查通过”写成“真实直播已验收”，禁止把“已合并”写成“已发布”。

## 7. 生命周期与校验

| 状态 | 含义 | 动作 |
| --- | --- | --- |
| 草案 | 方案未定 | 不合入发布说明 |
| 维护中 | 随插件更新 | 改动时同步更新 |
| 稳定 | 接口/流程固定 | 修改需评审 |
| 冻结 | 不再更新但需可查 | 顶部写替代文档 |
| 历史 | 已被取代 | 移入 `docs/archive/` 或保留原路径并标注 |

提交前运行：

```powershell
python tests/check_contributing.py
git diff --check
```

`check_contributing.py` 检查文档入口、索引、链接、增量记录与发布索引；它不判断功能结论是否正确。

## 相关文档

- [插件文档中心](../README.md)
- [插件开发指南](../development/README.md)
- [贡献指南](../../CONTRIBUTING.md)
- [仓库目录与资料分类](../repository-layout.md)
- 本体仓库的[开发文档规范](https://github.com/Lanpropro/DD_Monitor_CE/blob/develop/docs/standards/documentation.md)
