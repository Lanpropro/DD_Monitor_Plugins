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

## 2026-10-18 移动端开屏、插件目录与文档一致性

- 修复手机首页无法进入内容层：开屏墙原为 `touch-action: none`，纵向手势被整屏墙吃掉，而顶栏在滚动前保持隐藏，导致 390px 下没有任何导航入口。现改为 `pan-y`，触摸拖动只在横向超过阈值时接管，纵向手势交给页面滚动；手机上开始滚动后顶栏立即可见。
- 插件目录页脱离 VitePress 文档三栏布局（正文区原为 688px、卡片仅 335px），改为独立整页：1440px 三列、1100px 两列、760px 单列，窄屏不再横向溢出。
- 三款插件详情页补齐 1.1 / 1.2 更新记录，全部取自仓库 tag、`RELEASE-*.md` 与已发布 ZIP 内 README，不写推测内容。
- 重新核对下载入口：三款插件与软件 v0.4 的公开附件均返回 200，下载后计算的 SHA256 与 `docs/data/software-release.json`、`docs/data/plugins.json` 完全一致。
- 构建与 10 项网站回归通过；1440/1280/390px 实测开屏滑动、目录布局与控制台。

## 2026-10-17 开屏画面墙

开屏从标准网格一路迭代到当前形态，均集中在 `docs/.vitepress/theme/SplashWall.vue`：

- `1652bb3` 新增铺满首屏的画面墙，滚动时沉到内容下方；`18770a5` 支持拖动并跟随指针邻近放大。
- `7cdf039` 改为不规则错落摆放，并让顶栏滚动后才出现；`6a95d7d` 改回等尺寸格子、只保留摆放错落。
- `cdc886d` 漂移与放大调到肉眼可辨；`4bc5484` 改为 2×2 重复块按周期取模，四向拖动都无缝。
- `8d939b8` 进入内容层后墙面不再响应悬停放大，滚回顶部恢复。

## 2026-10-17 顶栏胶囊导航

- `e734d0f` 把导航收进顶栏圆角胶囊：五个图标 + 文字入口、当前页高亮、窄屏只留图标；同时撤掉右侧 Dock 的正文留白。
- 更早的 `e2223ee`（底部悬浮 Dock）与 `9225b6f`（BewlyCat 式右侧竖向 Dock）已被顶栏方案取代，仅作历史记录。
- `9a4e395` 统一插件卡片信息层次，`cad1f69` 让功能演示可直接拖动分隔条调比例。

## 2026-10-17 Dock 与功能演示

- 保留首页结构，用底部悬浮 Dock 替换主导航：五个文字/图标入口、SSR 当前页标记、精细指针下的轻微邻近放大、键盘焦点与手机安全区。
- 软件功能区提供比例滑块、深浅主题、观看组合的可操作示意，明确标注交互示意；真实截图仍保留在首屏，不冒充软件录像。
- 插件卡片补平台标签及流程切换，共用插件元数据中的三个操作步骤，保留原有版本和下载链接。
- 构建和9项网站测试通过，涵盖全站Dock链接与各页当前状态。浏览器验证比例滑块、主题、组合、插件流程、跨页导航、键盘操作；320px与390px窄屏无横向溢出，320px最大比例下无示意文字裁切，控制台无警告或错误。
- CSS/Dock遵循系统减少动态效果；不支持减少透明度媒体查询的浏览器保留默认材质，支持的浏览器可回退到不透明底座。未新增动画依赖。

## 2026-10-17 展示与动效更新

