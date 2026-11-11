# 实时翻译 v0.1

开发预览。插件 ID：`live_translate`，标准代码包名为 `live_translate-0.1.zip`，另提供包含模型的离线完整包 `live_translate-0.1-offline.zip`。

## 两个安装包

- `live_translate-0.1.zip`：仅插件源码，可由宿主“设置 → 插件”直接导入。首次使用需要准备本地模型；也可以只使用 API 翻译或仅原文模式。
- `live_translate-0.1-offline.zip`：附带 ReazonSpeech、Silero VAD、Qwen3 0.6B Q8、llama.cpp 和便携 Python。包体超过宿主导入器限制，解压后关闭宿主并执行包内 `install.ps1 -HostRoot <宿主目录>`；设置窗口会使用插件自身 `vendor` 中的路径。

内嵌字幕需要宿主提供 `set_tile_caption()` / `clear_tile_caption()`。旧宿主没有该接口时自动回退独立字幕窗；进程内识别和进程归组不依赖宿主修改。

## 首批范围

- 从宿主 `stream.resolved` 事件获取所选直播间的流地址和请求头，后台 FFmpeg 只解码音轨，不采集系统混音。
- YouTube／Twitch 音轨沿用宿主的系统／环境代理解析及本机 HLS 转发；FFmpeg 直连回环地址，由转发层处理上游代理、TLS 和分片重试。停止传译时一并停止转发，不内置代理内核。
- 一次只开启一个直播间；在功能中心或格子右键「实时翻译…」进入。
- 关注栏左侧功能轨可单独固定「实时翻译」图标，沿用宿主的功能中心固定操作；竖屏时使用顶部功能中心。
- 点击图标打开独立、非模态的设置窗口，再次点击收起；设置不替换直播墙或改变格子布局。右键房间入口始终打开设置并选中该房间。
- 本地 CPU 识别：默认 ReazonSpeech 日英 INT8；提供 Qwen3-ASR 0.6B INT8 目录适配，供多语言及精度对比。识别依赖在 `DD监控室CE.exe` 进程内加载，正常路径不再启动插件管理的 Python 子进程；推理在可停止的后台线程运行。FFmpeg 与本地 `llama-server.exe` 仍是独立子进程，但以宿主同名的临时启动镜像运行，任务管理器可将它们归入 `DD监控室CE` 组；FFmpeg 的同目录 DLL 依赖会一并准备。
- 原文先显示，再显示译文。可选择插件管理的本地翻译、用户自备 API 或仅显示原文；API Key 使用当前 Windows 用户的 DPAPI 加密保存，明文不进入插件配置。
- 优先使用宿主提供的直播格字幕覆盖层，位于视频和弹幕之上；全屏显示，画中画隐藏。字幕有独立显示开关，可设置原文/译文组合、总行数、字体大小、黑色背景透明度和字幕框宽高；背景逐行贴合实际文字宽度。播放栏显示或隐藏时自动调整字幕安全区域。旧宿主没有覆盖层接口时回退到独立字幕窗。更换直播源（画质、线路或续签地址）时保留当前会话，自动替换 FFmpeg 输入和识别队列并接续传译；只有关闭字幕窗、点击停止、移除房间或关闭宿主时结束会话。收起设置、按 Esc 或关闭设置窗口继续传译，再次打开保留参数。
- 记录最多 300 条阶段耗时及资源样本，可以导出 JSONL；不记录字幕正文、流地址、请求头、API Key。
- 音轨启动、首批 PCM 和退出原因同时写入宿主日志。FFmpeg 错误最多暂存 8 KiB，仅导出白名单类别、退出码、耗时和音频字节数，不写出原始错误文本。

本批不延后音视频，也没有实现按播放器 PTS 对齐字幕；因此不能用于验收「2 秒目标、5 秒上限」。下一批以实播数据确定压缩缓存、源时间戳与播放桥接。无需为了试用而修改宿主播放器。

## 准备本地识别

宿主需包含功能中心及 `context.recording.options()` 接口。最低版本号只是初筛，请使用配套根目录 `run.cmd` 验收。正常插件路径要求宿主为 CPython 3.12；离线完整包会从自身 `vendor/python/Lib/site-packages` 提供 NumPy 与 sherpa-onnx，不需要另行启动 Python 进程。

