# 展示网站已迁移

网站源码、前端依赖、素材、测试与部署配置现独立维护：
https://github.com/Lanpropro/DD_Monitor_Website

本地文件夹：`F:/CodexAppManager/Code/DD_Monitor_Website`。

```powershell
cd F:\CodexAppManager\Code\DD_Monitor_Website
npm ci
npm run dev
```

预览：http://127.0.0.1:5173/DD_Monitor_Website/
构建与验证：在网站仓库运行 `npm run build`、`npm test`。

2026-10-22迁移保留网站相关提交历史，并完整带入主播名称、卡片文案和截图说明的本地修改。
插件源码、打包工具、已有标签及 Releases 留在本仓库；软件与插件的下载地址不变。
完整记录见网站仓库 MIGRATION.md；本机备份位于 work/website-migration-20261022/。
