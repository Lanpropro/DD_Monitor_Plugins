# 比赛二路同步：模块职责与生命周期

本轮仅迁移已有实现，保持行为、信号连接、调用顺序、缓冲和定时器参数。不把拆分本身当作播放速度或CPU占用改善。

| 模块 | 职责及状态所有者 | 边界 |
| --- | --- | --- |
| [canvas.py](canvas.py) | Canvas拥有当前显示帧、重复帧计数、裁切框、缩放/平移和鼠标手势状态，负责Qt绘制与选择交互 | 不拥有来源、解码器、音频时钟或播放定时器；按调用者传入的解码器/目标时间取帧。主画布父对象仍为picture.video，对照及选择对话框使用同一个类 |
| [audio.py](audio.py) | AudioPump拥有输出时钟、不可变输入快照、锁、供给计数与回看读取游标；AudioFeed拥有输出设备和工作线程定时器 | AudioPump仍以Viewer为QObject父对象，GUI定时器每10ms读取界面状态形成快照；AudioFeed无父对象，移动到既有QThread后才创建QAudioSink与10ms PreciseTimer |
| [viewer.py](viewer.py) | Viewer继续拥有房间集合、主副路选择、同步状态、布局、设置、播放调度及生命周期协调 | 使用canvas/audio的实际类，不创建第二套帧/音频业务状态。本轮保留RoomRow、ComparisonPanel和Viewer内其余协调职责 |
| [media.py](media.py) / [video.py](video.py) | 取流、解码、历史缓存和异步RGB准备 | 保持现有线程与缓存所有权；画布只消费已准备帧 |
| [engine.py](engine.py) | 匹配、缓冲、PCM路由、重采样与混音算法 | 音频模块依赖既有算法，未复制或改写算法 |
| [replay.py](replay.py) / [replay_preview.py](replay_preview.py) / [preview_frames.py](preview_frames.py) | 回看时间线、拖动预览与后台缩图准备 | 与原有播放时钟和缓存交互，未在本轮迁移或改动 |
| [floating.py](floating.py) / [upscale.py](upscale.py) | 悬浮裁切画面和可选增强 | 与Canvas共享同一来源帧语义，窗口重挂接仍由Viewer协调 |
| [panels.py](panels.py) / [sending.py](sending.py) / [send_design.py](send_design.py) | 控制台展示、发送业务和控件样式 | 保留既有接口，不随本轮拆分调整发送或账号行为 |
| [diagnostics.py](diagnostics.py) | 帧节奏、音频供给和分阶段数值诊断 | 接收既有对象引用；迁移后的调用栈会显示canvas.py或audio.py，不改变指标含义 |

## 音频启动与收尾

1. GUI线程调用AudioPump.start，先停止旧输出，建立设备格式及初始快照。
2. 新建QThread，父对象仍为AudioPump；创建AudioFeed后moveToThread，在线程started回调创建设备与供给定时器。
3. GUI定时器只更新不可变快照；工作线程消费快照、读取媒体历史、混音并写设备。设备方法和flush继续在音频线程调用。
4. stop先停止GUI定时器，再通过BlockingQueuedConnection调用AudioFeed.stop停止设备；随后quit/wait工作线程，按原deleteLater连接释放Feed、设备和线程对象，并清空快照/时钟状态。

没有新增线程，没有移动现有QObject到另一个线程，也没有复制可变状态。时钟仍包含设备位置插值和既有非阻塞锁路径；回看偏移、独立播放和声道选择保持。

## 兼容与检查

viewer.Canvas、viewer.AudioFeed、viewer.AudioPump仍导出所属模块中的同一个类，供已有调用者和检查脚本使用；viewer.RATE及viewer.QImage保留已有兼容入口。新内部调用和模块专用替身应直接依赖所属模块，不依赖viewer转发其全局变量。

[模块边界检查](../../tests/check_match_sync_module_boundaries.py)验证导出身份、Qt父子关系、GUI/音频定时器、真实QThread中的替身设备启动/flush/停止及重复收尾，不连接音频设备或直播。
既有音频时钟、音频读取、独立播放、回看、裁切交互、透明绘制及全屏回归验证行为；根run.cmd隔离加载安装副本完成集成验证。

仍未拆分的Viewer职责包括来源与房间生命周期、匹配/偏移协调、回看投递、录制协调、窗口/控制台布局及浮层操作。后续应按完整依赖边界分别迁移，不将同一个可变状态同时放入新旧模块。