用已安装的 Python 3.12，在插件源码仓库运行：

```powershell
python plugins/live_translate/prepare_runtime.py --directory work/live-translate-runtime
```

脚本在指定目录建立独立运行环境，安装 NumPy / sherpa-onnx，下载约 77 MB 的 ReazonSpeech 权重及 Silero VAD，再准备约 25 MB 的 llama.cpp Windows x64 CPU 运行环境和约 640 MB 的 Qwen3 0.6B Q8 翻译模型。完成后打印模型和运行库路径；`python` 字段现在只用于定位运行库，不再用于启动识别进程。本地翻译首次准备需要网络，准备完成后使用不需要联网或 API Key。只准备识别时可加 `--recognition-only`。安装标准插件 ZIP 后，脚本位于安装目录 `plugins_user/live_translate/prepare_runtime.py`。

离线完整包已经把上述模型、llama.cpp 和便携 Python 放进插件自己的 `vendor/`。由于宿主插件安装器限制单包不超过 100 MB，离线包需要通过包内 `install.ps1 -HostRoot <宿主根目录>` 手动复制到 `plugins_user/live_translate`；插件会自动填入有效路径，不需要首次下载或填写 API Key。

在设置窗口填写打印出的运行环境和模型目录。标准代码包只含插件源码与许可文件；模型、虚拟环境和缓存由准备脚本放在外部目录。离线完整包单独携带模型、llama.cpp 和便携 Python，便于无网络首次使用。

ReazonSpeech 模型目录应包含：

```text
encoder-epoch-35-avg-1.int8.onnx
decoder-epoch-35-avg-1.onnx
joiner-epoch-35-avg-1.int8.onnx
tokens.txt
silero_vad.onnx
```

Qwen3-ASR 使用 sherpa-onnx 官方导出目录，需包含 `conv_frontend.onnx`、`encoder.int8.onnx`、`decoder.int8.onnx`、`tokenizer/{vocab.json,merges.txt,tokenizer_config.json}`，并放入 `silero_vad.onnx`。本脚本不自动下载约 1 GB 的 Qwen 权重。参见 [sherpa-onnx 模型说明](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html)。Qwen 识别自动检测语言；界面的原声语言用于翻译提示词。

## 翻译与千问

普通 Qwen 文本模型与 Qwen3-ASR 是不同模型，分别负责译文与听写。两者不能互相替代。

- **本地翻译**：默认选项。填写准备脚本输出的 `local_model` 和 `local_server`（开发版已预填）。开始传译时自动启动并加载模型，停止传译或关闭字幕窗后结束进程、释放权重。不需要自行启动服务，也不需要填写地址、API Key。
- **API 翻译**：显示接口地址、模型名称和 API Key。Key 通过当前 Windows 用户的 DPAPI 加密后保存在插件设置中，重新打开面板或宿主后自动解密填入；清空输入框并离开焦点可删除保存值。可填写在线兼容接口，或已有本地 Ollama / llama.cpp 服务的 `/v1` 基地址及其实际模型标识。这个模式不启动插件的本地翻译进程，且只发送识别文字。
- **仅显示识别原文**：不启动翻译模型、不发翻译请求，保留原文字幕用途。
- API 失败、达到等待上限或前一个请求仍未结束时，当前句保留原文，不堆积翻译任务。已超时请求的迟到结果不覆盖新字幕。翻译方式在运行期间锁定，停止后可切换，避免界面选项与实际运行服务不一致。

本地候选使用官方 Qwen3 0.6B Q8 GGUF，由固定版本 llama.cpp `b12211` 运行。准备脚本校验官方运行包摘要和模型 SHA256。采用 CPU、每模型默认 2 线程、1024 token 上下文、一个推理槽位，关闭思考输出；API 模式也发送 `/no_think` 提示，但实际行为取决于服务。小模型可能出现漏译或用词不准确，尤其是缺少上下文的直播短语；目前只是本地可用基线，不代表已证明它与较大模型同精度或内存最优。

