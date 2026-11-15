# 私有 YAML 解析依赖

PyYAML 6.0.3 的 `lib/yaml/*.py` 纯 Python 原始源码（16 文件，213,655 字节），MIT 许可证见 `yaml/LICENSE`。

- 上游：https://github.com/yaml/pyyaml
- 固定提交：`49790e73684bebad1df05ef8d828fa12f685bffb`
- 标签：`6.0.3`
- 官方源 ZIP：https://codeload.github.com/yaml/pyyaml/zip/refs/tags/6.0.3
- 源 ZIP SHA256：`673e7e7de556542598ab38f43e09e714d028383d8d8a62b3239c4e3541c8227b`

保留的源码未修改，省略 LibYAML C 扩展及其绝对导入桥接 `cyaml.py`，放在插件私有命名空间。只调用 `safe_load`，在载入 YAML 会话时解析至多 4 MiB 的已有资料，提取 DNS／hosts 后释放文档；JSON 使用标准库，节点 URI／Base64 仍交由 Mihomo 处理，均不加载 YAML 库。无额外 pip 安装、服务、后台线程或网络请求，不导入或替换宿主的全局 `yaml` 包。
