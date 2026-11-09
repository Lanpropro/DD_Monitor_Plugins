# RTMP 协议依赖

- [PyRTMP 0.3.0](https://pypi.org/project/pyrtmp/0.3.0/)，MIT，源码归档 SHA256 `2af92ed10382da490b8971b601bb8a16c45cd643deea72d4d5ca21d3d46d89fb`。
- [bitstring 4.0.2](https://pypi.org/project/bitstring/4.0.2/)，MIT，源码归档 SHA256 `a391db8828ac4485dd5ce72c80b27ebac3e7b989631359959e310cd9729723b2`。

各目录保留原始LICENSE。仅纳入运行源码；私有相对导入避免污染宿主模块空间，PyRTMP的tokenparser从bitstring 4.0.2顶层读取，移除上游全局DEBUG日志配置和三个常见FMLE辅助命令的误警告，不修改协议算法。bitstring使用不依赖C扩展的固定纯Python版本，不在运行时下载依赖。
