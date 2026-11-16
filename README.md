# FocusCrawler

个人自用、配置驱动的通用聚焦爬虫。通过修改任务 JSON 配置切换目标网站，无需重写程序。

## 当前状态

已完成里程碑 0 - 13：

| 功能 | 状态 |
| --- | --- |
| 项目骨架、路径与日志工具 | 完成 |
| JSON 配置加载与校验 | 完成 |
| SQLite 存储（URL / items / media / failures / parser_runs 等） | 完成 |
| httpx 静态抓取 + parsel 解析（CSS / XPath / 正则 / JSONPath） | 完成 |
| 调度器、去重、断点续爬、失败重跑 | 完成 |
| 通用链接发现（selector / self / auto） | 完成 |
| 子页多类型采集（data / image / video / audio，静态发现） | 完成 |
| 媒体去重、`discover_only` / `auto_download` | 完成 |
| Excel 多 sheet 导出 | 完成 |
| 自定义解析脚本（custom / hybrid、超时、异常、返回值校验） | 完成 |
| 规则预览 / 链接发现预览 / 子页采集预览 | 完成 |
| 图片扫描、过滤、去重、直链下载 | 完成 |
| 图片完整采集：批量并发下载、同名去重、跳过已完成、暂停/停止、失败入库 | 完成 |
| GUI 图片采集页：来源页筛选、缩略图、全选、批量下载与进度 | 完成 |
| 视频 HLS：主列表清晰度选择、ts 分片、AES-128 解密、ffmpeg 合并 mp4 | 完成 |
| 视频 DASH：MPD SegmentTemplate / SegmentTimeline / SegmentList，fMP4 拼接 | 完成 |
| 视频适配器：B 站 `playurl` durl 完整 MP4（配置开关 `video.adapter.bilibili`） | 完成 |
| 单页采集：`crawl.max_depth = 0` 时把起始页作为详情页采集 | 完成 |
| GUI 视频采集页：批量下载、进度、暂停 / 停止 | 完成 |
| 音频 HLS / DASH：EXT-X-MEDIA 独立音轨、AES-128、ffmpeg 合并 m4a，直链与 fMP4 保存 | 完成 |
| 音频适配器：B 站 `dash.audio`、网易云外链、域名开关、超时 / 异常 / 返回值校验与 `adapter_error` 分类 | 完成 |
| 音频地址刷新：下载前按来源页重新解析 B 站音轨并按码率匹配，避免旧任务的过期签名地址返回 403 | 完成 |
| 自定义适配器脚本（`audio.adapter.custom_script`，本地脚本需导出 `resolve_audio`） | 完成 |
| GUI 音频采集页：来源页筛选、全选、批量下载、进度、暂停 / 停止、商业音乐平台高风险版权提示 | 完成 |
| GUI 主窗口：任务列表（Ctrl/Shift 多选）、新建/编辑/复制/手动删除/批量删除任务、重名任务自动追加（1）（2）、手动刷新、预览、运行监控（暂停/停止）、日志、数据预览、失败中心、导出、设置 | 完成 |
| 新建任务默认配置：单页采集（`max_depth=0`）、子页采集开启、图片/视频/音频启用、B 站适配器开启、默认不勾选 robots；版权确认默认勾选 | 完成 |
| 下载位置与管理：媒体页「打开文件位置」定位文件、「删除文件」删除本地文件并重置记录；设置页可更改媒体下载根目录 | 完成 |
| 采集页文件名筛选：图片 / 视频 / 音频页可按文件名排除显示（逗号分隔、包含匹配、区分隐藏数量） | 完成 |
| 采集页序号范围勾选：图片 / 视频 / 音频页输入 `1-10, 15` 按当前可见行序号批量勾选（替换原勾选，隐藏行不参与）；操作按钮分两行布局；首列为选择复选框、行号在左侧表头，过长文件名 / 路径按省略号显示 | 完成 |
| 防盗链插件：自动/规则 Referer、域名与 URL 正则规则、全局/规则请求头、浏览器兼容头、自动 Origin，作用于图片、视频、音频的发现与下载 | 完成 |
| 媒体主机回退：域名解析失败（如 `WinError 11001`）时按 `media_fallback.rules` 切换到同路径镜像主机下载，任务编辑器「媒体与导出」页可配置 | 完成 |
| Playwright 子页动态嗅探（`render_for_media`）：逐子页加载、监听网络视频 / 音频、失败回退静态、受最大子页数限制 | 完成 |
| yt-dlp 通用解析（`video/audio.adapter.ytdlp`）：1750+ 站点视频 / 音频候选，`off / auto / force` 三种模式，平台适配器优先、yt-dlp 补漏 | 完成 |
| yt-dlp 下载：按页面地址重新解析（签名保鲜）、自动合流 / 转码、Cookies（文件 / 浏览器）、代理、超时重试、暂停与停止 | 完成 |
| 媒体来源落库（`media.source_kind`）与 Excel 导出列，区分静态嗅探 / 平台适配器 / yt-dlp | 完成 |
| 命令行环境自检：`focus-crawler doctor` 检查 yt-dlp / ffmpeg / playwright | 完成 |
| ffmpeg 自动探测：配置路径 → `%APPDATA%/FocusCrawler/tools` → PATH → Scoop / Chocolatey → imageio-ffmpeg → Playwright，探测 concat / mp4 能力，裁剪版自动改走二进制拼接 + 重封装 | 完成 |
| 设置页显示 ffmpeg / yt-dlp 实际状态，支持「自动检测」一键填入 | 完成 |
| 分段并发下载引擎（借鉴 Mister Download Manager / IDM 的动态分段规划）：多连接、动态拆分、断点续传、限速、暂停停止 | 完成 |
| 自适应选择器（借鉴 Scrapling 的 adaptive relocation）：页面改版后按元素指纹重新定位字段并回写 | 完成 |
| 终端风界面主题：深黑底 + 荧光绿 + 等宽字体 + HUD 状态条，覆盖主窗口 / 表格 / 对话框 / 滚动条 | 完成 |
| PyInstaller `--onedir` 打包：`scripts/build.ps1`、GUI 入口、测试与 exe 冒烟自检 | 完成 |