- 首页采用大幅真实截图、三套布局切换、0.4 功能介绍、三款插件卡片与独立下载按钮；首页和插件目录共用元数据。
- 动效使用原生 CSS、Vue Transition 与 IntersectionObserver：首屏错开入场、截图 200ms 淡化、内容一次性 500ms 渐入、精细指针悬停反馈。系统减少动态效果时不启动位移动画；无 JavaScript 或不支持观察器时保留完整静态内容。
- 已从 GitHub main README、v0.4 Release 及其 Assets 读取版本，软件直接下载固定为 `DDMonitorCE-v0.4-exe.zip`，校验值来自官方 Assets。版本数据在 `docs/data/software-release.json`，旧0.2证据保留在 legacyPackage 内。
- 插件下载按公开 Release 固定为国内1.2、海外1.1、比赛二路1.2；各自下载链接与SHA256已按公开附件核对。发布包可独立于当前源码工作树更新，不能用本机重打包哈希替代公开附件哈希。
- 构建、7项网站回归通过；1440px桌面与390px手机预览检查、快速切换截图、插件搜索分类和详情跳转通过，控制台无警告或错误。测试覆盖减少动态效果、缺少观察器、一次性观察及卸载清理。
- 本轮保持本地预览，未推送、未部署或修改账号配置。下方2026-10-03发布核实为历史记录，当前页面以本节和元数据为准。

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

2026-10-18 复核当前版本（已非本节记录）：软件 v0.4 附件 386636764 字节，SHA256 `453718d0…7cd17`，与本机 `results/DD监控室CE-v0.4-exe.zip` 一致；三款插件公开附件下载后计算的 SHA256 与网站数据一致（domestic_live 1.2 / global_live 1.1 / match_sync 1.2）。

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

## 2026-10-17 右侧 Dock 微调（已被顶栏方案取代）

- 参考 BewlyCat 的右侧竖向胶囊、圆形图标及向左文字提示，保留原有五个导航入口和当前页标记。
- 正文预留右侧空间；窄屏同样位于右侧并常显名称，短视口可滚动导航。
- 构建、9 项网站回归及 diff 检查通过；浏览器检查 320/390/768/1440px 布局、下载跳转和键盘进入插件页，控制台无警告或错误。
- 预览截图：work/website-preview/right-dock.png、right-dock-mobile.png。本轮仅本地修改。
- 参考源码：https://github.com/keleus/BewlyCat/blob/main/src/components/Dock/Dock.vue
- 后续 `e734d0f` 已把导航移回顶栏胶囊，本节仅作历史记录。

## 已知待办

- `plugins/match_sync/plugin.json` 仍为 1.1，但已发布安装包与软件仓库 `plugins_user/match_sync` 均为 1.2 / 1.2.8。网站展示按公开 Release 的 1.2，源码目录需要单独同步；插件仓库 README 的版本表同步前不要据其判断当前版本。
- 开屏画面墙的 224 个格子在手机上只用 8 列，格子偏大；如需更密的效果，改 `SplashWall.vue` 的 `@media (max-width: 700px)` 列数。

## 2026-10-18 主题扩散与滚动展示

- 主题沿用 VitePress 的持久化状态及原有切换入口，通过 `toggle-appearance` 接入按钮中心扩散的 View Transition；按钮有轻微压缩回弹，滑块采用 motion-web 的 CSS 弹性曲线。窄屏可直接切换主题，并修正菜单按钮内部容器被顶栏高度规则误伤的问题。
- 真实截图独立为 `ScreenshotShowcase.vue`：滚动时保持展示区域、从 100% 收拢到 82%，随后露出三种布局选项。保留原有真实截图及说明，去掉“查看完整截图”链接。
- 三种布局每 5 秒循环一次；离开视口、后台、悬停或键盘聚焦控制区时暂停，提供独立播放/暂停按钮及手动选择。减少动态效果或缺少观察器时使用静态展示与手动切换。
- 首页标题、功能说明、演示和插件卡片进入视口时才逐项显现；无 JavaScript 时仍保留所有正文与下载链接。
- 参考 Screen Studio（https://screen.studio/）的演示优先与滚动分段节奏，沿用现有开屏和内容结构；没有新增运行依赖。
- 构建、16 项网站测试与 diff 检查通过。测试覆盖三轮播状态、自动暂停、卸载清理、减少动态效果、主题扩散中心及快速连续切换。浏览器核验 320/390/1100/1280px、实际自动切换、手动选择与键盘操作，控制台无警告或错误。
- 本次预览使用 http://127.0.0.1:4174/DD_Monitor_Plugins/，避开已有 4173 服务。截图保存在 work/website-preview/scroll-screenshot-*.png。
