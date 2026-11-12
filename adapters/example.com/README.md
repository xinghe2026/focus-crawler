# example.com 适配器

演示第二层能力：`config.json` + 本地解析脚本。

- `list_parser.py`：从 `a.item-link` 提取子页链接与下一页。
- `detail_parser.py`：解析标题、正文、日期。
- 配置中 `parser.mode = "hybrid"`：优先执行脚本，失败时回退到 `detail` 选择器。

脚本约束：

- 只能做解析，不允许发起网络请求。
- 返回值必须是 `dict`。
- 执行超时由 `parser.script_timeout_ms` 控制。