## 安装

```powershell
python -m pip install -e ".[dev,gui]"

# 可选：yt-dlp 通用解析（任意站点视频 / 音频）
python -m pip install -e ".[ytdlp]"

# 可选：子页动态媒体嗅探（render_for_media）
python -m pip install -e ".[dynamic]"
playwright install chromium
```

```powershell
# 可选：内置一个完整 ffmpeg（无需另外安装，合流 / 合并开箱可用）
python -m pip install imageio-ffmpeg
```

yt-dlp、Playwright、imageio-ffmpeg 都是可选依赖：未安装时对应能力自动降级（记录一条警告，不影响其它抓取）。

ffmpeg 查找顺序（任选其一即可）：设置里的路径 → `%APPDATA%/FocusCrawler/tools/ffmpeg.exe` → PATH →
Scoop / Chocolatey / `C:\ffmpeg` → imageio-ffmpeg 包 → Playwright 内置。找错的构建会被自动跳过：
没有 concat 解复用器时改走「二进制拼接 + `-c copy` 重封装」。

## 使用（图形界面）

```powershell
python -m focus_crawler.gui
# 或安装后
focus-crawler-gui
```

界面流程：新建/导入任务 → 编辑配置 → 规则预览 → 开始运行（可暂停/停止）→ 查看监控与日志 → 导出 Excel。
媒体下载默认只发现不下载，需在运行前勾选“已确认媒体版权与合规用途”。

## 使用（命令行）

```powershell
# 预览：抓取入口页、发现子页，统计字段命中率与媒体数量
python -m focus_crawler preview adapters/example.com/config.json

# 运行任务并导出 Excel
python -m focus_crawler run adapters/example.com/config.json --db data/test.db --export out.xlsx

# 环境自检：yt-dlp / ffmpeg / playwright 是否就绪
python -m focus_crawler doctor
```

数据目录默认 `%APPDATA%/FocusCrawler/`，可用环境变量 `FOCUSCRAWLER_HOME` 覆盖（测试与便携模式使用）。

