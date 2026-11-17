# FocusCrawler 项目归档（2026-11-16）

> 用途：项目阶段性收尾时的自述档案。记录**做了什么、怎么验证、哪里还没做到完全体**，
> 避免下次接手时把「能跑」误当成「完成」。

## 1. 项目定位

个人自用、配置驱动的通用聚焦爬虫（Windows 桌面端）。两条并行入口：

- **任务模式**：JSON 配置 + 图形界面（`focus_crawler.gui`），适合零代码抓取；
- **Spider 模式**：`focus_crawler.spiders`，类 Scrapy 的 Python 爬虫 API，适合复杂逻辑。

## 2. 能力矩阵

| 领域 | 能力 | 落点 | 验证方式 |
| --- | --- | --- | --- |
| 抓取 | httpx + 重试 + 限速 + robots | `core/fetcher.py`、`compliance.py` | 单测 + fixture 站点 |
| 解析 | CSS / XPath / 正则 / JSONPath / 属性 / 变换 | `core/parser.py` | 单测 |
| 解析 | 自适应元素定位（指纹 + 重定位 + 回写） | `core/adaptive.py` | 改版页面单测 |
| 解析 | 寻找相似元素 `find_similar` | `core/adaptive.py` | 单测 |
| 媒体 | yt-dlp 通用解析 / 下载（可配 Cookies、代理、画质） | `core/ytdlp_engine.py` | 单测 + 真实站点人工验证 |
| 媒体 | 分段并发下载（动态拆分 / 断点 / 限速） | `core/segmented_downloader.py` | 单测（Range mock） |
| 媒体 | ffmpeg 自动探测 + 二进制拼接降级 | `utils/ffmpeg.py`、`core/media_pipeline.py` | 单测 + `doctor` |
| Spider | 队列 / 并发 / 去重 / 优先级 | `spiders/engine.py` | 单测 |
| Spider | 自动油门（AIMD）+ Retry-After + 阻塞钩子 | `spiders/throttle.py` | 单测 |
| Spider | 断点（crawldir）/ 开发模式缓存 | `spiders/engine.py`、`cache.py` | 单测 |
| Spider | 多会话（HTTP / 浏览器）、代理轮换、DoH、CDP、网络捕获 | `spiders/sessions.py` | 单测（部分需真实环境） |
| Spider | 模板（Crawl / Sitemap / XMLFeed / CSVFeed / Shopify） | `spiders/templates.py` | 单测 |
| Spider | LinkExtractor + 导出（JSON/JSONL/CSV/XML） | `spiders/linkextractor.py`、`exporters.py` | 单测 |
| UI | 终端风 QSS + HUD + 自适应字段列 + yt-dlp/ffmpeg 状态 | `gui/theme.py`、`hud.py`、`dialogs.py` | 离屏截图 + GUI 冒烟 |
| 打包 | PyInstaller onedir（内置 yt-dlp + imageio-ffmpeg） | `scripts/build.ps1` | `FOCUSCRAWLER_SMOKE_TEST=1` 自检 |

## 3. 验证命令

```powershell
python -m pytest                      # 239 项
python -m focus_crawler doctor        # yt-dlp / ffmpeg / playwright 能力
powershell -ExecutionPolicy Bypass -File scripts/build.ps1   # 打包 + 冒烟
```

## 4. 12 项缺口状态（2026-11-16 第二轮补齐）

| # | 缺口 | 状态 | 落地与验证 |
| --- | --- | --- | --- |
| 1 | 广告域名库 | ✅ 完成 | 引入 Scrapling 同源数据 3526 条（`third_party/ad_domains.py`，BSD-3 附 LICENSE）；测试 `test_full_ad_domain_list_is_bundled` |
| 2 | HTTP/3 | ✅ 完成（可选） | `SessionSettings(http3=True)` 走 curl_cffi |
| 3 | Cloudflare Turnstile | ⚠️ 增强 | patchright 后端 + 反检测初始化脚本 + `wait_for_challenge()`；不承诺 100% 通过 |
| 4 | TLS 指纹 | ✅ 完成（可选） | `impersonate`；需 `pip install curl_cffi` |
| 5 | Spider 接 GUI | ✅ 完成 | 主窗口「Spider」页（`gui/spider_page.py`）：加载/识别/运行/停止/日志/结果/导出 |
| 6 | `Request.errback` | ✅ 完成 | `Request.errback → Spider.errback → on_error` 三级；测试 `test_errback_called_on_final_failure` |
| 7 | Cookie 持久化 | ✅ 完成 | HTTP 写 Netscape `cookies.txt`；浏览器写 `storage_state`；测试 `test_http_session_persists_cookies` |
| 8 | 每域 QPS | ✅ 完成 | 令牌桶 `RateLimiter`，与自动油门叠加；测试 `test_rate_limiter_enforces_qps` |
| 9 | 流式导出 | ✅ 完成 | `export_items_stream`；测试 `test_streaming_exporter_handles_many_items` |
| 10 | 真实站点 E2E | ✅ 完成 | `scripts/e2e_smoke.py` 起本地 HTTP 服务，5/5 通过（非 mock） |
| 11 | 分段下载真实 Range | ✅ 完成 | 同上，8 MiB 文件 4 连接分段，字节一致 |
| 12 | 视频 403 刷新 | ✅ 完成 | `refresh_video` + collector 视频分支；测试 `test_collector_refreshes_signed_video_before_download` |

