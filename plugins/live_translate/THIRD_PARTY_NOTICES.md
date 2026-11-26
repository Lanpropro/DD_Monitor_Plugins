# 运行库、模型与参考来源

插件原创代码沿用仓库根 LICENSE。商店包 `live_translate-1.0.zip` 包含插件源码、图标、许可和准备脚本。权重与运行库在用户确认后从上游源下载。

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)：Apache-2.0；图形准备使用官方 PyPI 的 [sherpa-onnx 1.13.8](https://pypi.org/project/sherpa-onnx/1.13.8/) 及 [sherpa-onnx-core 1.13.8](https://pypi.org/project/sherpa-onnx-core/1.13.8/) Windows x64 包，校验固定 SHA256，保留包内许可。
- [NumPy 2.5.4](https://pypi.org/project/numpy/2.5.4/)：BSD-3-Clause，Windows wheel 中附带依赖的许可按其包内声明；图形准备使用 CPython 3.12 x64 官方 wheel，保留许可文件。
- [Zipformer small 音频分类模型](https://k2-fsa.github.io/sherpa/onnx/audio-tagging/pretrained_models.html)：sherpa-onnx 官方 `audio-tagging-models` 发布，基于 AudioSet 训练；模型与标签通过显式准备脚本下载，标准源码 ZIP 不含权重，分发离线权重时须保留各自上游许可与来源。
- [Silero VAD](https://github.com/snakers4/silero-vad)：MIT；本插件使用 sherpa-onnx 发布的 ONNX 文件。
- [Qwen3-ASR 1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)：Apache-2.0。识别默认下载 [thieunv-asilla 的 sherpa-onnx INT8 导出](https://huggingface.co/thieunv-asilla/sherpa-onnx-qwen3-asr-1.7B-int8)，使用 [Wasser1462 导出工具](https://github.com/Wasser1462/Qwen3-ASR-onnx)，固定导出修订及文件摘要。
- [Qwen3 1.7B GGUF](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF)：Apache-2.0；本地翻译默认使用官方 Q8_0 权重。
- [llama.cpp](https://github.com/ggml-org/llama.cpp)：MIT；准备脚本下载官方 Windows CPU 包。
- PySide6、NumPy、FFmpeg 及用户选择的翻译服务／模型分别遵循自己的许可及使用条款。
- 方案参考：[译声](https://github.com/kayson-39/YishengTranslator-White) 与 [同传字幕](https://github.com/KorenKrita/simul-subtitle)。没有将其界面、资源或模型权重复制进插件包。
