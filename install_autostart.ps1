# Install auto-start shortcut:
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $scriptDir 'deepseek_balance_widget.py'
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'DeepSeek-Balance-Widget.lnk'

$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command python.exe -ErrorAction Stop).Source }

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $pyw
$lnk.Arguments = '"' + $script + '"'
$lnk.WorkingDirectory = $scriptDir
$lnk.Description = 'DeepSeek balance widget'
$lnk.Save()

Write-Host ("Auto-start shortcut created: " + $lnkPath)
