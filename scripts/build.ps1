# FocusCrawler 打包脚本（PyInstaller --onedir，GUI 桌面版）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/build.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

Write-Host "== 1. 检查依赖 =="
& $Python -c "import PySide6, httpx, parsel, openpyxl, pytest; print('依赖已就绪')"
if ($LASTEXITCODE -ne 0) {
  Write-Host "缺少运行依赖，正在安装 ..."
  & $Python -m pip install --disable-pip-version-check -e ".[dev,gui]"
  if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}
& $Python -m pip install --disable-pip-version-check pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }

Write-Host "== 2. 运行测试 =="
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "测试未通过，终止打包" }

Write-Host "== 3. PyInstaller 打包 =="
$PyArgs = @(
  "--noconfirm", "--clean", "--windowed", "--onedir",
  "--name", "FocusCrawler", "--paths", "src"
)
& $Python -c "import playwright" 2>$null
if ($LASTEXITCODE -eq 0) {
  Write-Host "检测到 playwright，打包动态嗅探依赖"
  $PyArgs += @("--collect-all", "playwright")
}
& $Python -c "import imageio_ffmpeg" 2>$null
if ($LASTEXITCODE -eq 0) {
  # imageio-ffmpeg 自带一个完整 ffmpeg，打包后无需用户另装（约 80MB）
  Write-Host "检测到 imageio-ffmpeg，打包内置 ffmpeg"
  $PyArgs += @("--collect-binaries", "imageio_ffmpeg")
}
& $Python -c "import yt_dlp" 2>$null
if ($LASTEXITCODE -eq 0) {
  Write-Host "检测到 yt-dlp，打包通用解析依赖（体积约 30MB）"
  $PyArgs += @("--collect-all", "yt_dlp")
} else {
  Write-Host '未安装 yt-dlp，打包产物不含通用解析能力（可选：pip install -e ".[ytdlp]"）'
}
& $Python -m PyInstaller @PyArgs scripts/entry_gui.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

Write-Host "== 4. 验证 exe 启动 =="
$Exe = Join-Path $Root "dist\FocusCrawler\FocusCrawler.exe"
if (-not (Test-Path $Exe)) { throw "未找到打包产物：$Exe" }
# 非交互环境用 offscreen 平台与独立数据目录，避免无窗口会话或沙箱限制
$SmokeHome = Join-Path $Root ".smoke-home"
$env:FOCUSCRAWLER_HOME = $SmokeHome
$env:FOCUSCRAWLER_SMOKE_TEST = "1"
$env:QT_QPA_PLATFORM = "offscreen"
# 打包目录刚生成时可能被杀毒软件短暂占用，失败时重试一次
$Code = -1
try {
  for ($Attempt = 1; $Attempt -le 2; $Attempt++) {
    $Process = Start-Process -FilePath $Exe -PassThru
    if (-not $Process.WaitForExit(30000)) {
      Stop-Process -Id $Process.Id -Force
      Write-Host "第 $Attempt 次启动自检超时"
      Start-Sleep -Seconds 3
      continue
    }
    $Code = $Process.ExitCode
    if ($Code -eq 0) { break }
    Write-Host "第 $Attempt 次启动自检失败，退出码 $Code"
    Start-Sleep -Seconds 3
  }
} finally {
  Remove-Item Env:FOCUSCRAWLER_HOME -ErrorAction SilentlyContinue
  Remove-Item Env:FOCUSCRAWLER_SMOKE_TEST -ErrorAction SilentlyContinue
  Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
  Remove-Item $SmokeHome -Recurse -Force -ErrorAction SilentlyContinue
}
if ($Code -ne 0) { throw "exe 启动自检失败，退出码 $Code" }

Write-Host "== 完成 =="
Write-Host "输出目录: dist/FocusCrawler/"
Write-Host "运行时数据目录: %APPDATA%/FocusCrawler/"
Write-Host "提示: Playwright 浏览器与 ffmpeg 为外部依赖，需单独安装并在设置中指定路径。"
