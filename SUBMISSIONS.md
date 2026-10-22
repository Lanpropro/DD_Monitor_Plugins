# 新插件投稿

## 作者只需要填表

使用[提交新插件表单](https://github.com/Lanpropro/DD_Monitor_Plugins/issues/new?template=plugin-submission.yml)，不需要 Fork 仓库、修改索引或创建 PR。网站只是链接到此表单，没有网站账号或登录；GitHub Issue 本身需要作者登录 GitHub。

1. 准备公开的 GitHub 源码仓库和许可证。
2. 在同一仓库的 GitHub Release 上传单插件 ZIP，再填写表单。
3. 填写清单中的 ID、名称、版本，以及简介、兼容要求、权限与网络访问说明。
4. 自动检查在 Issue 留下评论；后续沟通直接在 GitHub 进行，网站不展示审核状态或拒绝原因。

安装包要求：

- ZIP 不超过 32 MiB，解压后不超过 100 MiB，条目不超过 256 个。
- 唯一顶层目录与插件 ID 相同，必须包含 `plugin.json` 和 `plugin.py`。
- 清单的 `id/name/version` 与表单完全一致，包含非空 `description`。
- ID 使用小写字母开头的小写字母、数字和下划线；版本使用数字和点。
- 首版只支持同一公开源码仓库的 GitHub Release ZIP 附件，不支持网盘、临时附件或 GitHub Source code ZIP。
- 不接受重复 ID。已有插件更新暂时直接联系维护者，源码贡献仍可走原有 PR 流程。

## 维护者操作

1. 查看 Issue、源码、许可证、依赖及权限；完成安全审查和隔离环境下的安装验收。基础检查通过不等于安全认证。
2. 复制最近一次检查评论中的完整 64 位“检查指纹”。
3. 在仓库 Actions → **Plugin submissions** → **Run workflow**，填写投稿 Issue 编号和指纹。
4. 机器人重新下载并检查当前投稿，校验维护者权限及指纹后，创建面向 `develop` 的社区索引 PR。相同审批重复运行返回原 PR；中途失败可重试。
5. 检查 PR 的单文件索引差异后手动合入。不自动合并、不自动发布安装包、不自动部署网站。

若作者修改了表单或替换了 ZIP，旧指纹不能用于生成新 PR，需重新编辑 Issue 触发检查并重新审核。指纹包含投稿资料、作者、Issue 编号和 ZIP 的 SHA256。检查不运行任何插件代码，也不会把仓库 Token 发给下载地址。

`catalog/community.json` 是新增的社区插件索引；官方插件源码与安装包继续沿用现有流程。索引只记录元数据、来源和安装包 SHA256，不复制第三方源码到本仓库。

**本轮网站只接投稿入口，未自动读取此索引生成插件展示卡片。** 审核通过后的网站介绍与详情页仍由维护者在网站仓库更新；合入索引 PR 不会自动部署网页。安装包可能被作者后续替换，使用前仍需对照索引 SHA256；索引不是签名或插件运行沙箱。

## 启用前置条件

- 先通过正常 PR 流程把表单、脚本和工作流集成到 `develop`，再按维护者安排同步到 GitHub 默认分支 `main`。Issue 表单和自动事件依赖默认分支上的文件。
- 仓库需开启 Issues 和 GitHub Actions。
- 在 Settings → Actions → General → Workflow permissions 允许 **Allow GitHub Actions to create and approve pull requests**。这里只使用创建 PR 能力，不自动批准 PR；无需添加 PAT 或其他长期密钥。
- 确认表单链接可用、工作流已启用后，再部署网站入口。未合入默认分支之前，不能视为投稿功能已上线。
- 新建测试投稿会产生真实 Issue 和 Actions 运行；上线验收由维护者主动提交测试插件。本轮本地测试使用假 API，不创建测试 Issue 或插件。

## 自动化安全边界

工作流只检出默认分支的可信工具，不检出作者仓库，不把 Issue 正文或输入值拼接进 shell。下载器无认证、禁用环境代理凭据，仅允许 HTTPS GitHub Release 及其指定资产域名跳转；限制包大小、下载时长、ZIP 条目与解压总量。它只读取清单和检查 ZIP CRC，不解压、不安装、不导入 Python。

`check` 任务只能读取仓库并写检查评论；只有维护者手动触发的 `approve` 任务可更新固定路径 `catalog/community.json` 并创建 PR。审批脚本仍会检查触发者的仓库写权限。写入目标固定为 `develop`，不会直接修改共享分支。

验证命令：

```powershell
python tests/check_plugin_submissions.py
python tests/check_contributing.py
python -m compileall -q tools tests
git diff --check
```

依据：[GitHub Issue 表单](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)、[Actions 安全使用](https://docs.github.com/en/actions/reference/security/secure-use)、[工作流权限设置](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-your-repository)。