## 运行测试

```powershell
python -m pytest
```

## 打包（Windows）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

脚本会安装依赖、运行测试，用 PyInstaller `--onedir` 生成 `dist/FocusCrawler/FocusCrawler.exe`，并以冒烟模式启动一次 exe 验证。Playwright 浏览器与 ffmpeg 为外部依赖，不随包分发。

## yt-dlp 通用解析（可选）

内置适配器只覆盖 B 站 / 网易云等少数站点；启用 yt-dlp 后，任意被 yt-dlp 支持的站点
（YouTube、Bilibili、Vimeo、Twitter/X、Twitch、各类新闻与短视频站点等 1750+ 站点）
都能解析出视频 / 音频候选，并走同一套入库、预览、批量下载与 Excel 导出流程。

```json
{
  "video": {
    "enabled": true,
    "adapter": {
      "bilibili": true,
      "ytdlp": {
        "mode": "auto",
        "quality": "highest",
        "format": "",
        "cookies_file": "",
        "cookies_from_browser": "",
        "proxy": ""
      }
    }
  },
  "audio": {
    "enabled": true,
    "adapter": {
      "ytdlp": { "mode": "auto", "quality": "highest" }
    }
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `mode` | `off` 关闭；`auto` 仅补漏（本页没嗅到媒体、且站点被 yt-dlp 支持时才解析，默认）；`force` 本页没候选项就对任意站点尝试（含通用提取器，会多一次请求） |
| `quality` | `highest` / `best` / `worst` / `2160p` / `1440p` / `1080p` / `720p` / `480p` / `360p` |
| `format` | 自定义 yt-dlp 格式表达式（如 `bv*[height<=1080]+ba/b`），非空时优先于 `quality` |
| `cookies_file` | Netscape 格式 cookies.txt 路径（登录态 / 会员可见内容） |
| `cookies_from_browser` | 直接从浏览器读取 Cookies：`chrome` / `edge` / `firefox` 等 |
| `proxy` | 代理地址，如 `http://127.0.0.1:7890` |
| `user_agent` | 自定义 UA（默认沿用 yt-dlp 内置 UA） |
| `socket_timeout` / `retries` | 网络超时与重试次数 |
| `playlist` / `max_entries` | 是否展开播放列表，以及单页最多取多少条 |

工作方式：

1. **发现**：平台适配器（B 站等）优先；没有结果时按 `mode` 调用 yt-dlp 解析，
   把每个条目折算成一条媒体记录，`format_id`、标题、清晰度、时长、码率一并入库。
2. **下载**：`source_kind = ytdlp` 的记录在下载前会用**页面地址**重新解析一次，
   由 yt-dlp 完成签名刷新、分片下载、音视频合流（用同一套 ffmpeg 探测结果，装好即用）与可选转码；
   yt-dlp 失败时自动回退到记录里的直链地址。
3. **降级**：未安装 yt-dlp、页面不是媒体页、站点需要登录等情况只记一条警告，
   不写入失败表，也不阻塞其它子页。

> 合规提醒：yt-dlp 只下载「页面本身可访问」的原始文件；不提供 DRM 解密、
> 会员内容绕过或签名逆向。请自行确认版权与站点条款。

## 分段下载引擎

直链媒体走 :mod:`focus_crawler.core.segmented_downloader`，规则参考 IDM 公开文档与
Mister Download Manager（GPL-3.0，**仅借鉴算法，代码为独立实现**）：

1. 先 ``Range: bytes=0-0`` 探测服务器是否支持分段与总大小；
2. 支持则按连接数切分文件，空闲连接优先领取未分配段；
3. 否则抢占「刚完成段的下一个、尚未开始接收」的段；
4. 再否则把最大的剩余段对半拆开（每半不小于 ``min_split``）；
5. 每段独立文件句柄按偏移写入，互不加锁；``.part`` + ``.fcdl`` 支持断点续传；
6. 全局限速令牌桶、进度回调、暂停 / 停止；
7. 服务器忽略 Range（返回 200）或文件小于 2 MiB 时自动回退单连接流式下载。

