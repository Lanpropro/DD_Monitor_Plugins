# DD 监控室 CE 插件

[DD 监控室 CE](https://github.com/Lanpropro/DD_Monitor_CE) 的独立插件集合。
每个插件单独启用、单独维护版本并提供一个可导入的 ZIP。

## 插件

| 插件 | ID | 版本 | 支持平台 |
| --- | --- | --- | --- |
| [国内直播平台](plugins/domestic_live/README.md) | `domestic_live` | 1.1 | 提供虎牙、斗鱼、抖音的主播，直播流解析 |
| [海外直播平台](plugins/global_live/README.md) | `global_live` | 1.1 | 提供Twitch、YouTube平台的主播，直播流解析 |
| [比赛二路同步](plugins/match_sync/README.md) | `match_sync` | 1.0 | 提供保持单一直播流情况下的多路混音，合并弹幕观看 |

## 安装

1. 使用最新版 DD 监控室 CE。
2. 从 [Releases](https://github.com/Lanpropro/DD_Monitor_Plugins/releases) 下载需要的插件 ZIP。
3. 在软件“设置 → 插件”选择“装载插件…”或将 ZIP 拖入插件页面。
4. 勾选插件，点击“保存并重启”。

ZIP 内必须只有插件 ID 对应的顶层文件夹。GitHub 自动生成的 Source code ZIP
包含整个仓库，不能直接作为单插件安装包导入。
手动安装时，将 `plugins/<ID>` 文件夹复制到软件的 `plugins_user/<ID>`。
已存在同 ID 的插件不能重复导入；更新前通过插件页面删除旧版本，再导入新包并重启。

## 软件依赖

需要包含直播平台扩展支持的配套软件构建，源码基线为 `4bf84bc` 或之后的版本。
当前软件公开仓库的 `main` 尚未同步该开发基线；安装前请确认使用配套更新的软件。
软件显示的 `v0.2` 名称不足以判断是否包含这些接口。

插件通过软件提供的 `ddm.plugins` 平台接口、VLC 播放器、FFmpeg 转流、预览和弹幕模块运行。
海外自动画质需要软件提供 HLS 下载监测与自动画质控制；建议使用软件最新版本。
源码运行时，在软件仓库安装其 `requirements.txt`，其中固定 `streamlink==8.6.1`。
便携版使用软件随附的依赖。直播平台请求使用软件当前网络及代理配置。
YouTube 已结束的直播可保留关注，但不会将录像当作正在直播播放。

## 开发与打包

目录 `plugins/<ID>/` 包含 `plugin.json`、`plugin.py`、README 和所需资源。
清单 `id` 必须与目录名一致，版本填写在各插件的 `plugin.json` 中。

先将发布文件加入 Git，再执行：

```powershell
git add plugins tools tests README.md LICENSE NOTICE.md .gitignore
python tools/build_plugins.py
python tests/check_packages.py --host ../DD_Monitor_CE
```

输出为 `dist/domestic_live-1.0.zip` 、`dist/global_live-1.0.zip` 和 `dist/match_sync-1.0.zip`，上传到 Releases。
构建只读取 Git 跟踪文件，并将许可与来源说明放入每个安装包。
各插件 README 中的播放、预览和弹幕集成测试在软件源码仓库执行，需先安装对应插件。

## 来源

首次提取自 DD 监控室 CE 的 `4bf84bc`，插件源码在本仓库维护。
保留原项目 LGPL-2.1 许可及现有第三方声明，详见 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)。
