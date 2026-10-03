# 软件与插件展示网站

网站源码位于 `docs/`，与插件实现分别维护。使用 Node 22+ 与 npm。

本地预览已验证桌面 1440px 与手机 390px：截图切换、插件名称／平台搜索、分类筛选、无结果恢复、详情下载入口、SHA256 展开、安装锚点和文档搜索正常，无页面横向溢出及控制台警告／错误。

VitePress 使用稳定版 1.6.4；其旧 Vite 依赖覆盖为修复安全公告的 6.4.3，已通过实际构建和本地预览，`npm audit` 无漏洞。

```powershell
npm ci
npm run docs:build
npm run test:website
npm run docs:preview
```

预览地址：http://127.0.0.1:4173/DD_Monitor_Plugins/
日常修改使用 `npm run docs:dev`，地址 http://127.0.0.1:5173/DD_Monitor_Plugins/。

## 内容维护

- 插件列表和详情头部共用 `docs/data/plugins.json`。更新版本时核对实际清单、该版本附件下载地址与 SHA256，不使用全仓库 latest 下载地址。
- 新插件需补详情 Markdown、平台图标和目录导航；测试会检查版本、安装包及站内链接。
- 软件下载及兼容性见 `docs/download.md`。确认新安装包兼容后再更新说明，不能只按版本名称推断。
- 素材来源见 `docs/public/ASSETS.md`。仅复制网页优化副本，不修改软件原图。

## 2026-10-03 发布核实

只读查询 GitHub Releases API，未更改发布记录。

- 软件 latest 为 v0.2，标签指向 `41ee0c4`。公开 main 另经 `git ls-remote` 确认为 `9f007e8`，包含插件所需源码基线 `4bf84bc`。
- 当前附件 `DDMonitorCE-v0.2-exe.zip` 大小 331339180 字节，更新于 2026-10-01；SHA256 为 `a3b423be41e50ac66db9c9d898cad56601bcd36edcb2fc01a0740b293e54bc36`，与本机同名包一致。
- EXE 附件可能独立于标签替换。只读检查内嵌 PYZ 与包内文件后，确认缺少 ddm.live_danmaku、ddm.global_danmaku、ddm.auto_quality。因此不作为完整配套版本，源码入口单独提供。
- 插件 v1.0 附件名称、大小及 GitHub API SHA256 与本机 dist 文件一致（22886 / 18061 字节）。
- 上游插件 README 与 Release 中“公开 main 未同步基线”的文字已过时，网站不采用；本次未修改插件源码、安装包或公开 Release。

可复核安装包结构（仅检查，不运行 EXE；需要 PyInstaller）：

```powershell
python tools/inspect_host_package.py "软件安装包路径.zip"
```

## 可审阅部署方案（尚未启用）

拟使用现有公开仓库 `Lanpropro/DD_Monitor_Plugins` 的 GitHub Pages，默认 URL 为
`https://lanpropro.github.io/DD_Monitor_Plugins/`。这是拟发布地址，目前不作为已上线入口。

构建 base 已设为 `/DD_Monitor_Plugins/`。建议取得公开发布授权后：

1. 推送网站提交至插件仓库；不推送软件仓库。
2. 在仓库 Settings → Pages 中选择 GitHub Actions。
3. 添加发布工作流：检出 → Node 22 → `npm ci` → `npm run docs:build` → `npm run test:website` → 上传 `docs/.vitepress/dist` → `deploy-pages`。
4. 工作流只需 `contents: read`、`pages: write`、`id-token: write`，使用 `github-pages` 环境；提交和手动触发均可发布。
5. 部署后实测所有页面、手机布局、截图、两插件 ZIP 和软件附件，再交付公开 URL。

当前未创建部署工作流、未修改 Pages 或域名设置、未公开部署。自定义域名后续决定，首版不需要购买域名。
