# 运行库、模型与参考来源

插件原创代码沿用仓库根 LICENSE。标准 `live_translate-0.1.zip` 只包含源码、许可和准备脚本，不包含模型权重、第三方推理引擎或独立运行环境。单独的离线完整包会在插件目录的 `vendor/` 下携带 ReazonSpeech、Silero VAD、Qwen3 0.6B、llama.cpp 和便携 Python；对应许可文本位于 `vendor/licenses/`，不得用本插件的许可代替第三方许可。

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)：Apache-2.0；实际使用版本以独立环境为准。
- [ReazonSpeech](https://github.com/reazon-research/ReazonSpeech) 及 [日英 ONNX 导出版](https://huggingface.co/csukuangfj/reazonspeech-k2-v2-ja-en)：下载模型时核对上游模型许可及来源，不能用本插件的许可代替模型许可。
- [Silero VAD](https://github.com/snakers4/silero-vad)：MIT；本插件使用 sherpa-onnx 发布的 ONNX 文件。
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)：Apache-2.0；对应 ONNX 导出工具与运行库遵循各自上游许可。
- [Qwen3 0.6B GGUF](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF)：Apache-2.0；本地翻译默认采用官方 Q8 权重。
- [llama.cpp](https://github.com/ggml-org/llama.cpp)：MIT；准备脚本下载官方 Windows CPU 包，标准代码 ZIP 不包含它，离线完整包携带该运行目录。
- PySide6、NumPy、FFmpeg 及用户选择的翻译服务／模型分别遵循自己的许可及使用条款。
- 方案参考：[译声](https://github.com/kayson-39/YishengTranslator-White) 与 [同传字幕](https://github.com/KorenKrita/simul-subtitle)。没有将其界面、资源或模型权重复制进插件包。