## 资源与延迟边界

模型仅在开始后由宿主进程内的识别线程加载；只保留一个识别实例和最多一个本地翻译进程，停止后统一结束识别线程并终止翻译进程、删除同名启动镜像，释放权重引用。设置窗口收起不停止字幕任务。每个本地模型默认 CPU 两线程，可在 1～8 线程调整，本地翻译固定不使用 GPU。

PCM 为 16 kHz 单声道 s16le。单个消息最多一秒，Qt 待发队列最多约两秒；超载时丢弃输入，下一次有效输入重置 VAD，避免拼接不连续音频。操作系统管道和 FFmpeg 还有自己的少量缓冲，因此该值不是整个链路的延迟上限。VAD 停顿为 250 ms、最长语段 1.8 秒；短语切分可能损伤词和语义，须在真实直播验收，不能声称与整句识别同精度。

翻译仅允许一个请求在途。超过配置等待时间后继续本地识别；旧请求结束前不再提交新翻译。资源记录中的 `asr_ms` / `translation_ms` 是完成分段后的处理耗时，不包含采集、排队、断句与播放，不能当作端到端延迟。

`child_rss_bytes` 现在只合计 FFmpeg 和插件管理的本地翻译子进程 RSS；识别模型与推理分配在宿主进程内，必须结合宿主 RSS 与 `recognition_embedded` 样本阅读，不能把该字段当作完整内存。API 模式下调用的外部服务、宿主 Qt / 播放器增量同样必须另行采集。后台任务和字幕历史均有容量上限；尚未设置未经测量的硬性内存预算。下载文件大小不代表运行内存。

## 字幕覆盖层

新宿主通过 `context.set_tile_caption()` / `clear_tile_caption()` 提供字幕覆盖层。插件只在当前会话绑定的格子上提交文本，不把字幕加入弹幕列表，也不改变弹幕轨道：

- `enabled`：独立显示开关；关闭只清除字幕，不停止识别和翻译。
- `mode`：`both`、`original`、`translation`。
- `lines`：总行数预算 1–4；默认原文和译文各占一行，更多行数用于换行。
- `opacity`：黑色圆角背景不透明度 0–100。
- `font_size`：字体像素大小 10–64；过大时宿主会按当前字幕框高度安全截断，避免文字被裁切。
- `width` / `height`：相对视频宽度和可用高度的百分比；播放栏出现时可用高度会缩短。

字幕覆盖层由宿主管理，使用宿主内置思源字体绘制中日文，不依赖系统字体回退或 `VideoDanmaku` 的私有绘制状态。黑色背景按每一行的实际文字宽度实时绘制，不再覆盖整个字幕框。旧宿主返回不可用时不报错，继续使用独立 `Captions` 窗口。

## 人工验收

1. 将发布附件中的源码 ZIP 导入宿主插件页面，启用并重启；统一从宿主根 `run.cmd` 启动。离线完整包按包内 README 手动安装。
2. 播放一间日语或英语直播，右键选择「实时翻译…」。选择「本地翻译」，确认识别及本地翻译的路径已就绪，不填写 API Key。
3. 连续点击左侧图标，设置窗口应打开、收起、再次打开，直播墙和格子布局始终保持可见。修改任一参数，收起再打开应保留；从另一房间右键打开时选中对应房间。
4. 开始后应先显示原文、再显示本地生成的译文，资源统计能区分宿主内识别线程与 FFmpeg/本地翻译子进程。收起设置、按 Esc 或关闭设置窗口，字幕应继续；关闭字幕窗后，识别线程、本地翻译及 FFmpeg 应结束。本地模型准备完后可断网复测。
5. 停止后切换「API 翻译」，应显示地址／模型／API Key。填写自行准备的接口重新开始，应正常生成译文且不启动插件本地翻译进程；重启宿主后 Key 应仍在密码框中，配置文件只出现密文字段。断网或接口变慢时原文应继续，翻译不无限排队。选择「仅显示识别原文」时不产生翻译请求。
6. 切换画质或直播源后，传译应自动接续，不应退回停止状态，也不应重新加载本地翻译模型；切房、停止播放、移除格子、关闭宿主时字幕不再更新且进程结束。
7. 对同一批真实直播分别记录识别及译文错误、处理耗时、超载丢弃、宿主和子进程内存。持续运行至少 30 分钟，导出记录。2 秒／5 秒端到端验收待下一批同步播放实现后进行。

