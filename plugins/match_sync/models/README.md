# 局部超分模型

`FSRCNN_x2.pb` 为 Saafke 的 FSRCNN TensorFlow 实现提供的未修改预训练模型。
来源版本：`6a4812c4ef1c4f5947d79beafa32a05a6eb4a94d`。

- 来源：https://github.com/Saafke/FSRCNN_Tensorflow
- 文件：https://raw.githubusercontent.com/Saafke/FSRCNN_Tensorflow/6a4812c4ef1c4f5947d79beafa32a05a6eb4a94d/models/FSRCNN_x2.pb
- SHA256：`366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c`
- 许可：Apache-2.0，全文见同目录 `FSRCNN-LICENSE.txt`。

运行使用宿主已有 OpenCV DNN 与 NumPy，不需要 TensorFlow/PyTorch，也不会在播放时下载模型。
优先使用 OpenCL，设备不可用或推理失败时回退 CPU。
这是单帧轻量超分，不保证恢复来源已经丢失的真实细节，也不保证视频时序一致性。

后续模型通过 `upscale.Fsrcnn2x.upscale(QImage) -> QImage` 的后端接口替换；
队列、时间戳、超时与普通缩放回退由 `CropEnhancer` 管理。