**仍然存在的客观限制**：Turnstile 属对抗性能力，只能"提升通过率"不能承诺；HTTP/3 依赖
curl_cffi 的 h3 支持与目标站协商。这两项在文档与 UI 中均已如实标注。

## 4.5 B 站解析去重与入口位置（2026-11-16 第三轮）

- 新增 `adapter.bilibili_priority`：`ytdlp_first`（默认）/ `adapter_first` / `adapter_only`，
  同一页面只跑一条解析链路，避免与 yt-dlp 的 BiliBili extractor 重复。
- Spider 图形入口从「第一个标签」移到「最后一个标签（Spider（进阶））」，
  避免抢占日常任务主流程。

## 5. 复盘：这一轮为什么被判定为半成品

- 不是 watchdog 超时：所有工具调用正常返回，最后一次 `pytest` 239 passed、`git push` 成功。
- 真实原因：把「全部补齐」执行成了「每个能力点做一个最小实现」，深度不足；
  且这些降级只在 README 事后披露，没有在动手前征询。
- 已写入长期记忆的用户要求：**默认档位是完全体**——可用实现 + 自动化测试 + 真实端到端验证 + 文档，
  做不到的必须提前说明并征询。

## 6. 下一步建议（按价值排序）

1. 缺口 5（Spider 接 GUI）：决定产品形态是不是「一个工具」；
2. 缺口 1/8/4：影响长时间大规模抓取的稳定性与被封概率；
3. 缺口 2/3：属于「承诺了就要做到，做不到就明说」的诚实性问题；
4. 缺口 10/11：把 mock 测试升级为真实环境回归。

## 7. 冗余审计（2026-11-16 第四轮）

审计方法：用 AST 扫描全量 `src/focus_crawler`，统计「顶层定义被引用次数」与「同名函数跨文件出现」，
再人工判定哪些是真冗余（而非有意分层）。

### 7.1 删除的死代码（10 处）

| 文件 | 定义 |
| --- | --- |
| `config/loader.py` | `list_config_files`、`resolve_relative` |
| `core/compliance.py` | `require_copyright_confirm` |
| `core/failure_classifier.py` | `describe_error` |
| `core/media_common.py` | `image_candidates_from_config` |
| `core/media_downloader.py` | `download_images` |
| `core/recipe_manager.py` | `copy_recipe` |
| `utils/hash.py` | `sha256_bytes` |
| `utils/paths.py` | `ensure_app_dirs` |
| `gui/task_editor.py` | `_line` |

### 7.2 合并的重复实现

| 冗余 | 处理 |
| --- | --- |
| `audio_adapter` / `video_adapter` 各自的 `adapter_for`、`_load_adapter`、`_validate_results` | 收敛到 `core/adapter_registry.py`，两处只留兼容薄封装 |
| `host_fallback._rule_matches` 与 `hotlink._rule_matches` | 统一到 `utils/urls.host_matches` |
| `dynamic_sniffer._dedupe` 与 `media_common._dedupe` | 保留一份通用去重 |
| `adaptive.domain_of` / `throttle.domain_of` | 统一到 `utils/urls.host_of` |
| `adaptive.clean_text` 与 `utils.text.clean_text` | 复用后者 + 截断 |
| `video_sniffer.parse_m3u8` / `pick_variant` 与 `core/hls.py` | 删除重复解析器，统一用 `hls.parse_playlist` |
| 主窗口三套 `_on_*_download` 启动逻辑 + 12 个回调 | 合并为 `_start_media_download(kind)` + 4 个统一回调 |
| `spiders/loader.load_module` 与 `ytdlp_engine.load_module` 同名不同义 | 改名 `load_spider_module` |

### 7.3 顺带补上的功能（原本是死代码/缺失入口）

- `recipe_manager` 原本只有测试在用 → 接到「导入/导出配置」：支持 `.zip` 任务包
  （配置 + 引用的解析脚本一起打包），`.json` 仍可用；
- Spider 从标签页改为 **工具栏「Spider」按钮 + 独立窗口**，并新增「新建模板」按钮。

### 7.4 教训

合并 `_on_image_done` 等回调时，我漏掉了线程结束后的媒体表格刷新（`_load_media_pages` 这个方法并不存在），
是人工核对原有实现时发现的——**重构后必须走一遍真实路径**，只跑单测不足以发现这类"回调里调了不存在的方法"。

### 7.5 数据

- 顶层定义被引用 ≤1 次的：14 → 2（剩余 `parse_m3u8` 已删除、`cbc_encrypt` 为 AES 测试用的对偶实现，保留）
- 源码净行数：19833 → 19762（净减 71 行；新增了 `adapter_registry.py`、`utils/urls.py` 等公共模块，
  但消除了 8 类重复实现）
- 测试：255 项通过
