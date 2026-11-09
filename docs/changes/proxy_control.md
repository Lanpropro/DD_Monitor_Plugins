# 网络代理增量记录

插件 ID：`proxy_control`。尚无公开发布基线。

## 待发布

开发预览，等待用户真实线路与交互验收。本批提供系统／直连、连接已有 HTTP／SOCKS5、内核管理订阅与节点两种入口，并附独立设置窗口和左侧图标。使用范围、个人资料保存位置和验收步骤见 [插件说明](../../plugins/proxy_control/README.md)。

本机服务与实际 Mihomo、mpv、FFmpeg 的协议和回收回归已经执行，不据此宣称用户真实代理、直播稳定性或内存对比已经通过。

2026-11-09 自动验证：`check_proxy_control.py --host <宿主> --core <官方内核>` 21 项通过；包含无效订阅的 COMPATIBLE 占位节点拒绝、实际内核停止回收和官方程序准备。贡献文档 13 项、临时目录包安装与装载、编译和差异检查通过。宿主根 `run.cmd --check-sidebar-navigation` 两项通过；真实节点线路与用户交互仍待验收。

## 集成与发布状态

- 分支：`function/proxy-control`，从 `origin/develop` 创建，尚未合入 develop。
- 宿主：复用现有网络栈及功能轨接口，本批不修改宿主源码。
- 内核：官方 Mihomo v1.19.33 Windows amd64 compatible，固定下载摘要，未进入插件 ZIP。
- 下一步：用户在根 `run.cmd` 中分别验证自己的现有代理、订阅／节点、YouTube／Twitch 播放与弹幕，记录稳定阶段内存，完成后再标为人工验收通过。
- 发布：本地候选尚未发布到 GitHub Release、插件商店或网站。