## 自适应选择器

字段解析默认按 CSS / XPath 取；开启 ``adaptive`` 后，选择器失效时按「元素指纹」
（标签、文本、属性、DOM 路径、父节点特征）在页面上重新定位，并把新指纹回写 SQLite，
下次继续可用。算法思路参考 Scrapling（BSD-3-Clause）的 relocate / similarity score。

```json
"detail": {
  "fields": {
    "title":  { "selector": "div.title-box > h1", "adaptive": true },
    "author": { "selector": ".meta .author", "adaptive": true, "adaptive_threshold": 40 }
  }
}
```

任务编辑器「字段与解析」页的字段表最后一列即「自适应」勾选框；
``focus-crawler doctor`` 之外，指纹保存在数据库 ``adaptive_fingerprints`` 表，可随时清空重学。

## 界面主题

``gui/theme.py`` 提供终端风 QSS（深黑 ``#040705`` + 荧光绿 ``#00FF41`` + 等宽字体），
``gui/hud.py`` 是顶部自绘 HUD（品牌、运行状态灯、任务计数、时钟）。主题在
``gui/app.py`` 启动时一次性应用，覆盖主窗口、表格、对话框、滚动条与进度条。

主视觉由美术模型生成，见 ``docs/art/``。

## 架构约定

- 第一层：纯 JSON 配置（选择器 / JSONPath），覆盖静态页面。
- 第二层：`parser.mode = custom|hybrid` + 本地脚本，脚本只做解析，不发起网络请求。
- 第三层：登录、验证码、JS 签名、强反爬 —— 主动放弃；站点已被 yt-dlp 覆盖时交给 yt-dlp（可带 Cookies）。
- 子页媒体采集默认 `media_mode = discover_only`，不自动下载；`auto_download` 仍需版权确认。

## 已知限制

- 音频直链暂未实现 HTTP Range 断点续传；HLS 音频合并依赖 ffmpeg。
- 音频时长 / 码率优先采用适配器返回值，尚未读取本地音频标签（mutagen）。
- 不提供商业音乐平台的解密、签名逆向或会员内容绕过；仅下载页面本身可访问的原始文件。
- 动态媒体嗅探逐子页串行执行（并发 1）；未安装 playwright 时自动回退静态抓取。
- yt-dlp 解析每个子页最多额外一次请求；`auto` 模式只在站点被 yt-dlp 支持时才触发。
- yt-dlp 合流音视频时依赖 ffmpeg；程序会自动探测（含打包内置的 imageio-ffmpeg），都找不到时只能拿单一格式。
- 分段下载依赖服务器支持 Range；不支持时自动回退单连接，速度受限于单连接带宽。
- 自适应定位是「尽力而为」：页面结构大改（例如整站换框架）仍可能需要人工更新选择器。
- 打包产物约 330MB（含 Playwright 与 imageio-ffmpeg）；不装这两个可选依赖可显著减小体积。

## 假设（信息不足时的默认决策）

1. 任务配置中的 `detail` 字段直接映射为 `{"字段名": {...}}`，不额外嵌套 `fields`。
2. 媒体去重键为 `sha256(media_url)`；同一媒体被多个子页引用时保留首次来源页，其余来源不重复落库。
3. `urls.status` 使用 `pending / fetching / done / failed`；`page_type` 使用 `seed / discovered / list / detail`。
4. 未提供 `ffmpeg`、`playwright` 的能力不阻塞基础流程，相关功能返回明确错误或跳过。
5. GUI 与打包为独立里程碑，核心逻辑全部通过 CLI 与 pytest 验证后再接入。

## 第三方致谢

| 项目 | 许可 | 本项目如何使用 |
| --- | --- | --- |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | 可选依赖，直接调用其解析 / 下载能力 |
| [Mister Download Manager](https://github.com/anefzaoui/misterdownloadmanager) | GPL-3.0 | 仅借鉴其动态分段规划算法（IDM 规则），未复制代码 |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | BSD-3-Clause | 借鉴自适应元素定位思路，独立实现于 `core/adaptive.py` |
