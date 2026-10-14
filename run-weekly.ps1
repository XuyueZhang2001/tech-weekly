# 本地手动跑一次科技周报（不需要建虚拟环境：本项目是纯标准库实现）
#
# 用法：
#   .\run-weekly.ps1                 # 按 config.json 直接发送
#   .\run-weekly.ps1 -Mode preview   # 只在 out\ 生成预览，不发信
#   .\run-weekly.ps1 -Mode test      # 只体检信源
#   .\run-weekly.ps1 -Mode run       # 先试发给 TEST_RECIPIENT，成功后再发全体
#
# 凭据可以写在 config.json 里，也可以用环境变量覆盖（推荐后者，避免密码进版本库）：
#   setx SMTP_USER "你的邮箱@qq.com"
#   setx SMTP_PASSWORD "你的SMTP授权码"
#   setx MAIL_TO "收件人1@example.com,收件人2@example.com"

[CmdletBinding()]
param(
  [ValidateSet('run', 'send', 'preview', 'test')]
  [string]$Mode = 'send',
  [int]$Days = 0
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

# 找一个可用的 Python：先看环境变量，再看 PATH，最后扫常见安装目录
$candidates = @()
if ($env:TECH_WEEKLY_PYTHON) { $candidates += $env:TECH_WEEKLY_PYTHON }
foreach ($name in @('python', 'python3', 'py')) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) { $candidates += $cmd.Source }
}
$globs = @(
  (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe'),
  (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\python.exe'),
  'C:\Python3*\python.exe',
  "$env:ProgramFiles\Python3*\python.exe"
)
foreach ($g in $globs) {
  $candidates += (Get-Item $g -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
}
$python = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $python) {
  throw '没找到 Python。请安装 Python 3.9+（安装时勾选 Add to PATH），或先把 Python 路径写进环境变量 TECH_WEEKLY_PYTHON 再运行。'
}
Write-Host "使用 Python: $python" -ForegroundColor DarkGray

# 时间窗口：未指定则用 config.json 里的值
if ($Days -le 0) {
  $Days = [int](Get-Content (Join-Path $root 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json).report.window_days
}

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logFile = Join-Path $logDir "run-$stamp.log"

Write-Host "模式=$Mode 窗口=$Days 天，日志=$logFile" -ForegroundColor Cyan
$pyArgs = @((Join-Path $root 'main.py'), $Mode, '--days', $Days)
& $python @pyArgs 2>&1 | Tee-Object -FilePath $logFile
$code = $LASTEXITCODE

if ($code -eq 0) {
  Write-Host "完成，退出码 0" -ForegroundColor Green
} else {
  Write-Host "失败，退出码 $code（日志见 $logFile）" -ForegroundColor Red
}
exit $code