自动替身检查、真实嵌入线程样本和官方语音样本只验证功能回归，不证明真实直播的精度、延迟或性能改善。用户已确认进程分组、字幕覆盖层、换源续传和 API Key 持久化验收通过；2 秒／5 秒端到端延迟和连续 30 分钟资源趋势仍需后续实测。

## 开发验证

```powershell
python tests/check_live_translate.py --host ../DD_Monitor_CE
python tests/check_live_translate_caption.py --host ../DD_Monitor_CE
python tests/check_contributing.py
python -m compileall -q plugins tests tools
git diff --check
```

第一条需使用带 PySide6 的宿主开发环境。使用明确选择的语音样本运行 `tests/check_live_translate_embedded.py --host <宿主根> --model-dir <模型目录> --python <含 NumPy/sherpa-onnx 的运行时> --sample <语音文件> --ffmpeg <FFmpeg路径> --engine qwen3_asr`，验证宿主进程内线程实际产出非空字幕；该检查只取最多 20 秒，不评定识别精度或直播性能。`tests/check_live_translate_model.py` 保留为独立 `worker.py` 的诊断入口，不是正常插件路径。

准备好本地翻译后，用宿主开发环境执行 `tests/check_live_translate_local.py --server <llama-server.exe> --model <翻译GGUF>`，验证真实服务就绪、中文输出及停止回收。测试短句不替代真实直播精度、内存或延迟验收。

完整录音回归入口为 `tests/check_live_translate_pipeline.py`，通过本机 HTTP 录音样本依次运行真实 FFmpeg、本地识别和本地翻译，并核对子进程总内存统计及停止回收。需要显式指定样本、模型、运行环境及 FFmpeg 路径；这个回归入口不读取真实账号，不用作实播性能证据。

## 模块职责与来源

| 模块 | 职责 |
| --- | --- |
| `plugin.py` | 宿主入口、单一流快照来源、切房与卸载协调 |
| `panel.py` | 独立非模态设置、字幕与诊断导出界面；Qt 对象由宿主窗口／插件拥有；设置可见性不控制任务生命周期 |
| `session.py` | GUI 线程中的 FFmpeg QProcess、识别线程控制面、容量控制、迟到结果过滤、停止回收 |
| `audio_diagnostics.py` | 有界 FFmpeg 错误分类，不导出原始 stderr、地址或请求头 |
| `options.py` / `protocol.py` | 模型与参数校验、FFmpeg 参数、PCM 消息边界 |
| `recognition.py` | 本地识别及 VAD；仅在工作进程导入权重依赖 |
| `translation.py` | 文字翻译协议、等待截止与单请求调度 |
| `local_translation.py` | 本地 CPU 翻译服务进程、就绪检测及停止；由 Session 统一拥有和计入资源 |
| `embedded_recognition.py` | 宿主进程内的识别线程，有界输入队列和模型生命周期 |
| `host_process.py` | 为 FFmpeg/本地翻译创建宿主同名临时启动镜像并清理 |
| `caption_style.py` | 字幕显示组合、行数、透明度、框宽高和持久化映射 |
| `secret_store.py` | 使用 Windows DPAPI 加密和解密 API Key；不写入明文 |
| `worker.py` | 兼容/诊断用独立推理入口；正常插件不启动 |
| `prepare_runtime.py` | 显式准备识别环境、识别和翻译模型、CPU 推理程序；不在插件装载时下载 |

主要方案参考 [译声](https://github.com/kayson-39/YishengTranslator-White)，小模型与静音填充参考 [同传字幕](https://github.com/KorenKrita/simul-subtitle/blob/main/docs/asr.md)。当前实现独立编写；第三方运行库与模型的许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。仍待完成的职责是单次取流复用、音视频延后播放、源 PTS 对齐以及真实直播对比。
